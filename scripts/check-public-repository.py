#!/usr/bin/env python3
"""Check public repository and distribution boundaries."""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import email.policy
import fnmatch
import gzip
import hashlib
import io
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
import tokenize
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any


def _fail(message: str) -> int:
    print(f"FAIL {message}")
    return 1


def _pass(message: str) -> int:
    print(f"PASS {message}")
    return 0


def _policy(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _tracked(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(item.decode() for item in result.stdout.split(b"\0") if item)


def _manifest(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _declared_manifest(root: Path, policy: dict[str, Any], boundary: str) -> Path:
    if boundary == "repository":
        relative = policy["repository"]["manifest"]
    else:
        relative = policy["artifacts"][boundary]["manifest"]
    return root / str(relative)


def _is_private(path: str, repository: dict[str, Any]) -> bool:
    return path not in repository["exceptions"] and any(
        fnmatch.fnmatchcase(path, pattern) for pattern in repository["private_globs"]
    )


def check_staged(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    repository = _policy(args.policy.resolve())["repository"]
    private = [path for path in _tracked(root) if _is_private(path, repository)]
    if private:
        return _fail(
            "workflow records must stay local; remove these paths from the index "
            "with git rm --cached, preserving their working files:\n"
            + "\n".join(private)
        )
    return _pass("index contains no local workflow records")


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True
    ).stdout


def _commit(root: Path, revision: str) -> str:
    if re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", revision) is None:
        raise ValueError(f"expected a full Git object ID, got {revision!r}")
    return (
        _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    )


def _history_violations(
    root: Path, policy: dict[str, Any], head: str, base: str | None
) -> list[str]:
    if _git(root, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise ValueError("history is shallow; fetch full history before checking")
    tip = _commit(root, head)
    exclusions = []
    if base and set(base) != {"0"}:
        exclusions.append(_commit(root, base))
    baseline = policy.get("history", {}).get("published_baseline")
    if baseline:
        exclusions.append(_commit(root, baseline))
    revisions = [tip, *(("--not", *exclusions) if exclusions else ())]
    commits = _git(root, "rev-list", *revisions).decode().splitlines()
    violations = []
    for commit in commits:
        paths = _git(
            root,
            "diff-tree",
            "--root",
            "-m",
            "--no-commit-id",
            "--no-renames",
            "--diff-filter=ACMT",
            "--name-only",
            "-z",
            "-r",
            commit,
        ).split(b"\0")
        for path in sorted(set(paths) - {b""}):
            name = path.decode("utf-8", errors="surrogateescape")
            if _is_private(name, policy["repository"]):
                violations.append(f"{commit}: {name!r}")
    return violations


def check_history(args: argparse.Namespace) -> int:
    violations = _history_violations(
        args.root.resolve(), _policy(args.policy.resolve()), args.head, args.base
    )
    if violations:
        return _fail(
            "new commits contain local workflow records, including intermediate "
            "commits; repair unpublished history before pushing:\n"
            + "\n".join(violations)
        )
    return _pass("new commit history contains no local workflow records")


def check_outgoing(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    policy = _policy(args.policy.resolve())
    violations: list[str] = []
    for line in sys.stdin:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("malformed pre-push ref update; expected four fields")
        _local_ref, head, remote_ref, base = fields
        if set(head) == {"0"}:
            continue
        if set(base) != {"0"}:
            try:
                _commit(root, base)
            except subprocess.CalledProcessError:
                base = "0" * 40
        violations.extend(
            f"{remote_ref}: {row}"
            for row in _history_violations(root, policy, head, base)
        )
    if violations:
        return _fail("push contains local workflow records:\n" + "\n".join(violations))
    return _pass("all outgoing refs preserve the local-records boundary")


def check_repository(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    policy = _policy(args.policy.resolve())
    repository = policy["repository"]
    tracked = _tracked(root)
    private = [path for path in tracked if _is_private(path, repository)]
    if private:
        return _fail("tracked private-record category members:\n" + "\n".join(private))

    manifest_path = args.manifest
    if manifest_path is None:
        manifest_path = _declared_manifest(root, policy, "repository")
    members = _manifest(manifest_path.resolve())
    if members != sorted(set(members)):
        return _fail("repository manifest must be sorted and unique")
    extra = sorted(set(members) - set(tracked))
    missing = sorted(set(tracked) - set(members))
    if extra or missing:
        details: list[str] = []
        if extra:
            details.append("extra manifest members:\n" + "\n".join(extra))
        if missing:
            details.append("missing manifest members:\n" + "\n".join(missing))
        return _fail("\n".join(details))
    return _pass("repository index matches the reviewed manifest")


def _text_members(
    root: Path, policy: dict[str, Any], *, tests: bool | None
) -> list[tuple[str, str]]:
    repository = policy["repository"]
    manifest = root / repository["manifest"]
    public = set(_manifest(manifest)) if manifest.is_file() else set(_tracked(root))
    result: list[tuple[str, str]] = []
    for relative in _tracked(root):
        if relative not in public or _is_private(relative, repository):
            continue
        is_test = relative == "tests" or relative.startswith("tests/")
        if tests is not None and is_test != tests:
            continue
        if relative == "scripts/check-public-repository.py":
            # The observer necessarily contains its own neutral sentinel text.
            continue
        if tests is True and relative == "tests/guardrails/test_public_repository.py":
            # This test module contains the policy's executable negative inputs.
            continue
        try:
            content = (root / relative).read_bytes()
        except OSError:
            continue
        if b"\0" in content:
            continue
        try:
            result.append((relative, content.decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return result


_CONCRETE_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"(?:docs/(?:features|proposals|gate-trajectories)|plans)/"
    r"[A-Za-z0-9_.@/-]+\.(?:md|ya?ml|jsonl?|log|txt|tar\.gz)"
)
_MARKDOWN_LINK = re.compile(r"\]\(([^) #]+)(?:#[^)]*)?\)")
_PRIVATE_REVIEW_ID = re.compile(
    r"\b(?:SR|XF|XS|CQ|TS|RA|INV|SY|RR)-[A-Z0-9][A-Z0-9./-]*\b",
    re.IGNORECASE,
)
# An owner-decision label is a number, date, short code (D12, CP-4) or single
# capital letter; prose such as "owner ruling because" is public vocabulary.
_PRIVATE_DECISION_LABEL = re.compile(
    r"\b(?:owner[- ]rul(?:e|ed|ing)\s+"
    r"(?:[A-Za-z]{0,2}-?\d[A-Za-z0-9-]*|(?-i:[A-Z]))|"
    r"checkpoint\s+\d+\s+[DR]\d+|m\d+[a-z]?\s+dogfood|"
    r"M\d+[A-Z]?\s+(?:host-tooling|[^\n()]{0,30}?\bincrement\b|"
    r"wave(?:-|\s)|pm\d))\b",
    re.IGNORECASE,
)
_PRIVATE_MILESTONE_LABEL = re.compile(r"\bM\d+[A-Z]?\b")
_PRIVATE_DEVELOPMENT_ID = re.compile(
    r"\b(?:D-?\d{1,3}[A-Za-z]?|REQ-?\d+|OD-\d+|"
    r"H(?:[4-9]|[1-9]\d)|M\d+[A-Z]?|pm\d+|"
    r"AD-\d+|CP-?\d+(?:-[A-Z])?|PL-[A-Z0-9]+|PR-\d+|"
    r"R(?:-P)?\d+(?:-\d+)?|P\d+-\d+)\b"
)
_BARE_MARKDOWN_REFERENCE = re.compile(r"(?<![/A-Za-z0-9_.-])([A-Za-z0-9_-]+\.md)\b")
_ALLOWED_GENERATED_MARKDOWN_REFERENCES = {"_aggregate.md", "MEMORY.md"}
_HISTORICAL_TEST_PATH = re.compile(
    r"(?:^|/)(?:test_(?:pm\d+|m\d+[a-z]?|iar)(?:_|\.)|"
    r"test_[^/]*simplification_wave\d+|(?:m\d+[a-z]?|iar)_helpers\.py$)",
    re.IGNORECASE,
)
_HISTORICAL_TEST_MARKER = re.compile(
    r"pytest\.mark\.(?:pm\d+|m\d+[a-z]?|iar)\b", re.IGNORECASE
)
_HISTORICAL_TEST_SYMBOL = re.compile(
    r"^\s*(?:def\s+(?:test_|_)(?:pm\d+|m\d+[a-z]?|wave\d+|iar)(?:_|\b)|"
    r"class\s+Test\w*(?:PM\d+|M\d+[A-Z]?|Wave\d+|IAR)\w*)",
    re.IGNORECASE,
)
_HISTORICAL_TEST_REFERENCE = re.compile(
    r"\b(?:test_(?:pm\d+|m\d+[a-z]?|iar)_\w+|"
    r"test_\w*simplification_wave\d+\w*|(?:m\d+[a-z]?|iar)_helpers)\.py\b",
    re.IGNORECASE,
)
_HISTORICAL_TEST_CONTEXT = re.compile(
    r"(?:\bD\d{1,3}[A-Za-z]?\b|"
    r"\b(?:RA|XF)(?:-[A-Z])?[-_.]?\d[A-Za-z0-9._-]*\b|"
    r"(?<!/)\bH3\b|\bH(?:[4-9]|[12]\d)\b|"
    r"\bPhase[ _-]?(?:5|6|9)\b|"
    r"\b(?:(?:pm|PM)\d+|(?:wave|Wave)[ _-]?\d+|iar|IAR)\b)"
)
_HISTORICAL_MILESTONE_PROSE = re.compile(
    r"(?:\b(?:landed|landing|chronology|source[- ]era)\b.{0,48}\bM\d+[A-Z]?\b|"
    r"\bM\d+[A-Z]?\b.{0,48}\b(?:landed|landing|chronology|source[- ]era)\b|"
    r"\bM\d+[A-Z]?(?:/M\d+[A-Z]?)+\b)"
)
_HISTORICAL_TEST_SYMBOL_CONTEXT = re.compile(
    r"^\s*(?:def\s+(?:test_|_)(?:d\d{1,3}[a-z]?|ra\d+|xf\w*\d+|"
    r"h(?:[3-9]|[12]\d)|phase(?:5|6|9))(?:_|\b)|"
    r"class\s+Test(?:D\d+|RA\d+|XF\w*\d+|H(?:[3-9]|[12]\d)|Phase(?:5|6|9)))",
    re.IGNORECASE,
)
_HISTORICAL_INVENTORY_NODE = re.compile(
    r"(?:::Test(?:D\d+|RA\d+|XF\w*\d+|H(?:[3-9]|[12]\d)|Phase(?:5|6|9))\w*)?"
    r"::test_(?:d\d{1,3}[a-z]?|ra\d+|xf\w*\d+|h(?:[3-9]|[12]\d)|"
    r"phase(?:5|6|9))(?:_|$)",
    re.IGNORECASE,
)
_PYTEST_INVENTORY = "tests/fixtures/pytest-inventory-current.txt.gz.b64"
_SYNTHETIC_RECORD_ID = re.compile(r"(?P<quote>['\"])D\d{1,3}(?P=quote)")


def _has_historical_test_context(line: str) -> bool:
    """Ignore exact synthetic record IDs while rejecting source-era prose."""
    return bool(_HISTORICAL_TEST_CONTEXT.search(_SYNTHETIC_RECORD_ID.sub("", line)))


def _has_historical_milestone_prose(line: str) -> bool:
    """Reject milestone chronology in prose without rejecting current wire fields."""
    stripped = line.lstrip()
    if stripped.startswith(("#", '"', "'")):
        return bool(_HISTORICAL_MILESTONE_PROSE.search(line))
    if "#" in line:
        return bool(_HISTORICAL_MILESTONE_PROSE.search(line.split("#", 1)[1]))
    return False


def _python_narrative_lines(text: str) -> set[int]:
    """Return comment and docstring lines without treating data literals as prose."""
    lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                lines.add(token.start[0])
        tree = ast.parse(text)
    except (SyntaxError, tokenize.TokenError):
        return set(range(1, len(text.splitlines()) + 1))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        last_line = first.value.end_lineno or first.value.lineno
        lines.update(range(first.value.lineno, last_line + 1))
    return lines


def _narrative_lines(relative: str, text: str, *, test_corpus: bool) -> set[int]:
    if not test_corpus and relative.endswith(".py"):
        return _python_narrative_lines(text)
    if not test_corpus and relative.startswith("heddle/") and relative.endswith(".md"):
        return set(range(1, len(text.splitlines()) + 1))
    if test_corpus and (
        Path(relative).name == "README.md" or relative.endswith(".gitattributes")
    ):
        return set(range(1, len(text.splitlines()) + 1))
    return set()


def _public_members(
    root: Path, policy: dict[str, Any], tracked: list[str]
) -> tuple[list[str], set[str], set[str]]:
    manifest = root / policy["repository"]["manifest"]
    public = set(_manifest(manifest)) if manifest.is_file() else set(tracked)
    members = [
        path for path in tracked if path in public and not path.startswith("plans/")
    ]
    documents = {
        path
        for path in members
        if (
            path.endswith(".md")
            or path in {"README", "README.md", "AGENTS.md", "CLAUDE.md"}
        )
        and not path.startswith("tests/")
    }
    return members, documents, public


def check_references(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    policy = _policy(args.policy.resolve())
    tracked = _tracked(root)
    members, documents, public = _public_members(root, policy, tracked)
    excluded = sorted(
        (path for path in set(tracked) - public if "/" in path),
        key=len,
        reverse=True,
    )
    public_markdown_names = {
        Path(path).name for path in public if path.lower().endswith(".md")
    }
    generated = set(policy.get("local_records", {}).get("scaffolds", {}))
    generated_names = _ALLOWED_GENERATED_MARKDOWN_REFERENCES | {
        Path(path).name for path in generated
    }
    violations: list[str] = []
    for relative in members:
        try:
            content = (root / relative).read_bytes()
            if b"\0" in content:
                continue
            text = content.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        narrative_lines = (
            _python_narrative_lines(text) if relative.endswith(".py") else set()
        )
        for line_number, line in enumerate(text.splitlines(), 1):
            excluded_reference = any(path in line for path in excluded)
            concrete = [
                match.group(0) for match in _CONCRETE_PRIVATE_PATH.finditer(line)
            ]
            narrative_reference = (
                relative in documents or line_number in narrative_lines
            )
            bad_concrete = narrative_reference and any(
                _is_private(path, policy["repository"]) and path not in generated
                for path in concrete
            )
            dangling_bare = narrative_reference and any(
                name not in public_markdown_names and name not in generated_names
                for name in _BARE_MARKDOWN_REFERENCE.findall(line)
            )
            if relative not in documents:
                if excluded_reference or bad_concrete or dangling_bare:
                    violations.append(f"{relative}:{line_number}")
                continue
            bad_link = False
            for target in _MARKDOWN_LINK.findall(line):
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith(
                    "#"
                ):
                    continue
                resolved = (Path(relative).parent / target).as_posix()
                normalized = posixpath.normpath(resolved)
                if normalized not in public and not any(
                    path.startswith(normalized.rstrip("/") + "/") for path in public
                ):
                    bad_link = True
                    break
            if excluded_reference or bad_concrete or dangling_bare or bad_link:
                violations.append(f"{relative}:{line_number}")
    if violations:
        return _fail("private-category reference:\n" + "\n".join(violations))
    return _pass("retained files contain no private-category references")


def _provenance_violations(
    members: list[tuple[str, str]], *, test_corpus: bool
) -> list[str]:
    violations: list[str] = []
    for relative, text in members:
        texts = [text]
        if test_corpus and relative == _PYTEST_INVENTORY:
            try:
                texts = [gzip.decompress(base64.b64decode(text)).decode("utf-8")]
            except (ValueError, OSError, UnicodeDecodeError):
                violations.append(f"{relative}:1")
        if test_corpus and _HISTORICAL_TEST_PATH.search(relative):
            violations.append(f"{relative}:1")
        narrative_lines = _narrative_lines(
            relative, "\n".join(texts), test_corpus=test_corpus
        )
        for line_number, line in enumerate("\n".join(texts).splitlines(), 1):
            lowered = line.lower()
            stripped = line.lstrip()
            bad = (
                "source_feature" in lowered
                if test_corpus
                else stripped.startswith("source_feature:")
            )
            bad = bad or "source: docs/proposals/" in lowered
            bad = bad or "adapted from" in lowered or "ported from" in lowered
            if test_corpus:
                bad = bad or bool(_HISTORICAL_TEST_MARKER.search(line))
                bad = bad or _has_historical_milestone_prose(line)
                bad = bad or bool(_HISTORICAL_TEST_SYMBOL.search(line))
                bad = bad or bool(_HISTORICAL_TEST_REFERENCE.search(line))
                if relative.endswith(".py") or relative == _PYTEST_INVENTORY:
                    bad = bad or _has_historical_test_context(line)
                    bad = bad or bool(_HISTORICAL_TEST_SYMBOL_CONTEXT.search(line))
                if relative == _PYTEST_INVENTORY:
                    bad = bad or bool(_HISTORICAL_INVENTORY_NODE.search(line))
                if line_number in narrative_lines:
                    bad = bad or bool(_PRIVATE_DEVELOPMENT_ID.search(line))
                    bad = bad or bool(_PRIVATE_REVIEW_ID.search(line))
            if not test_corpus:
                bad = bad or stripped.startswith("**Source:**")
                bad = bad or stripped.startswith("Source: docs/features/")
                bad = bad or "/home/ubuntu/" in line
                # AC-* and REQ-* remain valid public contract vocabulary.
                # Internal review/finding namespaces and owner-decision labels
                # have no meaning without the private development record.
                if not relative.startswith("heddle/resources/prompts/"):
                    bad = bad or bool(_PRIVATE_REVIEW_ID.search(line))
                bad = bad or bool(_PRIVATE_DECISION_LABEL.search(line))
                if line_number in narrative_lines:
                    bad = bad or bool(_PRIVATE_DEVELOPMENT_ID.search(line))
                # Reusable patterns describe behavior, so source-era milestone
                # labels are opaque even when they are not paired with a
                # review identifier.
                if relative.startswith("docs/patterns/"):
                    bad = bad or bool(_PRIVATE_MILESTONE_LABEL.search(line))
            if bad:
                violations.append(f"{relative}:{line_number}")
    return violations


def check_surfaces(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    policy = _policy(args.policy.resolve())
    violations = _provenance_violations(
        _text_members(root, policy, tests=False), test_corpus=False
    )
    if violations:
        return _fail("public-surface provenance:\n" + "\n".join(violations))
    return _pass("public surfaces are self-contained")


def check_tests(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    policy = _policy(args.policy.resolve())
    violations = _provenance_violations(
        _text_members(root, policy, tests=True), test_corpus=True
    )
    if violations:
        return _fail("test provenance:\n" + "\n".join(violations))
    return _pass("test corpus uses current synthetic context")


def _wheel(path: Path) -> tuple[dict[str, tuple[int, bytes]], list[str]]:
    members: dict[str, tuple[int, bytes]] = {}
    duplicates: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.filename in members:
                duplicates.add(info.filename)
                continue
            members[info.filename] = (
                info.external_attr >> 16,
                archive.read(info),
            )
    return members, sorted(duplicates)


def _record_valid(members: dict[str, tuple[int, bytes]]) -> bool:
    record_names = [name for name in members if name.endswith(".dist-info/RECORD")]
    if len(record_names) != 1:
        return False
    record_name = record_names[0]
    try:
        rows = list(csv.reader(io.StringIO(members[record_name][1].decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error):
        return False
    seen: set[str] = set()
    for row in rows:
        if len(row) != 3:
            return False
        name, encoded_hash, size = row
        if name in seen or name not in members:
            return False
        seen.add(name)
        if name == record_name:
            if encoded_hash or size:
                return False
            continue
        content = members[name][1]
        expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(
            b"="
        )
        if encoded_hash != f"sha256={expected.decode()}" or size != str(len(content)):
            return False
    return seen == set(members)


def compare_wheel(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    policy = _policy(args.policy.resolve())
    expected_members = _manifest(_declared_manifest(root, policy, "wheel"))
    left, left_duplicates = _wheel(args.left.resolve())
    right, right_duplicates = _wheel(args.right.resolve())
    duplicates = sorted(set(left_duplicates) | set(right_duplicates))
    if duplicates:
        return _fail("wheel duplicate member: " + ", ".join(duplicates))
    for label, members in (("left", left), ("right", right)):
        if sorted(members) != expected_members:
            return _fail(f"{label} wheel member manifest mismatch")
    if not _record_valid(left) or not _record_valid(right):
        return _fail("wheel RECORD mismatch")
    if set(left) != set(right):
        return _fail("wheel member mismatch")
    for name in sorted(left):
        if left[name][0] != right[name][0]:
            return _fail(f"wheel mode mismatch: {name}")
    record_name = next(name for name in left if name.endswith(".dist-info/RECORD"))
    for name in sorted(left):
        if name == record_name:
            continue
        if left[name][1] != right[name][1]:
            category = "metadata" if ".dist-info/" in name else "bytes"
            return _fail(f"wheel {category} mismatch: {name}")
    left_record = sorted(csv.reader(io.StringIO(left[record_name][1].decode("utf-8"))))
    right_record = sorted(
        csv.reader(io.StringIO(right[record_name][1].decode("utf-8")))
    )
    if left_record != right_record:
        return _fail("wheel RECORD mismatch")
    return _pass("wheels are semantically equivalent")


def _metadata_values(content: bytes) -> dict[str, list[str]]:
    message = BytesParser(policy=email.policy.default).parsebytes(content)
    fields = (
        "Name",
        "Version",
        "Summary",
        "Requires-Python",
        "License",
        "Author-email",
        "Project-URL",
    )
    return {field: message.get_all(field, []) for field in fields}


def _expected_metadata(source: Path) -> dict[str, list[str]]:
    project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
    authors = project.get("authors", [])
    author_email = [
        ", ".join(
            f"{author['name']} <{author['email']}>"
            for author in authors
            if "name" in author and "email" in author
        )
    ]
    license_value = project.get("license")
    license_text = (
        license_value.get("text") if isinstance(license_value, dict) else license_value
    )
    return {
        "Name": [project["name"]],
        "Version": [project["version"]],
        "Summary": [project["description"]],
        "Requires-Python": [project["requires-python"]],
        "License": [license_text],
        "Author-email": author_email,
        "Project-URL": [
            f"{label}, {url}" for label, url in project.get("urls", {}).items()
        ],
    }


def _wheel_metadata(path: Path) -> bytes | None:
    with zipfile.ZipFile(path) as archive:
        names = [
            info.filename
            for info in archive.infolist()
            if not info.is_dir() and info.filename.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            return None
        return archive.read(names[0])


def _distribution_mode(mode: int) -> int:
    return 0o755 if mode & 0o111 else 0o644


def compare_sdist(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    policy = _policy(args.policy.resolve())
    manifest = set(_manifest(_declared_manifest(args.root.resolve(), policy, "sdist")))
    declared_generated = set(policy["artifacts"]["sdist"]["generated"])
    archive_members: dict[str, bytes] = {}
    archive_modes: dict[str, int] = {}
    generated: dict[str, bytes] = {}
    seen: set[str] = set()
    with tarfile.open(args.sdist.resolve(), "r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = Path(member.name).parts
            if len(parts) < 2:
                return _fail(f"invalid sdist member path: {member.name}")
            relative = Path(*parts[1:])
            name = relative.as_posix()
            if name in seen:
                return _fail(f"sdist duplicate member: {name}")
            seen.add(name)
            extracted = archive.extractfile(member)
            if extracted is None:
                return _fail(f"sdist source member is unreadable: {relative}")
            content = extracted.read()
            if name in declared_generated:
                generated[name] = content
                continue
            archive_members[name] = content
            archive_modes[name] = member.mode

    missing = sorted(manifest - set(archive_members))
    extra = sorted(set(archive_members) - manifest)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing sdist source members: " + ", ".join(missing))
        if extra:
            details.append("extra sdist source members: " + ", ".join(extra))
        return _fail("; ".join(details))
    missing_generated = sorted(declared_generated - set(generated))
    extra_generated = sorted(set(generated) - declared_generated)
    if missing_generated or extra_generated:
        return _fail(
            "sdist generated metadata mismatch: "
            f"missing={missing_generated}, extra={extra_generated}"
        )
    for name in sorted(manifest):
        relative = Path(name)
        source_path = source / relative
        if not source_path.is_file():
            return _fail(f"sdist source member mismatch: {relative}")
        if archive_members[name] != source_path.read_bytes():
            return _fail(f"sdist source bytes mismatch: {relative}")
        source_mode = _distribution_mode(stat.S_IMODE(source_path.stat().st_mode))
        archive_mode = _distribution_mode(archive_modes[name])
        if archive_mode != source_mode:
            return _fail(f"sdist source mode mismatch: {relative}")
    package_metadata = generated.get("PKG-INFO")
    if package_metadata is None:
        return _fail("sdist generated metadata mismatch: PKG-INFO")
    if _metadata_values(package_metadata) != _expected_metadata(source):
        return _fail("sdist generated metadata mismatch: PKG-INFO")
    if args.wheel is not None:
        wheel_metadata = _wheel_metadata(args.wheel.resolve())
        if wheel_metadata is None or wheel_metadata != package_metadata:
            return _fail("sdist and wheel metadata mismatch")
    return _pass("sdist source members match the source tree")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=root)
    common.add_argument(
        "--policy", type=Path, default=root / "release/public-repository.toml"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    repository = subparsers.add_parser("repository", parents=[common])
    repository.add_argument("--manifest", type=Path)
    repository.set_defaults(run=check_repository)
    subparsers.add_parser("staged", parents=[common]).set_defaults(run=check_staged)
    history = subparsers.add_parser("history", parents=[common])
    history.add_argument("--base")
    history.add_argument("--head", required=True)
    history.set_defaults(run=check_history)
    outgoing = subparsers.add_parser("outgoing", parents=[common])
    outgoing.add_argument("remote_name")
    outgoing.add_argument("remote_url")
    outgoing.set_defaults(run=check_outgoing)
    for command, runner in (
        ("references", check_references),
        ("surfaces", check_surfaces),
        ("tests", check_tests),
    ):
        subparsers.add_parser(command, parents=[common]).set_defaults(run=runner)
    wheel = subparsers.add_parser("compare-wheel", parents=[common])
    wheel.add_argument("--left", type=Path, required=True)
    wheel.add_argument("--right", type=Path, required=True)
    wheel.set_defaults(run=compare_wheel)
    sdist = subparsers.add_parser("compare-sdist", parents=[common])
    sdist.add_argument("--sdist", type=Path, required=True)
    sdist.add_argument("--source", type=Path, required=True)
    sdist.add_argument("--wheel", type=Path)
    sdist.set_defaults(run=compare_sdist)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return int(args.run(args))
    except (
        OSError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
        ValueError,
    ) as error:
        return _fail(str(error))


if __name__ == "__main__":
    sys.exit(main())
