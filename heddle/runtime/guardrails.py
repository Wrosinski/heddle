"""Read-only guardrails executed by `heddle validate`."""

from __future__ import annotations

import glob
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from heddle.contracts.result import Diagnostic, Severity
from heddle.kernel.knowledge import (
    PATTERNS_ROOT,
    heading_anchor,
    read_markdown,
    read_spec_verification_facts,
    read_yaml_mapping,
    read_yaml_mapping_with_duplicates,
)
from heddle.kernel.project_config import KernelError, ProjectConfig

SPEC_DRIFT_BASELINE = Path(".heddle") / "spec-drift-baseline.yaml"
SPEC_DRIFT = "spec-drift"
SPEC_BASELINE_MISSING = "spec-baseline-missing"
PATTERN_INTEGRITY = "pattern-integrity"
ORIENTATION_DRIFT = "orientation-drift"
# Managed host-integration guardrails.
MANAGED_BLOCK_INTEGRITY = "managed-block-integrity"
MIRROR_DRIFT = "mirror-drift"
PROMPT_CONVENTIONS = "prompt-conventions"
AGENTS_DOC = Path("AGENTS.md")
FEATURES_ROOT = Path("docs") / "features"
ORIENTATION_DOC = Path("docs") / "workflow" / "orientation.md"
PATTERN_REF = re.compile(r"\[\[pattern:([A-Za-z0-9_.-]+)\]\]")
SOURCE_PATH = re.compile(r"^Source path:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def run_guardrails(root: Path, config: ProjectConfig) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    guardrails: tuple[tuple[str, Callable[[], list[Diagnostic]]], ...] = (
        (SPEC_DRIFT, lambda: _spec_drift(root, config)),
        (PATTERN_INTEGRITY, lambda: _pattern_integrity(root)),
        (ORIENTATION_DRIFT, lambda: _orientation_drift(root)),
        (MANAGED_BLOCK_INTEGRITY, lambda: _managed_block_integrity(root)),
        (MIRROR_DRIFT, lambda: _mirror_drift(root, config)),
        (PROMPT_CONVENTIONS, lambda: _prompt_conventions(root, config)),
    )
    for code, guardrail in guardrails:
        try:
            diagnostics.extend(guardrail())
        except (KernelError, OSError, UnicodeDecodeError, ValueError) as error:
            # ValueError is the backstop for a path-side
            # fault that is not a KernelError — a null byte reaching
            # Path.resolve() / glob.glob, or an unbalanced URL authority reaching
            # urlsplit. Without it such a fault would escape run_guardrails and
            # run_validate, and the dispatcher's catch-all would discard
            # every accumulated finding as `exit 1 internal`. Isolating it per
            # guardrail keeps the run at `exit 3 ledger-drift`.
            diagnostics.append(
                _guardrail_diagnostic(
                    Severity.FATAL,
                    code,
                    f"{code}: guardrail input could not be read: {error}",
                    "repair the guardrail input and rerun validate.",
                )
            )
    return diagnostics


def _guardrail_diagnostic(
    severity: Severity, code: str, message: str, fix: str
) -> Diagnostic:
    return Diagnostic(severity, code, f"{message}. Fix: {fix}")


def _spec_drift(root: Path, config: ProjectConfig) -> list[Diagnostic]:
    specs_root = root / config.layout.specs
    if not specs_root.is_dir():
        return []
    baseline_path = root / SPEC_DRIFT_BASELINE
    if not baseline_path.is_file():
        return []
    diagnostics: list[Diagnostic] = []
    baseline, duplicate_keys = read_yaml_mapping_with_duplicates(baseline_path)
    for duplicate_key in duplicate_keys:
        diagnostics.append(
            _guardrail_diagnostic(
                Severity.FATAL,
                "internal",
                f"spec-drift baseline duplicate key: {duplicate_key}",
                "remove duplicate keys from the baseline manifest.",
            )
        )
    if duplicate_keys:
        return diagnostics
    for spec_path in sorted(specs_root.rglob("*.md")):
        feature_id = spec_path.relative_to(specs_root).with_suffix("").as_posix()
        try:
            facts = read_spec_verification_facts(spec_path)
        except (KernelError, OSError, UnicodeDecodeError) as error:
            # Per-document isolation makes one malformed spec a fatal finding
            # and the sweep continues — a single unreadable doc must not abort
            # the rest of the guardrail (mirrors validate's per-workspace
            # catch-and-continue; AC-9 accumulate intent).
            diagnostics.append(
                _spec_diagnostic(
                    Severity.FATAL,
                    SPEC_DRIFT,
                    feature_id,
                    f"spec could not be read: {error}",
                )
            )
            continue
        if facts.status != "complete":
            continue
        row = baseline.get(feature_id)
        if row is None:
            diagnostics.append(
                _spec_diagnostic(
                    Severity.ADVISORY,
                    SPEC_BASELINE_MISSING,
                    feature_id,
                    "complete spec has no spec-drift baseline row",
                )
            )
            continue
        if not isinstance(row, dict):
            diagnostics.append(
                _spec_diagnostic(
                    Severity.FATAL,
                    "internal",
                    feature_id,
                    "spec-drift baseline row is not a mapping",
                )
            )
            continue
        for verified_by in facts.verified_by:
            verified_path = Path(verified_by)
            target = repo_relative_path(root, verified_by)
            if target is None:
                diagnostics.append(
                    _spec_diagnostic(
                        Severity.FATAL,
                        SPEC_DRIFT,
                        feature_id,
                        f"Verified-by path escapes host layout {verified_by}",
                    )
                )
                continue
            expected = row.get(verified_path.as_posix())
            if not target.is_file():
                diagnostics.append(
                    _spec_diagnostic(
                        Severity.FATAL,
                        SPEC_DRIFT,
                        feature_id,
                        f"missing Verified-by file {verified_by}",
                    )
                )
                continue
            if not isinstance(expected, str):
                diagnostics.append(
                    _spec_diagnostic(
                        Severity.ADVISORY,
                        SPEC_BASELINE_MISSING,
                        feature_id,
                        (f"Verified-by {verified_by} has no baseline hash"),
                    )
                )
                continue
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                diagnostics.append(
                    _spec_diagnostic(
                        Severity.FATAL,
                        SPEC_DRIFT,
                        feature_id,
                        f"unreadable Verified-by file {verified_by}",
                    )
                )
                continue
            if _sha256_text(content) != expected:
                diagnostics.append(
                    _spec_diagnostic(
                        Severity.FATAL,
                        SPEC_DRIFT,
                        feature_id,
                        (
                            f"Verified-by {verified_by} hash "
                            "differs from the read-only baseline"
                        ),
                    )
                )
    return diagnostics


def _spec_diagnostic(
    severity: Severity, code: str, feature_id: str, message: str
) -> Diagnostic:
    return _guardrail_diagnostic(
        severity,
        code,
        f"{feature_id}: {message}",
        "repair the spec, verified file, or spec-drift baseline manifest.",
    )


def _pattern_diagnostic(severity: Severity, message: str, fix: str) -> Diagnostic:
    return _guardrail_diagnostic(severity, PATTERN_INTEGRITY, message, fix)


def _pattern_integrity(root: Path) -> list[Diagnostic]:
    patterns_root = root / PATTERNS_ROOT
    if not patterns_root.is_dir():
        return []
    diagnostics: list[Diagnostic] = []
    descriptions_readable = True
    try:
        descriptions = read_yaml_mapping(patterns_root / "_descriptions.yaml")
    except KernelError as error:
        # A malformed descriptions registry must cost only the
        # description-entry advisory, not all structural coverage. Isolate the
        # Handle a failed registry read like a per-pattern failure: emit one fatal.
        # to an empty registry, and keep running the broken-ref / duplicate-slug
        # dangling-source structural checks, which do not depend on it.
        # Suppress the per-pattern description-entry advisory while the registry
        # is unreadable rather than flooding one advisory per pattern.
        diagnostics.append(
            _pattern_diagnostic(
                Severity.FATAL,
                f"_descriptions.yaml: descriptions registry unreadable: {error}",
                "repair the patterns _descriptions.yaml registry.",
            )
        )
        descriptions = {}
        descriptions_readable = False
    pattern_docs: list[tuple[Path, dict[str, Any], str]] = []
    for path in sorted(patterns_root.glob("*.md")):
        if path.name.startswith("_"):
            # Underscore-prefixed files are registry/template infrastructure
            # (_descriptions.yaml, _template.md), not pattern docs — same
            # convention the registry read above already applies.
            continue
        try:
            document = read_markdown(path)
        except (KernelError, OSError, UnicodeDecodeError) as error:
            # Skip an unreadable pattern document with a fatal finding rather
            # than aborting the whole guardrail sweep.
            diagnostics.append(
                _pattern_diagnostic(
                    Severity.FATAL,
                    f"{path.name}: pattern doc could not be read: {error}",
                    "repair the pattern document and rerun validate.",
                )
            )
            continue
        pattern_docs.append((path, document.frontmatter, document.body))

    names: dict[str, list[Path]] = {}
    for path, frontmatter, _body in pattern_docs:
        name = frontmatter.get("name")
        if isinstance(name, str):
            names.setdefault(name, []).append(path)

    for name, paths in sorted(names.items()):
        if len(paths) > 1:
            diagnostics.append(
                _pattern_diagnostic(
                    Severity.FATAL,
                    f"duplicate pattern name {name}: {_paths(paths)}",
                    "give each pattern a unique name.",
                )
            )

    known_names = set(names)
    for path, frontmatter, body in pattern_docs:
        name = frontmatter.get("name")
        label = name if isinstance(name, str) else path.name
        for reference in PATTERN_REF.findall(body):
            if reference not in known_names:
                diagnostics.append(
                    _pattern_diagnostic(
                        Severity.FATAL,
                        f"{label}: broken pattern reference {reference}",
                        "create the referenced pattern or correct the reference.",
                    )
                )
        source_feature = frontmatter.get("source_feature")
        if isinstance(source_feature, str):
            # Resolve the feature reference inside the host layout; an
            # escaping reference is a dangling finding, never an
            # out-of-repo stat.
            source_rel = (FEATURES_ROOT / f"{source_feature}.md").as_posix()
            source_path = repo_relative_path(root, source_rel)
            if source_path is None or not source_path.is_file():
                diagnostics.append(
                    _pattern_diagnostic(
                        Severity.FATAL,
                        f"{label}: dangling source_feature {source_feature}",
                        "restore the source feature spec or correct source_feature.",
                    )
                )
        for cited_source in _source_paths(frontmatter, body):
            # A cited source path that escapes the host layout resolves to None
            # and is reported dangling, not stat'd.
            source_path = repo_relative_path(root, cited_source)
            if source_path is None or not source_path.is_file():
                diagnostics.append(
                    _pattern_diagnostic(
                        Severity.FATAL,
                        f"{label}: dangling source path {cited_source}",
                        "restore the cited source path or correct the citation.",
                    )
                )
        if "**Concrete future-feature scenario:**" not in body:
            diagnostics.append(
                _pattern_diagnostic(
                    Severity.ADVISORY,
                    f"{label}: missing concrete future-feature scenario",
                    "add the required scenario section.",
                )
            )
        if (
            descriptions_readable
            and isinstance(name, str)
            and (name not in descriptions)
        ):
            diagnostics.append(
                _pattern_diagnostic(
                    Severity.ADVISORY,
                    f"{name}: missing _descriptions.yaml entry",
                    "add a description row for this pattern.",
                )
            )
    return diagnostics


def _orientation_drift(root: Path) -> list[Diagnostic]:
    orientation = root / ORIENTATION_DOC
    if not orientation.is_file():
        return []
    document = read_markdown(orientation)
    resolved_root = root.resolve()
    anchor_cache: dict[Path, set[str]] = {}
    linked: set[str] = set()
    diagnostics: list[Diagnostic] = []
    for raw_target in MARKDOWN_LINK.findall(document.body):
        target, anchor = _split_markdown_target(raw_target)
        if urlsplit(target).scheme:
            continue
        target_path = (
            orientation.resolve()
            if target == ""
            else (orientation.parent / target).resolve()
        )
        try:
            rel = target_path.relative_to(resolved_root).as_posix()
        except ValueError:
            # A link that resolves outside the host layout is a fatal drift
            # finding — never stat'd or read off-repo.
            # (Legitimate in-repo relative links with `..` resolve back inside
            # root and pass this check.)
            diagnostics.append(
                _guardrail_diagnostic(
                    Severity.FATAL,
                    ORIENTATION_DRIFT,
                    f"orientation link target escapes host layout: {target}",
                    "point the link at an in-repo target.",
                )
            )
            continue
        if not target_path.is_file():
            diagnostics.append(
                _guardrail_diagnostic(
                    Severity.FATAL,
                    ORIENTATION_DRIFT,
                    f"orientation link target is missing: {target}",
                    "restore the target document or correct the link.",
                )
            )
            continue
        linked.add(rel)
        if anchor:
            anchors = anchor_cache.get(target_path)
            if anchors is None:
                try:
                    anchor_text = target_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    # An unreadable link target has no resolvable headings —
                    # the anchor cannot be verified and is reported missing
                    # below, never crashing the run.
                    anchor_text = ""
                anchors = _anchors(anchor_text)
                anchor_cache[target_path] = anchors
            if anchor not in anchors:
                diagnostics.append(
                    _guardrail_diagnostic(
                        Severity.FATAL,
                        ORIENTATION_DRIFT,
                        f"{rel}: missing anchor {anchor}",
                        "restore the heading or correct the anchor.",
                    )
                )

    covers = document.frontmatter.get("covers")
    if not isinstance(covers, list):
        return diagnostics
    for pattern in covers:
        if not isinstance(pattern, str):
            continue
        # Containment-check the glob pattern before expanding
        # it. An absolute or `..`-escaping covers pattern would otherwise make
        # glob.glob walk and stat an off-repo tree before the per-result guard
        # discards it; reject it up front so no off-repo path is ever touched
        # (mirrors the repo_relative_path posture used for link targets).
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            continue
        for covered in sorted(glob.glob(str(root / pattern))):
            covered_path = Path(covered).resolve()
            if not covered_path.is_file():
                continue
            try:
                rel = covered_path.relative_to(resolved_root).as_posix()
            except ValueError:
                # A `covers` glob that escapes the repo root (absolute or
                # `..`) has no in-repo target to index — skip it rather than
                # let `relative_to` raise and abort the whole validate run
                # (A4: validate never aborts). Mirrors the link-target guard.
                continue
            if rel not in linked:
                diagnostics.append(
                    _guardrail_diagnostic(
                        Severity.ADVISORY,
                        ORIENTATION_DRIFT,
                        f"{rel}: covered target is not linked from orientation",
                        "add it to orientation or remove it from covers.",
                    )
                )
    return diagnostics


def _managed_block_integrity(root: Path) -> list[Diagnostic]:
    """Marker integrity for the `heddle sync` managed block in AGENTS.md.

    Deliberately NO content-freshness check: `heddle sync` is explicit-only,
    so a lagging block body is normal operation, not drift. A
    missing AGENTS.md is a host that has not adopted sync — nothing to
    check; a present file without the block is ADVISORY (sync creates
    the block on demand); malformed or half-present markers are FATAL
    (broken state sync cannot repair blindly)."""
    from heddle.kernel.managed_regions import marker_fault_kind
    from heddle.runtime.sync import SESSION_ENTRY_ID

    target = root / AGENTS_DOC
    if not target.is_file():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [
            _guardrail_diagnostic(
                Severity.FATAL,
                MANAGED_BLOCK_INTEGRITY,
                f"AGENTS.md could not be read: {error}",
                "repair AGENTS.md (readable UTF-8) and rerun validate.",
            )
        ]
    fault = marker_fault_kind(text, SESSION_ENTRY_ID)
    if fault is None:
        return []
    if fault == "absent":
        return [
            _guardrail_diagnostic(
                Severity.ADVISORY,
                MANAGED_BLOCK_INTEGRITY,
                f"AGENTS.md has no `{SESSION_ENTRY_ID}` managed block",
                "run `heddle sync` to create the managed block.",
            )
        ]
    return [
        _guardrail_diagnostic(
            Severity.FATAL,
            MANAGED_BLOCK_INTEGRITY,
            (f"AGENTS.md `{SESSION_ENTRY_ID}` managed markers are malformed ({fault})"),
            "restore the exact BEGIN/END marker pair, then rerun validate.",
        )
    ]


def _mirror_drift(root: Path, config: ProjectConfig) -> list[Diagnostic]:
    """Byte-identity of the declared AGENTS.md mirror (`sync.mirror`).

    Default-on through CLAUDE.md; a host opts
    out with `sync: {mirror: null}`. `heddle sync` and `heddle init` write
    the mirror, so this is the validation backstop: a missing mirror on a
    host that has AGENTS.md is ADVISORY (sync creates it on demand, the
    posture of the managed block); divergent bytes are FATAL. A host
    with neither file has not adopted sync — nothing to check. A symlink
    that resolves to AGENTS.md satisfies the check (reads follow it)."""
    mirror_rel = config.sync_mirror
    if not mirror_rel:
        return []
    mirror = repo_relative_path(root, mirror_rel)
    if mirror is None:
        return [_mirror_diagnostic(f"sync.mirror escapes host layout: {mirror_rel}")]
    agents = root / AGENTS_DOC
    if not agents.is_file():
        if not mirror.is_file():
            return []
        return [
            _mirror_diagnostic(
                f"sync.mirror {mirror_rel} is present but AGENTS.md is missing"
            )
        ]
    if not mirror.is_file():
        return [
            _guardrail_diagnostic(
                Severity.ADVISORY,
                MIRROR_DRIFT,
                f"declared mirror {mirror_rel} is missing",
                "run `heddle sync` to create the mirror.",
            )
        ]
    if agents.read_bytes() != mirror.read_bytes():
        return [_mirror_diagnostic(f"{mirror_rel} is not byte-identical to AGENTS.md")]
    return []


def _mirror_diagnostic(message: str) -> Diagnostic:
    return _guardrail_diagnostic(
        Severity.FATAL,
        MIRROR_DRIFT,
        message,
        "run `heddle sync` to rewrite the mirror from AGENTS.md, "
        "or set `sync: {mirror: null}` in .heddle.yaml.",
    )


def prompt_convention_violations(prompts_dir: Path) -> list[str]:
    """Validate prompt convention declarations against prompt files.

    The pre-commit wrapper and the validate guardrail share this single
    implementation. Prompts are checked as the
    model receives them (partials expanded), not as they sit on disk — a
    rule spliced in from ``_partials/`` is a rule the model is told.
    Raises ``KernelError`` only for malformed manifest data; prompt resource
    errors are collected as per-prompt violations."""
    from heddle.gate.prompt import expand_partials

    partials_dir = prompts_dir / "_partials"
    config = read_yaml_mapping(prompts_dir / "conventions.yaml")
    conventions = config.get("conventions")
    prompts = config.get("prompts")
    if not isinstance(conventions, dict) or not isinstance(prompts, dict):
        return ["conventions.yaml must define 'conventions' and 'prompts'"]

    violations: list[str] = []
    for prompt_name in sorted(prompts):
        raw_required = prompts[prompt_name]
        if not isinstance(raw_required, list):
            violations.append(f"{prompt_name}: required convention list is invalid")
            continue
        path = prompts_dir / str(prompt_name)
        if not path.is_file():
            violations.append(f"{prompt_name}: configured prompt file is missing")
            continue
        try:
            text = expand_partials(
                path.read_text(encoding="utf-8"),
                partials_dir,
                template_resource=path,
            )
        except KernelError as error:
            violations.append(f"{prompt_name}: {error.message}")
            continue
        except (OSError, UnicodeDecodeError) as error:
            violations.append(f"{prompt_name}: prompt file could not be read: {error}")
            continue
        for convention_name in raw_required:
            raw_convention = conventions.get(convention_name)
            if not isinstance(raw_convention, dict):
                violations.append(
                    f"{prompt_name}: unknown convention {convention_name}"
                )
                continue
            keywords = raw_convention.get("keywords")
            if not isinstance(keywords, list) or not keywords:
                violations.append(
                    f"{prompt_name}: convention {convention_name} has no keywords"
                )
                continue
            # mode: all — every keyword must co-occur (drift groups whose
            # markers are chosen to be unique to one shared partial, so a
            # dropped or gutted splice cannot pass on a marker that another
            # source happens to carry). Default stays any-of.
            mode = raw_convention.get("mode", "any")
            if mode == "all":
                missing = [str(k) for k in keywords if str(k) not in text]
                if missing:
                    violations.append(
                        f"{prompt_name}: missing marker for {convention_name} "
                        f"(mode=all; absent: {', '.join(missing)})"
                    )
            elif not any(str(keyword) in text for keyword in keywords):
                violations.append(
                    f"{prompt_name}: missing marker for {convention_name} "
                    f"(expected one of {', '.join(map(str, keywords))})"
                )
    return violations


def _prompt_conventions(root: Path, config: ProjectConfig) -> list[Diagnostic]:
    prompts_dir = root / config.layout.prompts
    manifest = prompts_dir / "conventions.yaml"
    if not manifest.is_file():
        # Hosts without a conventions manifest have declared nothing to
        # enforce — the guardrail only runs where the contract exists.
        return []
    # Name the manifest that actually governs, not a fixed `prompts/` path:
    # the corpus root is `layout.prompts`, which differs per host (heddle's
    # own is the packaged tree under `heddle/resources/prompts/`).
    hint = (
        "restore the declared convention markers in the prompt "
        f"(or fix {manifest.relative_to(root).as_posix()})."
    )
    return [
        _guardrail_diagnostic(
            Severity.FATAL,
            PROMPT_CONVENTIONS,
            f"prompt-conventions: {violation}",
            hint,
        )
        for violation in prompt_convention_violations(prompts_dir)
    ]


def repo_relative_path(root: Path, relpath: str) -> Path | None:
    """The one path-containment guard: resolve a
    host-layout-relative reference inside ``root`` or return None. Shared
    by the guardrail sweeps and validate's cross-plane checks so the
    catch set cannot drift between them (fail-closed: an unresolvable
    path reads as escaping — a finding, never a crash)."""
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved_root = root.resolve()
    try:
        resolved = (resolved_root / path).resolve()
        resolved.relative_to(resolved_root)
    except (ValueError, OSError):
        # A null byte (ValueError from resolve()) or a
        # symlink-loop (OSError) must read as "escapes layout" — a dangling
        # finding for that single ref — never escape and abort the run (A4).
        return None
    return resolved


def _source_paths(frontmatter: dict[str, Any], body: str) -> tuple[str, ...]:
    paths: list[str] = []
    for key in ("source_path", "source_file"):
        value = frontmatter.get(key)
        if isinstance(value, str):
            paths.append(value)
    paths.extend(SOURCE_PATH.findall(body))
    return tuple(paths)


def _split_markdown_target(raw_target: str) -> tuple[str, str]:
    target, separator, anchor = raw_target.partition("#")
    if not separator:
        return raw_target, ""
    return target, anchor


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _anchors(text: str) -> set[str]:
    return {heading_anchor(match) for match in HEADING.findall(text)}


def _paths(paths: list[Path]) -> str:
    return ", ".join(path.as_posix() for path in paths)
