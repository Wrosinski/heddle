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
    # The scope is read by _ac24_states_every_lane_scope; these groups only
    # find the candidate blocks.
    "reviews-rule-on-every-lane": (
        ("reviews/",),
        LANE_ANCHOR,
        (
            "out-of-scope-access",
            "fail",
            "may not",
            "must not",
            "off limits",
            "forbidden",
        ),
    ),
}
# What the read topic needs beyond its term groups, for the message.
AC24_READINGS = {
    "reviews-rule-on-every-lane": (
        "and scope the rule to every lane, by an every-lane phrase or by "
        "listing Claude, Codex and Antigravity, with no wording that narrows "
        "it to Claude and Codex or exempts Antigravity"
    ),
}

# The reviews/ rule holds on every lane, Antigravity included.
AC24_EVERY_LANE = re.compile(
    r"\b(?:every|each|any|all(?: three)?) (?:reviewer |review )?lanes?\b"
    r"|\b(?:every|all) reviewers?\b"
)
_AC24_LANE_NAME = r"(claude|codex|antigravity|agy)"
_AC24_LIST_JOIN = r"(?: lanes?)?(?:,| and| or|, and|, or) (?:the )?"
AC24_THREE_LANE_LIST = re.compile(
    rf"\b{_AC24_LANE_NAME}{_AC24_LIST_JOIN}{_AC24_LANE_NAME}\b"
    rf"{_AC24_LIST_JOIN}{_AC24_LANE_NAME}\b"
)
AC24_SCOPE_EXCLUSIONS = (
    re.compile(r"(?<!not )\bonly (?:on |for |in |to )?(?:the )?claude and codex\b"),
    re.compile(r"\bclaude and codex (?:lanes? |reviewers? )?only\b"),
    re.compile(
        r"\b(?:except|excluding|other than|but not|save for)"
        r" (?:on |for |in )?(?:the )?(?:antigravity|agy)\b"
    ),
    re.compile(r"\b(?:lanes?|reviewers?) but (?:the )?(?:antigravity|agy)\b"),
    re.compile(
        r"\bnot (?:(?:apply|applied|extend|extended|enforced|checked) )?"
        r"(?:to|on|for|in) (?:the )?(?:antigravity|agy)\b"
    ),
    re.compile(
        r"\b(?:antigravity|agy)\b[^.;]{0,40}?\b(?:exempt|excluded|not covered"
        r"|unaffected|(?:does|do)(?: not|n't) apply)\b"
    ),
)

# agy's host-rule precedence is an allow-list, pinned by the plan. The
# host-integration guide carries the pinned sentence verbatim (whitespace and
# line wrapping aside). Any other sentence on the five surfaces that ranks the
# prompt against AGENTS.md or host rules, or says which wins a conflict, fails
# the topic, whichever way it ranks them. A sentence that only mentions both
# does not rank them.
AC24_PRECEDENCE_TOPIC = "agy-host-rule-precedence"
AC24_PINNED_PRECEDENCE = (
    "`agy` loads the checkout's `AGENTS.md` as user rules that take "
    "precedence over the gate prompt."
)
AC24_PINNED_SURFACE = "host-integration guide"
_AC24_RANKED_PROMPT = re.compile(r"\bprompts?\b")
_AC24_RANKED_HOST = re.compile(
    r"agents\.md|user_rules|\b(?:host|user) rules?\b|\bhost instructions?\b"
    r"|\b(?:these|those|such|its|their) rules\b"
)
# Authority and win words, and conflict words, rank beside either side
# alone. With neither side named, an authority or win word ranks beside a
# conflict phrase ("in a conflict, the latter wins"); a bare conflict noun or
# "a briefing that conflicts" names another subject and does not count.
_AC24_AUTHORITY_WORDS = re.compile(
    r"\b(?:preceden\w*|precede[sd]?|priorit\w*|outrank\w*|supersed\w*|trump\w*"
    r"|authorit\w*|prevail\w*|wins?|winning|won(?!')|winner|los(?:e|es|ing|er)"
    r"|final say|last word|upper hand|go(?:es)? with|went with|defer\w*"
    r"|yield\w*|give[sn]? way|gave way|takes? over|took over|outweigh\w*"
    r"|dominat\w*|subordinat\w*|controls?|governs?|binding|decisive"
    r"|comes? first|in charge|paramount|supreme|overrode|overridden"
    r"|overrid(?:e|es|ing) (?:the|any|all|every|other|its|their|agents|host"
    r"|user|prompt))\b"
)
_AC24_CONFLICT_WORDS = re.compile(
    r"\b(?:conflict\w*|contradict\w*|disagree\w*|clash\w*|collid\w*"
    r"|collision\w*|compet(?:e|es|ing|ition)|inconsisten\w*|at odds"
    r"|if in doubt)\b"
)
_AC24_CONFLICT_PHRASES = re.compile(
    r"\b(?:in|on|during|for) (?:a|an|any|each|every|the|case of(?: a)?)"
    r" (?:conflict|clash|disagreement|contradiction|collision)s?\b"
    r"|\b(?:when|whenever|where|wherever|if|should|once)\b(?: \S+){0,3}?"
    r" (?:conflict|disagree|clash|collide|compete|contradict)(?:s|ed)?\b"
    r"|\b(?:conflicting|contradictory|if in doubt|in case of doubt|at odds)\b"
)
# Directive and comparison words rank only beside both sides.
_AC24_RANKING_WORDS_BESIDE_BOTH = re.compile(
    r"\b(?:overrid\w*|govern\w*|control\w*|decid\w*|regardless|irrespective"
    r"|whatever|no matter|even (?:if|when|where|though)|otherwise|follow\w*"
    r"|ignor\w*|disregard\w*|instead|rather than|in favou?r of|prefer\w*"
    r"|above|below|ahead of|rank\w*|obey\w*|heed\w*|beats?|first)\b"
)


def _ac24_normalize(piece: str) -> str:
    """Casefolded text without code or emphasis marks, whitespace collapsed."""
    plain = re.sub(r"[`*]", "", piece).replace("\u2019", "'")
    return " ".join(plain.casefold().split())


def _ac24_split(text: str) -> list[str]:
    """Normalized blocks of one text: paragraphs, top-level list items and
    table rows."""
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = paragraph.splitlines()
        if lines and all(line.lstrip().startswith("|") for line in lines):
            pieces = lines
        else:
            pieces = re.split(r"\n(?=(?:[-*]|\d+\.) )", paragraph)
        blocks.extend(_ac24_normalize(piece) for piece in pieces)
    return blocks


def _ac24_sentences(text: str) -> list[str]:
    """Sentences of one text, case and marks kept, whitespace collapsed, with
    heading, list, quote and table markup set apart."""
    text = re.sub(r"^(#{1,6} .*)$", "\\1\n", text, flags=re.MULTILINE)
    sentences: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = paragraph.splitlines()
        if lines and all(line.lstrip().startswith("|") for line in lines):
            pieces = [cell for line in lines for cell in line.split("|")]
        else:
            pieces = re.split(r"\n(?=\s*(?:[-*+]|\d+\.) )", paragraph)
        for piece in pieces:
            piece = re.sub(r"^\s*(?:>\s?)+", "", piece, flags=re.MULTILINE)
            piece = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", piece)
            flat = " ".join(piece.split())
            sentences.extend(s for s in re.split(r"(?<=[.!?])\s+", flat) if s)
    return sentences


def _ac24_surface_texts() -> dict[str, str]:
    """The five surfaces' text; the scaffold's YAML comments read as prose."""
    texts: dict[str, str] = {}
    for name, path in AC24_SURFACES.items():
        assert path.is_file(), f"FAIL AC-24: the {name} surface is missing: {path}"
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".yaml":
            text = "\n".join(re.sub(r"^#\s?", "", line) for line in text.splitlines())
        texts[name] = text
    return texts


def _ac24_blocks() -> list[tuple[str, str]]:
    """(surface, normalized block) for every block of the five surfaces."""
    return [
        (name, block)
        for name, text in _ac24_surface_texts().items()
        for block in _ac24_split(text)
    ]


def _ac24_states_every_lane_scope(block: str) -> bool:
    """The block scopes the rule to every lane, by an every-lane phrase or a
    list naming the three lanes, and no wording in it narrows that scope."""
    listed = any(
        {"antigravity" if name == "agy" else name for name in match.groups()}
        == {"claude", "codex", "antigravity"}
        for match in AC24_THREE_LANE_LIST.finditer(block)
    )
    scoped = listed or AC24_EVERY_LANE.search(block) is not None
    return scoped and not any(rule.search(block) for rule in AC24_SCOPE_EXCLUSIONS)


def _ac24_describes(topic: str, block: str) -> bool:
    """One normalized block describes the topic: it holds every term group
    and, for the reviews/ rule, scopes it to every lane."""
    groups = AC24_TOPICS[topic]
    if not all(any(term in block for term in group) for group in groups):
        return False
    if topic == "reviews-rule-on-every-lane":
        return _ac24_states_every_lane_scope(block)
    return True


def _ac24_ranks(sentence: str) -> bool:
    """The sentence ranks the prompt against host rules, or says which wins
    a conflict: it pairs a conflict phrase with an authority or win word,
    names either side beside an authority, win or conflict word, or names
    both sides beside a directive or comparison word."""
    plain = _ac24_normalize(sentence)
    authority = _AC24_AUTHORITY_WORDS.search(plain) is not None
    conflict = _AC24_CONFLICT_WORDS.search(plain) is not None
    if authority and _AC24_CONFLICT_PHRASES.search(plain):
        return True
    prompt = _AC24_RANKED_PROMPT.search(plain) is not None
    host = _AC24_RANKED_HOST.search(plain) is not None
    if not (prompt or host):
        return False
    if authority or conflict:
        return True
    return prompt and host and _AC24_RANKING_WORDS_BESIDE_BOTH.search(plain) is not None


def _ac24_precedence_problems(texts: dict[str, str]) -> list[tuple[str, str]]:
    """(kind, detail) for each way the surfaces depart from the pinned
    precedence: "pinned" when the guide lacks the sentence verbatim, and
    "ranking" for every other sentence that ranks the two sides."""
    problems: list[tuple[str, str]] = []
    guide = _ac24_sentences(texts.get(AC24_PINNED_SURFACE, ""))
    if AC24_PINNED_PRECEDENCE not in guide:
        problems.append(
            ("pinned", f"the {AC24_PINNED_SURFACE} lacks {AC24_PINNED_PRECEDENCE!r}")
        )
    pinned = _ac24_normalize(AC24_PINNED_PRECEDENCE)
    for name, text in texts.items():
        for sentence in _ac24_sentences(text):
            if _ac24_normalize(sentence) != pinned and _ac24_ranks(sentence):
                problems.append(("ranking", f"{name}: {sentence!r}"))
    return problems


@pytest.mark.parametrize("topic", [*AC24_TOPICS, AC24_PRECEDENCE_TOPIC])
def test_ac24_documented_surfaces_describe_the_antigravity_lane(topic: str) -> None:
    """AC-24: README.md, the host-integration guide, the `.heddle.yaml`
    scaffold, the test-selection map and CHANGELOG.md together describe the
    topic: some block of one of them holds every term group, scoping the
    reviews/ rule to every lane. agy's precedence is the plan's pinned
    sentence in the guide, and no other sentence ranks the two sides."""
    if topic == AC24_PRECEDENCE_TOPIC:
        problems = _ac24_precedence_problems(_ac24_surface_texts())
        assert not problems, (
            f"FAIL AC-24: agy's host-rule precedence must be the pinned sentence "
            f"alone: the {AC24_PINNED_SURFACE} carries it verbatim and no other "
            f"sentence of {sorted(AC24_SURFACES)} ranks the prompt against "
            f"AGENTS.md or host rules: {problems}"
        )
        return
    groups = AC24_TOPICS[topic]
    found = [
        surface for surface, block in _ac24_blocks() if _ac24_describes(topic, block)
    ]
    reading = f", {AC24_READINGS[topic]}" if topic in AC24_READINGS else ""
    assert found, (
        f"FAIL AC-24: no block of {sorted(AC24_SURFACES)} describes {topic} "
        f"(each block needs one term of every group: {groups}{reading})"
    )


# Matcher controls for the read topics. Pure in-memory text, no surface read;
# they pass now. Each control is a topic and the surface texts it reads.
# Every rejected reviews/ text holds the topic's old terms (a lane name,
# reviews/ and a failure word), so each rejection comes from the scope
# reading. A rejected precedence control beside the pinned sentence fails
# only on a ranking sentence; one without it fails on the pinned check.
def _beside_pinned(text: str) -> dict[str, str]:
    return {AC24_PINNED_SURFACE: f"{AC24_PINNED_PRECEDENCE} {text}"}


def _on_guide(text: str) -> dict[str, str]:
    return {AC24_PINNED_SURFACE: text}


AC24_MENTION_ONLY = (
    "The reviewer reads the gate prompt's captured input with `read_file`, "
    "and `agy` also loads `GEMINI.md` beside `AGENTS.md`."
)
AC24_ACCEPTED: dict[str, tuple[str, dict[str, str]]] = {
    "every-lane-including-antigravity": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "On every lane, including Antigravity, a reviewer that reads "
            "`reviews/` fails as `out-of-scope-access`."
        ),
    ),
    "three-lanes-listed": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "- Claude, Codex and `agy` reviewers may not read `reviews/`; a read "
            "fails the review as `out-of-scope-access`."
        ),
    ),
    "all-three-lanes": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "The `reviews/` rule holds on all three lanes: an Antigravity reviewer "
            "that opens a sibling review fails like a Claude or Codex one."
        ),
    ),
    "pinned-sentence": (AC24_PRECEDENCE_TOPIC, _on_guide(AC24_PINNED_PRECEDENCE)),
    "pinned-sentence-wrapped-in-a-list-item": (
        AC24_PRECEDENCE_TOPIC,
        _on_guide(
            "## Antigravity lane\n"
            "- `agy` loads the checkout's `AGENTS.md` as user rules\n"
            "  that take precedence over the gate prompt.\n"
            "- `heddle doctor` reports `agy` at info severity.\n"
        ),
    ),
    "pinned-sentence-and-a-mention-only-sentence": (
        AC24_PRECEDENCE_TOPIC,
        _beside_pinned(AC24_MENTION_ONLY),
    ),
    "pinned-sentence-repeated-on-another-surface": (
        AC24_PRECEDENCE_TOPIC,
        {
            AC24_PINNED_SURFACE: AC24_PINNED_PRECEDENCE,
            "CHANGELOG.md": "- agy loads the checkout's AGENTS.md as user rules "
            "that take precedence over the gate prompt.",
        },
    ),
}

# Any other ranking sentence fails beside the pinned one: the reversals the
# earlier free-prose readings had to catch, and, by design, correct but
# different wording.
AC24_PRECEDENCE_REVERSALS = {
    # Reviewer probes, verbatim.
    "prompt-authoritative-in-conflicts": (
        "In agy, host AGENTS.md outranks the gate prompt. When the instructions "
        "conflict, the gate prompt is authoritative."
    ),
    "conflict-ruling-without-host-rules": (
        "When the instructions conflict, the gate prompt is authoritative."
    ),
    "go-with-the-gate-prompt": "When the two disagree, go with the gate prompt.",
    "in-a-conflict-use-the-prompt-instead": (
        "In agy the host AGENTS.md outranks the gate prompt. In a conflict, "
        "however, use the prompt instead."
    ),
    "treat-the-prompt-as-authoritative": "Treat the gate prompt as authoritative.",
    # Conflict rulings that name neither side.
    "the-latter-wins-a-conflict": "When they conflict, the latter wins.",
    "lane-block-wins-a-conflict": "In a conflict, the lane block wins.",
    "prompt-outranks-host-rules": (
        "`agy` loads the host `AGENTS.md`, but the gate prompt outranks those rules."
    ),
    "prompt-takes-precedence": (
        "In `agy` the gate prompt takes precedence over `AGENTS.md` and other "
        "host rules."
    ),
    "host-rules-lower-precedence": (
        "When `agy` runs, `AGENTS.md` has lower precedence than the review prompt."
    ),
    "host-rules-do-not-override": (
        "`agy` reads `AGENTS.md`, yet its rules do not override the gate prompt."
    ),
    "both-directions": (
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt. "
        "Where they conflict, the prompt overrides `AGENTS.md`."
    ),
    "precedence-without-direction": (
        "In `agy`, `AGENTS.md` takes precedence; the prompt is a user message."
    ),
    "conflict-exception-gives-the-prompt-the-win": (
        "In `agy`, host `AGENTS.md` outranks the gate prompt, except when it "
        "contradicts the prompt, in which case the prompt wins."
    ),
    "but-the-prompt-wins-a-conflict": (
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt, but "
        "when they conflict, the prompt wins."
    ),
    "unless-the-prompt-says-otherwise": (
        "`agy` gives `AGENTS.md` precedence over the gate prompt unless the "
        "prompt says otherwise."
    ),
    "only-outside-a-conflict": (
        "`AGENTS.md` outranks the prompt in `agy` only when the two do not conflict."
    ),
    "conflict-follows-the-prompt": (
        "`AGENTS.md` takes precedence over the prompt in `agy`; where the two "
        "conflict, the reviewer follows the prompt."
    ),
    "host-rules-yield-in-a-conflict": (
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt, yet "
        "in a conflict the host rules yield."
    ),
    "in-a-conflict-follow-the-prompt": (
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt. In a "
        "conflict, follow the prompt."
    ),
    "use-the-prompt-instruction-when-they-disagree": (
        "`AGENTS.md` takes precedence over the gate prompt in `agy`; use the "
        "gate prompt's instruction when they disagree."
    ),
    "prompt-should-be-followed-in-a-conflict": (
        "`agy` loads `AGENTS.md` as user rules that outrank the prompt, but if "
        "they conflict, the prompt should be followed."
    ),
    "if-in-doubt-defer-to-the-prompt": (
        "In `agy`, `AGENTS.md` overrides the prompt; if in doubt, defer to the prompt."
    ),
    "give-the-prompt-priority": (
        "`AGENTS.md` outranks the prompt in `agy`, yet reviewers should give "
        "the prompt priority."
    ),
    "follow-the-prompt-rather-than-host-rules": (
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt; "
        "still, follow the prompt rather than `AGENTS.md`."
    ),
    "instead-of-host-rules-use-the-prompt": (
        "`AGENTS.md` takes precedence over the prompt in `agy`. Instead of "
        "`AGENTS.md`, reviewers use the prompt."
    ),
}
AC24_PRECEDENCE_DIFFERENT_RULINGS = {
    "user-rules-take-precedence": (
        "`agy` injects the host `AGENTS.md` as user rules that take precedence "
        "over the prompt, so the lane block forbids `heddle` whatever those "
        "rules say."
    ),
    "user-rules-outrank-the-prompt": (
        "`agy` injects `AGENTS.md` as user rules that outrank the gate prompt."
    ),
    "host-rules-override-the-prompt": (
        "In `agy`, host rules from `AGENTS.md` override the review prompt."
    ),
    "conflict-follows-host-rules": (
        "When the two conflict, the reviewer follows `AGENTS.md`."
    ),
    "prompt-outranked-in-any-conflict": (
        "In `agy` the gate prompt is outranked by `AGENTS.md` in any conflict."
    ),
    "whatever-the-prompt-says": (
        "Whatever the gate prompt says, `agy` follows the host `AGENTS.md`."
    ),
    "host-rules-win-the-conflict": (
        "When they conflict, the host rules win and the prompt loses."
    ),
    "in-a-conflict-go-with-host-rules": "In a conflict, go with `AGENTS.md`.",
    "use-host-rules-rather-than-the-prompt": (
        "When they disagree, `agy` uses `AGENTS.md` rather than the gate prompt."
    ),
}
AC24_REJECTED: dict[str, tuple[str, dict[str, str]]] = {
    "every-lane-omitting-antigravity": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "- On every lane, a reviewer that reads a sibling's output under "
            "`reviews/` fails as `out-of-scope-access`."
        ),
    ),
    "claude-and-codex-named": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "On Claude and Codex, a reviewer that reads `reviews/` fails as "
            "`out-of-scope-access`; `agy` reviews are audited from their stream."
        ),
    ),
    "claude-and-codex-only": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "Reading `reviews/` fails as `out-of-scope-access` on the Claude and "
            "Codex lanes only, not on Antigravity."
        ),
    ),
    "every-lane-except-antigravity": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "On every lane except Antigravity, a reviewer that reads `reviews/` "
            "fails as `out-of-scope-access`."
        ),
    ),
    "rule-not-applied-to-agy": (
        "reviews-rule-on-every-lane",
        _on_guide(
            "The `reviews/` rule applies to all lanes and fails a read as "
            "`out-of-scope-access`, but it does not apply to `agy`."
        ),
    ),
    "pinned-sentence-missing": (AC24_PRECEDENCE_TOPIC, _on_guide(AC24_MENTION_ONLY)),
    "pinned-sentence-reworded": (
        AC24_PRECEDENCE_TOPIC,
        _on_guide(
            "`agy` loads the checkout's `AGENTS.md` as user rules that outrank "
            "the gate prompt."
        ),
    ),
    "pinned-sentence-with-a-curly-apostrophe": (
        AC24_PRECEDENCE_TOPIC,
        _on_guide(AC24_PINNED_PRECEDENCE.replace("'", "\u2019")),
    ),
    "pinned-sentence-only-outside-the-guide": (
        AC24_PRECEDENCE_TOPIC,
        {AC24_PINNED_SURFACE: "", "README.md": AC24_PINNED_PRECEDENCE},
    ),
    "reversal-on-another-surface": (
        AC24_PRECEDENCE_TOPIC,
        {
            AC24_PINNED_SURFACE: AC24_PINNED_PRECEDENCE,
            "README.md": "In a conflict, the gate prompt wins over `AGENTS.md`.",
        },
    ),
    **{
        f"reversal-{case}": (AC24_PRECEDENCE_TOPIC, _beside_pinned(text))
        for case, text in AC24_PRECEDENCE_REVERSALS.items()
    },
    **{
        f"different-ruling-{case}": (AC24_PRECEDENCE_TOPIC, _beside_pinned(text))
        for case, text in AC24_PRECEDENCE_DIFFERENT_RULINGS.items()
    },
}
# The terms a term-only check accepted; every rejected reviews/ control holds
# them.
AC24_TERM_ONLY = (
    ("reviews/",),
    ("out-of-scope-access",),
    ("every lane", "all lanes", "claude and codex"),
)


def _ac24_control_holds(topic: str, texts: dict[str, str]) -> bool:
    if topic == AC24_PRECEDENCE_TOPIC:
        return not _ac24_precedence_problems(texts)
    return any(
        _ac24_describes(topic, block)
        for text in texts.values()
        for block in _ac24_split(text)
    )


@pytest.mark.parametrize("case", list(AC24_ACCEPTED))
def test_ac24_matcher_accepts_the_contract_wording(case: str) -> None:
    """AC-24 matcher control (passes now): text that scopes the reviews/
    rule to every lane with Antigravity, or carries the pinned precedence
    sentence in the guide with only mention-only sentences beside it,
    describes its topic."""
    topic, texts = AC24_ACCEPTED[case]
    assert _ac24_control_holds(topic, texts), (
        f"FAIL AC-24: the matcher rejects contract wording for {topic}: {texts!r} "
        f"{_ac24_precedence_problems(texts) if topic == AC24_PRECEDENCE_TOPIC else ''}"
    )


@pytest.mark.parametrize("case", list(AC24_REJECTED))
def test_ac24_matcher_rejects_omitted_lanes_and_reversed_precedence(
    case: str,
) -> None:
    """AC-24 matcher control (passes now): text that omits Antigravity from
    the reviews/ rule or narrows it to Claude and Codex describes nothing;
    the precedence topic fails without the pinned sentence in the guide, and
    beside it fails on any other ranking sentence, reversed or not."""
    topic, texts = AC24_REJECTED[case]
    if topic == AC24_PRECEDENCE_TOPIC:
        kinds = {kind for kind, _ in _ac24_precedence_problems(texts)}
        pinned = AC24_PINNED_PRECEDENCE in _ac24_sentences(
            texts.get(AC24_PINNED_SURFACE, "")
        )
        expected = {"ranking"} if pinned else {"pinned"}
        assert expected <= kinds and (not pinned or kinds == expected), (
            f"FAIL AC-24: {case} must fail on {expected}, found {kinds}: {texts!r}"
        )
        return
    blocks = [block for text in texts.values() for block in _ac24_split(text)]
    assert any(
        all(any(term in block for term in group) for group in AC24_TERM_ONLY)
        for block in blocks
    ), f"control {case} must hold the terms a term-only check would accept"
    assert not _ac24_control_holds(topic, texts), (
        f"FAIL AC-24: the matcher accepts {case} wording for {topic}: {texts!r}"
    )
