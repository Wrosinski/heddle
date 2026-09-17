#!/usr/bin/env python3
"""Rebuild, install, and qualify a manifest-pinned Heddle supervisor."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heddle.pinned-supervisor/v1"
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")


class SupervisorError(RuntimeError):
    """A bounded adoption failure with an operator-readable explanation."""


def _fail(message: str) -> NoReturn:
    raise SupervisorError(message)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(f"{label} must be a string-keyed mapping")
    return value


def _closed(value: Mapping[str, Any], label: str, keys: set[str]) -> None:
    observed = set(value)
    if observed != keys:
        _fail(
            f"{label} keys differ: missing={sorted(keys - observed)!r}, "
            f"unknown={sorted(observed - keys)!r}"
        )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        _fail(f"cannot read supervisor manifest {path}: {error}")
    manifest = _mapping(value, "manifest")
    _closed(
        manifest,
        "manifest",
        {"schema", "name", "source", "build", "artifact", "runtime", "qualification"},
    )
    if manifest["schema"] != SCHEMA:
        _fail(f"unsupported supervisor schema: {manifest['schema']!r}")

    source = _mapping(manifest["source"], "source")
    build = _mapping(manifest["build"], "build")
    artifact = _mapping(manifest["artifact"], "artifact")
    runtime = _mapping(manifest["runtime"], "runtime")
    qualification = _mapping(manifest["qualification"], "qualification")
    _closed(source, "source", {"git_revision", "archive_sha256", "source_date_epoch"})
    _closed(build, "build", {"tool", "version", "arguments"})
    _closed(artifact, "artifact", {"filename", "sha256"})
    _closed(runtime, "runtime", {"python_version", "distributions"})
    _closed(
        qualification,
        "qualification",
        {"help_arguments", "require_external_cwd"},
    )

    strings = {
        "name": manifest["name"],
        "source.git_revision": source["git_revision"],
        "source.archive_sha256": source["archive_sha256"],
        "build.tool": build["tool"],
        "build.version": build["version"],
        "artifact.filename": artifact["filename"],
        "artifact.sha256": artifact["sha256"],
        "runtime.python_version": runtime["python_version"],
    }
    if not all(isinstance(value, str) and value for value in strings.values()):
        _fail("manifest identity fields must be non-empty strings")
    if not HEX40.fullmatch(source["git_revision"]):
        _fail("source.git_revision must be a full lowercase Git object id")
    for label in ("source.archive_sha256", "artifact.sha256"):
        if not HEX64.fullmatch(strings[label]):
            _fail(f"{label} must be a lowercase SHA-256 digest")
    if not isinstance(source["source_date_epoch"], int) or isinstance(
        source["source_date_epoch"], bool
    ):
        _fail("source.source_date_epoch must be an integer")
    for label, values in (
        ("build.arguments", build["arguments"]),
        ("qualification.help_arguments", qualification["help_arguments"]),
    ):
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(item, str) and item for item in values)
        ):
            _fail(f"{label} must be a non-empty list of strings")
    distributions = _mapping(runtime["distributions"], "runtime.distributions")
    if not distributions or not all(
        isinstance(version, str) and version for version in distributions.values()
    ):
        _fail("runtime.distributions must pin non-empty version strings")
    if qualification["require_external_cwd"] is not True:
        _fail("qualification.require_external_cwd must be true")
    return manifest


def _sha256(value: bytes | Path) -> str:
    data = value if isinstance(value, bytes) else value.read_bytes()
    return hashlib.sha256(data).hexdigest()


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        _fail(
            f"command failed ({result.returncode}): {list(arguments)!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _source_archive(manifest: Mapping[str, Any]) -> bytes:
    revision = manifest["source"]["git_revision"]
    result = subprocess.run(
        ["git", "-C", str(ROOT), "archive", revision],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        _fail(
            f"git archive failed ({result.returncode}) for {revision}: "
            + result.stderr.decode(errors="replace")
        )
    actual = _sha256(result.stdout)
    expected = manifest["source"]["archive_sha256"]
    if actual != expected:
        _fail(f"source archive hash mismatch: expected {expected}, observed {actual}")
    return result.stdout


def _check_tool(manifest: Mapping[str, Any]) -> str:
    build = manifest["build"]
    result = _run([build["tool"], "--version"], cwd=ROOT)
    match = re.search(r"(\d+\.\d+\.\d+)", result.stdout + result.stderr)
    actual = match.group(1) if match else "unknown"
    if actual != build["version"]:
        _fail(
            "build tool version mismatch: "
            f"expected {build['version']}, observed {actual}"
        )
    return actual


def _check_python(manifest: Mapping[str, Any]) -> None:
    actual = ".".join(str(part) for part in sys.version_info[:3])
    expected = manifest["runtime"]["python_version"]
    if actual != expected:
        _fail(f"Python version mismatch: expected {expected}, observed {actual}")


def _copy_distribution(name: str, version: str, target_site: Path) -> None:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        _fail(f"required local distribution is missing: {name}=={version}")
    if distribution.version != version:
        _fail(
            f"distribution version mismatch for {name}: "
            f"expected {version}, observed {distribution.version}"
        )
    source_root = Path(str(distribution.locate_file(""))).resolve()
    target_root = target_site.resolve()
    copied = 0
    for relative in distribution.files or ():
        source = (source_root / relative).resolve()
        if not source.is_file() or not source.is_relative_to(source_root):
            continue
        target = (target_site / relative).resolve()
        if not target.is_relative_to(target_root):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    if not copied:
        _fail(f"required local distribution has no copyable files: {name}=={version}")


def _build_wheel(manifest: Mapping[str, Any], archive: bytes, workspace: Path) -> Path:
    source = workspace / "source"
    source.mkdir()
    archive_path = workspace / "source.tar"
    archive_path.write_bytes(archive)
    with tarfile.open(archive_path) as bundle:
        bundle.extractall(source, filter="data")
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(manifest["source"]["source_date_epoch"])
    env["PIP_NO_INDEX"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    build = manifest["build"]
    _run([build["tool"], *build["arguments"]], cwd=source, env=env)
    wheel: Path = source / "dist" / manifest["artifact"]["filename"]
    if not wheel.is_file():
        _fail(f"build did not produce the declared wheel: {wheel}")
    return wheel


def _verify_wheel(manifest: Mapping[str, Any], wheel: Path) -> str:
    if not wheel.is_file():
        _fail(f"wheel is missing: {wheel}")
    actual = _sha256(wheel)
    expected = manifest["artifact"]["sha256"]
    if actual != expected:
        _fail(f"wheel hash mismatch: expected {expected}, observed {actual}")
    return actual


def _clean_env(venv: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    env.update(
        {
            "PATH": f"{venv / 'bin'}:/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        }
    )
    return env


def _install_and_qualify(
    manifest: Mapping[str, Any], wheel: Path, destination: Path
) -> dict[str, Any]:
    venv = destination / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], cwd=destination)
    python = venv / "bin/python"
    command = venv / "bin/heddle"
    site_result = _run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=destination,
    )
    site_packages = Path(site_result.stdout.strip())
    for name, version in manifest["runtime"]["distributions"].items():
        _copy_distribution(name, version, site_packages)
    _run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        cwd=destination,
    )
    env = _clean_env(venv)
    help_result = _run(
        [str(command), *manifest["qualification"]["help_arguments"]],
        cwd=destination,
        env=env,
    )
    try:
        help_payload = json.loads(help_result.stdout)
    except json.JSONDecodeError as error:
        _fail(f"qualified help did not emit JSON: {error}: {help_result.stdout!r}")
    if not isinstance(help_payload, dict) or help_payload.get("ok") is not True:
        _fail("qualified help did not emit a successful Heddle envelope")
    probe = _run(
        [
            str(python),
            "-c",
            (
                "import json, pathlib, sys, heddle; "
                "print(json.dumps({'package': "
                "str(pathlib.Path(heddle.__file__).resolve()), "
                "'executable': str(pathlib.Path(sys.executable).absolute())}))"
            ),
        ],
        cwd=destination,
        env=env,
    )
    origins = json.loads(probe.stdout)
    expected_root = venv.resolve()
    package_origin = Path(origins["package"])
    executable = Path(origins["executable"])
    if not package_origin.is_relative_to(expected_root):
        _fail(f"qualified package escaped the supervisor environment: {package_origin}")
    if not executable.is_relative_to(venv.absolute()) or not executable.is_file():
        _fail(f"qualified executable escaped the supervisor environment: {executable}")
    return {
        "command": str(command.resolve()),
        "python": origins["executable"],
        "package_origin": origins["package"],
        "help_schema": help_payload.get("schema_version"),
    }


def _external_destination(path: Path) -> Path:
    destination = path.resolve()
    if destination.is_relative_to(ROOT.resolve()):
        _fail(f"destination must be outside the source checkout: {destination}")
    if destination.exists():
        _fail(f"destination already exists: {destination}")
    return destination


def execute(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    _check_python(manifest)
    tool_version = _check_tool(manifest)
    archive = _source_archive(manifest)
    wheel_argument = args.wheel.resolve() if args.wheel else None
    if wheel_argument is not None:
        wheel_sha256 = _verify_wheel(manifest, wheel_argument)
    else:
        wheel_sha256 = manifest["artifact"]["sha256"]
    checked = {
        "schema": "heddle.pinned-supervisor-receipt/v1",
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "source_revision": manifest["source"]["git_revision"],
        "source_archive_sha256": manifest["source"]["archive_sha256"],
        "wheel_sha256": wheel_sha256,
        "build_tool": {"name": manifest["build"]["tool"], "version": tool_version},
        "status": "checked" if args.check else "qualified",
    }
    if args.check:
        return checked
    if args.destination is None:
        _fail("--destination is required unless --check is used")
    destination = _external_destination(args.destination)
    created = False
    try:
        destination.mkdir(parents=True)
        created = True
        with tempfile.TemporaryDirectory(
            prefix=".heddle-pinned-build-", dir=destination.parent
        ) as raw_workspace:
            workspace = Path(raw_workspace)
            wheel = (
                wheel_argument
                if wheel_argument is not None
                else _build_wheel(manifest, archive, workspace)
            )
            _verify_wheel(manifest, wheel)
            dist = destination / "dist"
            dist.mkdir()
            retained_wheel = dist / manifest["artifact"]["filename"]
            shutil.copy2(wheel, retained_wheel)
            checked["wheel"] = str(retained_wheel.resolve())
            checked["qualification"] = _install_and_qualify(
                manifest, retained_wheel, destination
            )
        receipt = destination / "qualification.json"
        checked["receipt"] = str(receipt.resolve())
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(checked, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, receipt)
        return checked
    except BaseException:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    try:
        result = execute(_parser().parse_args())
    except SupervisorError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
