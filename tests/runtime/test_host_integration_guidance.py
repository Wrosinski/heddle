from __future__ import annotations

import re
import shlex
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE_PATH = REPO_ROOT / "docs" / "workflow" / "host-integration.md"
README_PATH = REPO_ROOT / "README.md"


def _guide(ac_id: str) -> str:
    assert GUIDE_PATH.is_file(), f"FAIL {ac_id}: canonical host guide is missing"
    return GUIDE_PATH.read_text(encoding="utf-8")


def _topic_section(document: str, terms: tuple[str, ...]) -> tuple[str, int]:
    headings = list(re.finditer(r"^(#{2,6})\s+(.+?)\s*$", document, re.MULTILINE))
    matches = [
        heading
        for heading in headings
        if all(term in heading.group(2).casefold() for term in terms)
    ]
    assert len(matches) == 1, f"expected one heading for topic: {terms}"
    selected = matches[0]
    level = len(selected.group(1))
    end = len(document)
    for heading in headings:
        if heading.start() > selected.start() and len(heading.group(1)) <= level:
            end = heading.start()
            break
    return document[selected.end() : end], level


def _semantic_block(document: str, term_groups: tuple[tuple[str, ...], ...]) -> str:
    matches = []
    for block in re.split(r"\n\s*\n", document):
        normalized = " ".join(block.casefold().split())
        if all(
            any(term in normalized for term in alternatives)
            for alternatives in term_groups
        ):
            matches.append(block)
    assert len(matches) == 1, f"expected one semantic block for: {term_groups}"
    return matches[0]


def _assert_ordered(document: str, terms: tuple[str, ...]) -> None:
    cursor = 0
    for term in terms:
        if term.startswith(("git ", "heddle ", "uv ")):
            match = re.search(
                rf"(?m)^[ \t]*{re.escape(term)}[ \t]*$", document[cursor:]
            )
            assert match is not None, f"missing exact ordered command: {term}"
            cursor += match.end()
        else:
            match = re.search(re.escape(term), document[cursor:], re.IGNORECASE)
            assert match is not None, f"missing ordered term: {term}"
            cursor += match.end()


def _fenced_commands(document: str) -> list[str]:
    blocks = re.findall(r"```(?:bash|sh)\s*\n(.*?)```", document, re.DOTALL)
    return [
        line.strip()
        for block in blocks
        for line in block.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_installation_modes_are_complete_and_generic() -> None:
    guide = _guide("AC-1")
    section, level = _topic_section(guide, ("installation", "mode"))
    commands = (
        "uv tool install --force --editable --python 3.13 <checkout>",
        'uv tool install --force --refresh --python 3.13 "heddle @ '
        'git+https://github.com/Wrosinski/heddle.git@<full-commit-sha>"',
        "uv tool install --force --python 3.13 "
        "/absolute/path/to/dist/heddle-<version>-py3-none-any.whl",
    )
    assert Counter(_fenced_commands(section)) == Counter(commands)
    readme_install, _ = _topic_section(
        README_PATH.read_text(encoding="utf-8"), ("install", "heddle", "adoption")
    )
    readme_commands = [
        command
        for command in _fenced_commands(readme_install)
        if command.startswith("uv tool install ")
    ]
    assert Counter(tuple(shlex.split(command)[:-1]) for command in commands) == Counter(
        tuple(shlex.split(command)[:-1]) for command in readme_commands
    )
    mode_headings = re.findall(
        rf"^#{{{level + 1},6}}\s+(.+?)\s*$", section, flags=re.MULTILINE
    )
    modes = set()
    for heading in mode_headings:
        folded = heading.casefold()
        if "edit" in folded or "source" in folded:
            modes.add("editable")
        elif "git" in folded:
            modes.add("git")
        elif "wheel" in folded:
            modes.add("wheel")
    assert modes == {"editable", "git", "wheel"}
    assert "Choose exactly one" in section


def test_first_adoption_is_previewed_and_host_owned() -> None:
    guide = _guide("AC-2")
    section, _ = _topic_section(guide, ("adopt", "repository"))
    _assert_ordered(
        section,
        (
            "git status --short",
            "AGENTS.md",
            "docs/workflow/engineering-principles.md",
            "heddle init --dry-run",
            "heddle init",
        ),
    )
    assert "checkpoint" in section.casefold()
    _semantic_block(
        section,
        (
            ("refus",),
            ("do not overwrite", "never overwrite", "without overwrit"),
            ("each target", "every target", "all target", "refused target"),
            ("review", "inspect", "resolve"),
            ("remove", "relocate", "rename", "choose another"),
        ),
    )


def test_configuration_and_authority_boundaries_are_explicit() -> None:
    guide = _guide("AC-3")
    section, _ = _topic_section(guide, ("configur", "host"))
    assert all(
        term in section
        for term in (
            ".heddle.yaml",
            "layout.specs",
            "layout.plans",
            "layout.prompts",
            "layout.schemas",
            "layout.tests",
            "layout.source",
            "commands.test",
            "commands.lint",
            "commands.typecheck",
            "agents.claude",
            "agents.codex",
        )
    )
    assert "heddle flow set" in section
    assert "AGENTS.md" in section
    assert all(
        term in section.casefold()
        for term in (
            "test authority",
            "production",
            "push",
            "deployment",
            "publication",
        )
    )


def test_verification_and_new_feature_entry_are_complete() -> None:
    guide = _guide("AC-4")
    section, _ = _topic_section(guide, ("verify", "integration"))
    commands = ("heddle doctor --json", "heddle validate", "heddle orient")
    _assert_ordered(section, commands)
    _semantic_block(
        section,
        (
            ("no active feature", "without an active feature"),
            ("exit 3",),
            ("heddle feature prepare",),
            ("legal", "healthy", "expected"),
        ),
    )
    _semantic_block(
        section,
        (
            ("fatal",),
            ("resolve", "repair", "fix"),
            ("before feature work", "before starting feature work"),
        ),
    )


def test_update_and_editable_runtime_boundaries_are_explicit() -> None:
    guide = _guide("AC-5")
    section, _ = _topic_section(guide, ("update", "heddle"))
    _assert_ordered(
        section,
        (
            "checkpoint",
            "runtime provenance",
            "fresh process",
            "heddle doctor --json",
            "affected verification",
        ),
    )
    _semantic_block(
        section,
        (
            ("editable runtime", "editable install", "editable checkout"),
            ("python source", "python code"),
            ("packaged resources", "resource files"),
            ("fresh process", "new process", "restart"),
        ),
    )
    _semantic_block(
        section,
        (
            ("selected package", "pinned package", "wheel", "git revision"),
            ("package metadata", "dependency metadata", "entry point"),
            ("reinstall", "install again"),
        ),
    )
    active_feature, _ = _topic_section(section, ("active", "projection"))
    assert all(term in active_feature.casefold() for term in ("preview", "apply"))
    _assert_ordered(active_feature, ("heddle sync --dry-run", "heddle sync"))
    inactive, _ = _topic_section(section, ("no", "active", "feature"))
    _semantic_block(
        inactive,
        (
            ("no active feature", "without an active feature"),
            ("do not create", "never create", "must not create"),
        ),
    )
    _assert_ordered(inactive, ("heddle validate", "heddle orient"))
    _assert_ordered(
        section,
        ("heddle doctor --json", "affected verification", "heddle sync --dry-run"),
    )
    _semantic_block(
        section,
        (
            ("affected verification", "affected checks", "relevant verification"),
            ("before",),
            ("rely", "use"),
            ("new evidence", "new result"),
            ("lock-tracked",),
            ("never silently rewrites", "does not silently rewrite"),
        ),
    )


def test_readme_links_the_canonical_guide() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    target = "(docs/workflow/host-integration.md)"
    install, _ = _topic_section(readme, ("install", "heddle", "adoption"))
    adoption, _ = _topic_section(readme, ("adopt", "host"))
    assert install.count(target) == 1, (
        "FAIL AC-6: installation entry lacks one canonical guide link"
    )
    assert adoption.count(target) == 1, (
        "FAIL AC-6: adoption entry lacks one canonical guide link"
    )


# --------------------------------------------------------------------------- #
# antigravity-reviewer-lane-v1 AC-24: the documented surfaces describe the
# Antigravity lane. Each topic must appear, whole, in one block (a paragraph,
# a top-level list item or a table row) of at least one of the five surfaces.
# Term alternatives keep the wording free; a lane anchor keeps unrelated text,
# such as the drive's allowlist, from counting.
# --------------------------------------------------------------------------- #
AC24_SURFACES = {
    "README.md": README_PATH,
    "host-integration guide": GUIDE_PATH,
    ".heddle.yaml scaffold": REPO_ROOT
    / "heddle"
    / "resources"
    / "heddle-yaml.scaffold.yaml",
    "test-selection map": REPO_ROOT / "docs" / "testing" / "test-selection-map.md",
    "CHANGELOG.md": REPO_ROOT / "CHANGELOG.md",
}
LANE_ANCHOR = ("antigravity", "agy")
AC24_TOPICS: dict[str, tuple[tuple[str, ...], ...]] = {
    "enabling-the-lane": (
        ("agents.antigravity", "antigravity: true"),
        ("agy",),
    ),
    "model-naming": (
        ("gemini-4-neon",),
        ("effort",),
        (
            "suffix",
            "separate",
            "base name",
            "base model",
            "gemini 4 only",
            "only gemini 4",
            "refus",
        ),
    ),
    "saved-conversations-off-limits": (
        LANE_ANCHOR,
        ("conversation",),
        ("saved", "saves", "keeps", "stores", "stored", "retain"),
        (
            "may not read",
            "must not read",
            "never read",
            "cannot read",
            "not read",
            "out-of-scope-access",
            "off limits",
            "forbidden",
        ),
    ),
    "full-permission-launch-and-deny-rule": (
        LANE_ANCHOR,
        (
            "--dangerously-skip-permissions",
            "full permission",
            "full-permission",
            "skip-permissions",
            "skips permission",
        ),
        ("deny", "denies", "denied"),
        ("read_file", ".gemini", "data director"),
    ),
    "tool-allowlist": (
        LANE_ANCHOR,
        ("allowlist", "allow list", "allow-list", "allowed tools", "approved tools"),
        ("unapproved", "fail", "outside"),
    ),
    "input-ceiling": (
        LANE_ANCHOR,
        ("250,000", "250000", "250 000", "250 kb", "250kb"),
        ("refus", "ceiling", "limit", "too large"),
    ),
    "gate-session-guard": (
        ("heddle_agent_session",),
        ("gate-session-refused", "refus"),
        ("mutat", "state-changing", "change state", "changes state"),
    ),
    "reviews-rule-on-every-lane": (
        ("reviews/",),
        (
            "out-of-scope-access",
            "fail",
            "may not",
            "must not",
            "off limits",
            "forbidden",
        ),
        (
            "every lane",
            "all lanes",
            "all three lanes",
            "each lane",
            "any lane",
            "claude and codex",
            "claude, codex",
            "every reviewer",
        ),
    ),
    "agy-host-rule-precedence": (
        LANE_ANCHOR,
        ("agents.md",),
        (
            "precedence",
            "outrank",
            "override",
            "take priority",
            "takes priority",
            "above the prompt",
            "over the prompt",
            "ahead of the prompt",
        ),
    ),
}


def _ac24_blocks() -> list[tuple[str, str]]:
    """(surface, normalized block) for every block of the five surfaces."""
    blocks: list[tuple[str, str]] = []
    for name, path in AC24_SURFACES.items():
        assert path.is_file(), f"FAIL AC-24: the {name} surface is missing: {path}"
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".yaml":
            # The scaffold is commented YAML: read its comments as prose.
            text = "\n".join(re.sub(r"^#\s?", "", line) for line in text.splitlines())
        for paragraph in re.split(r"\n\s*\n", text):
            lines = paragraph.splitlines()
            if lines and all(line.lstrip().startswith("|") for line in lines):
                pieces = lines
            else:
                pieces = re.split(r"\n(?=(?:[-*]|\d+\.) )", paragraph)
            blocks.extend(
                (name, " ".join(piece.casefold().split())) for piece in pieces
            )
    return blocks


@pytest.mark.parametrize("topic", list(AC24_TOPICS))
def test_ac24_documented_surfaces_describe_the_antigravity_lane(topic: str) -> None:
    """AC-24: README.md, the host-integration guide, the `.heddle.yaml`
    scaffold, the test-selection map and CHANGELOG.md together describe the
    topic: some block of one of them holds every term group."""
    groups = AC24_TOPICS[topic]
    found = [
        surface
        for surface, block in _ac24_blocks()
        if all(any(term in block for term in group) for group in groups)
    ]
    assert found, (
        f"FAIL AC-24: no block of {sorted(AC24_SURFACES)} describes {topic} "
        f"(each block needs one term of every group: {groups})"
    )
