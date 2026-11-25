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
    # Scope and direction are read by _ac24_states_every_lane_scope and
    # _ac24_precedence_signs; these groups only find the candidate blocks.
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
    "agy-host-rule-precedence": (
        LANE_ANCHOR,
        ("agents.md",),
        ("prompt",),
    ),
}
# What the two read topics need beyond their term groups, for the message.
AC24_READINGS = {
    "reviews-rule-on-every-lane": (
        "and scope the rule to every lane, by an every-lane phrase or by "
        "listing Claude, Codex and Antigravity, with no wording that narrows "
        "it to Claude and Codex or exempts Antigravity"
    ),
    "agy-host-rule-precedence": (
        "and rule for AGENTS.md, injected as user rules: a sentence that it "
        "outranks, overrides or takes precedence over the prompt, or a "
        "conflict clause (such as 'when they conflict') giving it the win, "
        "with no sentence, exception or directive (such as 'in a conflict, "
        "use the prompt') giving the prompt the win"
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

# agy injects the host's AGENTS.md as user rules that outrank the gate prompt,
# so in a conflict the host rules control. The block is read clause by clause
# and each pattern carries the direction it states: +1 when the host rules
# win, -1 when the prompt wins. A negation just before the subject or the
# verb flips it. Precedence patterns read "subject ... verb ... object".
_AC24_HOST_RULES = (
    r"(?:agents\.md|user_rules|\b(?:host|user|these|those|such|its|their) rules?\b)"
)
_AC24_PROMPT = r"prompt"
_AC24_OUTRANKS = (
    r"outranks?|overrides?|supersedes?|trumps?|wins? over"
    r"|(?:precedence|priority) over|(?:higher|greater) (?:precedence|priority) than"
    r"|(?:ranks?|ranked|sits?|placed) (?:above|over|ahead of)|above|ahead of"
)
_AC24_OUTRANKED = (
    r"(?:outranked|overridden|superseded|trumped) by"
    r"|yields? to|defers? to|gives? way to|subordinate to"
    r"|(?:lower|less) (?:precedence|priority) than"
    r"|(?:ranks?|ranked|sits?|placed) below|below"
)
AC24_PRECEDENCE_STATEMENTS = tuple(
    (
        re.compile(
            rf"{subject}(?P<mid>(?:(?!{obj}).){{0,120}}?)"
            rf"\b(?:{verbs})\b(?:(?!{subject}).){{0,40}}?{obj}"
        ),
        direction,
    )
    for subject, obj, verbs, direction in (
        (_AC24_HOST_RULES, _AC24_PROMPT, _AC24_OUTRANKS, 1),
        (_AC24_PROMPT, _AC24_HOST_RULES, _AC24_OUTRANKS, -1),
        (_AC24_HOST_RULES, _AC24_PROMPT, _AC24_OUTRANKED, -1),
        (_AC24_PROMPT, _AC24_HOST_RULES, _AC24_OUTRANKED, 1),
    )
)
# Wherever they stand, grants and preferences also rule: "give <side>
# precedence", "precedence goes to <side>", "let <side> win", "follow <side>
# rather than <other>", "instead of <other>, use <side>".
_AC24_DIRECTIVES = (
    r"follows?|following|obeys?|obeying|sides? with|goes? with|go by|goes by"
    r"|defers? to|uses?|using|prefers?|preferring|appl(?:y|ies|ying)"
    r"|rel(?:y|ies) on|sticks? to|keeps? to|honou?rs?|trusts?|choos(?:e|es|ing)"
    r"|picks?|listens? to|acts? on|heeds?|abides? by|compl(?:y|ies) with"
    r"|falls? back (?:on|to)|reverts? to"
)
_AC24_SIDES = (
    (_AC24_HOST_RULES, _AC24_PROMPT, 1),
    (_AC24_PROMPT, _AC24_HOST_RULES, -1),
)


def _ac24_near(other: str, span: int) -> str:
    """Up to span characters within one clause that never name the other side."""
    return rf"(?:(?!{other})[^,;:]){{0,{span}}}?"


AC24_PRECEDENCE_GRANTS = (
    tuple(
        (re.compile(pattern.format(side=side, near=_ac24_near(other, 20))), direction)
        for side, other, direction in _AC24_SIDES
        for pattern in (
            r"(?P<mid>)\b(?:gives?|giving|grants?|granting|hands?|handing)\b{near}"
            r"{side}(?:'s \w+)?"
            r" (?:precedence|priority|the (?:win|final say|last word))",
            r"(?P<mid>)\b(?:precedence|priority|the final say|the last word)"
            r" (?:goes |go |belongs |falls |is given |is handed )?to\b{near}{side}",
            r"(?P<mid>)\blet(?:s|ting)?\b{near}{side}(?:'s \w+)?"
            r" (?:win|prevail|decide|control|govern|take over)\b",
        )
    )
    + tuple(
        (
            re.compile(
                rf"(?P<mid>)\b(?:{_AC24_DIRECTIVES})\b{_ac24_near(other, 30)}{side}"
                r"[^.;]{0,40}?"
                r"(?:\b(?:instead of|rather than|in place of|over)\b|, not\b)"
                rf"[^.;]{{0,20}}?{other}"
            ),
            direction,
        )
        for side, other, direction in _AC24_SIDES
    )
    + tuple(
        (
            re.compile(
                rf"(?P<mid>)\b(?:instead of|rather than|in place of)\b"
                rf"(?:(?!{side})[^.;]){{0,30}}?{other}[^.;]{{0,30}}?"
                rf"\b(?:{_AC24_DIRECTIVES})\b{_ac24_near(other, 30)}{side}"
            ),
            direction,
        )
        for side, other, direction in _AC24_SIDES
    )
)
# A clause that speaks of a conflict also states who wins it: "<side> wins",
# "<side> should be followed", "<side> loses" (subject and verb with no comma
# or colon between them), or a directive, imperative or not: "agy follows
# <side>", "in a conflict, use <side>". These rulings count only in such a
# clause, and an exception that hands the conflict to the prompt reverses
# the block.
AC24_CONFLICT_CUE = re.compile(
    r"conflict|contradict|disagree|clash|inconsisten|at odds|\bdiffer(?:s|ing)?\b"
    r"|otherwise|whatever|regardless|even (?:if|when|where|though)|doubt"
    r"|collid|collision|\bcompet(?:e|es|ing|ition)\b"
)
_AC24_WINS = (
    r"wins?|prevails?|controls?|governs?|decides?|comes? first|takes? over"
    r"|(?:is|are|gets?|be|been) (?:followed|obeyed|used|applied|preferred"
    r"|trusted|honou?red)|applies|apply"
    r"|(?:has|have|takes?|gets?) (?:the )?(?:precedence|priority|final say"
    r"|last word|upper hand)"
)
_AC24_LOSES = (
    r"loses?|yields?|gives? way"
    r"|(?:is|are|gets?|be|been) (?:ignored|overridden|dropped|set aside)"
)
AC24_CONFLICT_RULINGS = tuple(
    (
        re.compile(rf"{side}(?P<mid>{_ac24_near(other, 30)})\b(?:{verbs})\b"),
        outcome * direction,
    )
    for side, other, direction in _AC24_SIDES
    for verbs, outcome in ((_AC24_WINS, 1), (_AC24_LOSES, -1))
) + tuple(
    (
        re.compile(
            rf"(?P<mid>)\b(?:{_AC24_DIRECTIVES})\b{_ac24_near(other, 30)}{side}"
        ),
        direction,
    )
    for side, other, direction in _AC24_SIDES
)
AC24_CONFLICT_EXCEPTION = re.compile(
    r"\b(?:except|unless|save for|but not when|only (?:when|if|where))\b"
    r"[^.;]{0,80}?(?:prompt|conflict|contradict|disagree|clash|otherwise)"
)
AC24_NEGATION = re.compile(
    r"\b(?:not|never|cannot|can't|won't|doesn't|don't|isn't|aren't|no longer)\b"
)


def _ac24_split(text: str) -> list[str]:
    """Normalized blocks of one text: paragraphs, top-level list items and
    table rows, casefolded, without code or emphasis marks."""
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = paragraph.splitlines()
        if lines and all(line.lstrip().startswith("|") for line in lines):
            pieces = lines
        else:
            pieces = re.split(r"\n(?=(?:[-*]|\d+\.) )", paragraph)
        for piece in pieces:
            plain = re.sub(r"[`*]", "", piece).replace("’", "'")
            blocks.append(" ".join(plain.casefold().split()))
    return blocks


def _ac24_blocks() -> list[tuple[str, str]]:
    """(surface, normalized block) for every block of the five surfaces."""
    blocks: list[tuple[str, str]] = []
    for name, path in AC24_SURFACES.items():
        assert path.is_file(), f"FAIL AC-24: the {name} surface is missing: {path}"
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".yaml":
            # The scaffold is commented YAML: read its comments as prose.
            text = "\n".join(re.sub(r"^#\s?", "", line) for line in text.splitlines())
        blocks.extend((name, block) for block in _ac24_split(text))
    return blocks


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


def _ac24_negated(text: str, words: int) -> bool:
    return AC24_NEGATION.search(" ".join(text.split()[-words:])) is not None


def _ac24_direction(clause: str, match: re.Match[str], direction: int) -> int:
    before = re.split(r"[,:(]", clause[: match.start()])[-1]
    flips = _ac24_negated(before, 3) + _ac24_negated(match["mid"], 2)
    return -direction if flips % 2 else direction


def _ac24_precedence_readings(block: str) -> set[int]:
    """Every direction the block states: +1 where host rules control, -1
    where the prompt does. A precedence sentence is itself a ruling on a
    conflict; an exception clause about the prompt or a conflict counts -1."""
    stated: set[int] = set()
    for sentence in re.split(r"(?<=[.;!?])\s+", block):
        clauses = re.split(
            r",\s*(?=(?:but|so|yet|while|whereas|although|though)\b)", sentence
        )
        for clause in clauses:
            cue = AC24_CONFLICT_CUE.search(clause) is not None
            patterns = (
                AC24_PRECEDENCE_STATEMENTS
                + AC24_PRECEDENCE_GRANTS
                + (AC24_CONFLICT_RULINGS if cue else ())
            )
            signs = {
                _ac24_direction(clause, match, direction)
                for pattern, direction in patterns
                for match in pattern.finditer(clause)
            }
            if (cue or signs) and AC24_CONFLICT_EXCEPTION.search(clause):
                signs.add(-1)
            stated |= signs
    return stated


def _ac24_describes(topic: str, block: str) -> bool:
    """One normalized block describes the topic: it holds every term group
    and, for the reviews/ scope and agy's precedence, states them the
    contract's way round."""
    groups = AC24_TOPICS[topic]
    if not all(any(term in block for term in group) for group in groups):
        return False
    if topic == "reviews-rule-on-every-lane":
        return _ac24_states_every_lane_scope(block)
    if topic == "agy-host-rule-precedence":
        return _ac24_precedence_readings(block) == {1}
    return True


@pytest.mark.parametrize("topic", list(AC24_TOPICS))
def test_ac24_documented_surfaces_describe_the_antigravity_lane(topic: str) -> None:
    """AC-24: README.md, the host-integration guide, the `.heddle.yaml`
    scaffold, the test-selection map and CHANGELOG.md together describe the
    topic: some block of one of them holds every term group and states the
    reviews/ scope and agy's precedence the contract's way round."""
    groups = AC24_TOPICS[topic]
    found = [
        surface for surface, block in _ac24_blocks() if _ac24_describes(topic, block)
    ]
    reading = f", {AC24_READINGS[topic]}" if topic in AC24_READINGS else ""
    assert found, (
        f"FAIL AC-24: no block of {sorted(AC24_SURFACES)} describes {topic} "
        f"(each block needs one term of every group: {groups}{reading})"
    )


# Matcher controls for the two read topics. Pure in-memory text, no surface
# read; they pass now. Every rejected text holds the topic's old terms (a lane
# name, reviews/ and a failure word, or AGENTS.md and a precedence word), so
# each rejection comes from the scope, direction or conflict reading.
AC24_ACCEPTED = {
    "every-lane-including-antigravity": (
        "reviews-rule-on-every-lane",
        "On every lane, including Antigravity, a reviewer that reads "
        "`reviews/` fails as `out-of-scope-access`.",
    ),
    "three-lanes-listed": (
        "reviews-rule-on-every-lane",
        "- Claude, Codex and `agy` reviewers may not read `reviews/`; a read "
        "fails the review as `out-of-scope-access`.",
    ),
    "all-three-lanes": (
        "reviews-rule-on-every-lane",
        "The `reviews/` rule holds on all three lanes: an Antigravity reviewer "
        "that opens a sibling review fails like a Claude or Codex one.",
    ),
    # "whatever those rules say" reports what the lane block tells the
    # reviewer, which the contract has it say; it names no winner, so the
    # precedence sentence stands as the ruling.
    "user-rules-take-precedence": (
        "agy-host-rule-precedence",
        "`agy` injects the host `AGENTS.md` as user rules that take precedence "
        "over the prompt, so the lane block forbids `heddle` whatever those "
        "rules say.",
    ),
    "user-rules-outrank-the-prompt": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the gate prompt.",
    ),
    "host-rules-override-the-prompt": (
        "agy-host-rule-precedence",
        "In `agy`, host rules from `AGENTS.md` override the review prompt.",
    ),
    "conflict-follows-host-rules": (
        "agy-host-rule-precedence",
        "`agy` injects the host `AGENTS.md` as user rules that take precedence "
        "over the prompt: when the two conflict, the reviewer follows "
        "`AGENTS.md`.",
    ),
    "prompt-outranked-in-any-conflict": (
        "agy-host-rule-precedence",
        "In `agy` the gate prompt is outranked by `AGENTS.md` in any conflict, "
        "because `AGENTS.md` arrives as user rules.",
    ),
    "prompt-cannot-override-in-a-contradiction": (
        "agy-host-rule-precedence",
        "Antigravity loads `AGENTS.md` as user rules, and where they contradict "
        "the gate prompt, the prompt cannot override those rules.",
    ),
    "whatever-the-prompt-says": (
        "agy-host-rule-precedence",
        "Whatever the gate prompt says, `agy` follows the host `AGENTS.md`, "
        "which it injects as user rules.",
    ),
    "host-rules-win-the-conflict": (
        "agy-host-rule-precedence",
        "`AGENTS.md` outranks the prompt in `agy`; when they conflict, the "
        "host rules win and the prompt loses.",
    ),
    "in-a-conflict-follow-host-rules": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the gate prompt; "
        "in a conflict, follow `AGENTS.md`.",
    ),
    "in-a-conflict-do-not-use-the-prompt": (
        "agy-host-rule-precedence",
        "In agy the host AGENTS.md outranks the gate prompt. In a conflict, "
        "however, do not use the prompt instead.",
    ),
    "use-host-rules-rather-than-the-prompt": (
        "agy-host-rule-precedence",
        "When they disagree, `agy` uses `AGENTS.md` rather than the gate prompt.",
    ),
}
AC24_REJECTED = {
    "every-lane-omitting-antigravity": (
        "reviews-rule-on-every-lane",
        "- On every lane, a reviewer that reads a sibling's output under "
        "`reviews/` fails as `out-of-scope-access`.",
    ),
    "claude-and-codex-named": (
        "reviews-rule-on-every-lane",
        "On Claude and Codex, a reviewer that reads `reviews/` fails as "
        "`out-of-scope-access`; `agy` reviews are audited from their stream.",
    ),
    "claude-and-codex-only": (
        "reviews-rule-on-every-lane",
        "Reading `reviews/` fails as `out-of-scope-access` on the Claude and "
        "Codex lanes only, not on Antigravity.",
    ),
    "every-lane-except-antigravity": (
        "reviews-rule-on-every-lane",
        "On every lane except Antigravity, a reviewer that reads `reviews/` "
        "fails as `out-of-scope-access`.",
    ),
    "rule-not-applied-to-agy": (
        "reviews-rule-on-every-lane",
        "The `reviews/` rule applies to all lanes and fails a read as "
        "`out-of-scope-access`, but it does not apply to `agy`.",
    ),
    "prompt-outranks-host-rules": (
        "agy-host-rule-precedence",
        "`agy` loads the host `AGENTS.md`, but the gate prompt outranks those rules.",
    ),
    "prompt-takes-precedence": (
        "agy-host-rule-precedence",
        "In `agy` the gate prompt takes precedence over `AGENTS.md` and other "
        "host rules.",
    ),
    "host-rules-lower-precedence": (
        "agy-host-rule-precedence",
        "When `agy` runs, `AGENTS.md` has lower precedence than the review prompt.",
    ),
    "host-rules-do-not-override": (
        "agy-host-rule-precedence",
        "`agy` reads `AGENTS.md`, yet its rules do not override the gate prompt.",
    ),
    "both-directions": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt. "
        "Where they conflict, the prompt overrides `AGENTS.md`.",
    ),
    "precedence-without-direction": (
        "agy-host-rule-precedence",
        "In `agy`, `AGENTS.md` takes precedence; the prompt is a user message.",
    ),
    "conflict-exception-gives-the-prompt-the-win": (
        "agy-host-rule-precedence",
        "In `agy`, host `AGENTS.md` outranks the gate prompt, except when it "
        "contradicts the prompt, in which case the prompt wins.",
    ),
    "but-the-prompt-wins-a-conflict": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt, but "
        "when they conflict, the prompt wins.",
    ),
    "unless-the-prompt-says-otherwise": (
        "agy-host-rule-precedence",
        "`agy` gives `AGENTS.md` precedence over the gate prompt unless the "
        "prompt says otherwise.",
    ),
    "only-outside-a-conflict": (
        "agy-host-rule-precedence",
        "`AGENTS.md` outranks the prompt in `agy` only when the two do not conflict.",
    ),
    "conflict-follows-the-prompt": (
        "agy-host-rule-precedence",
        "`AGENTS.md` takes precedence over the prompt in `agy`; where the two "
        "conflict, the reviewer follows the prompt.",
    ),
    "host-rules-yield-in-a-conflict": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt, yet "
        "in a conflict the host rules yield.",
    ),
    # The reviewer's text, verbatim: plain precedence, then a directive that
    # hands the conflict to the prompt.
    "in-a-conflict-use-the-prompt-instead": (
        "agy-host-rule-precedence",
        "In agy the host AGENTS.md outranks the gate prompt. In a conflict, "
        "however, use the prompt instead.",
    ),
    "in-a-conflict-follow-the-prompt": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt. In a "
        "conflict, follow the prompt.",
    ),
    "use-the-prompt-instruction-when-they-disagree": (
        "agy-host-rule-precedence",
        "`AGENTS.md` takes precedence over the gate prompt in `agy`; use the "
        "gate prompt's instruction when they disagree.",
    ),
    "prompt-should-be-followed-in-a-conflict": (
        "agy-host-rule-precedence",
        "`agy` loads `AGENTS.md` as user rules that outrank the prompt, but if "
        "they conflict, the prompt should be followed.",
    ),
    "if-in-doubt-defer-to-the-prompt": (
        "agy-host-rule-precedence",
        "In `agy`, `AGENTS.md` overrides the prompt; if in doubt, defer to the prompt.",
    ),
    "give-the-prompt-priority": (
        "agy-host-rule-precedence",
        "`AGENTS.md` outranks the prompt in `agy`, yet reviewers should give "
        "the prompt priority.",
    ),
    "follow-the-prompt-rather-than-host-rules": (
        "agy-host-rule-precedence",
        "`agy` injects `AGENTS.md` as user rules that outrank the prompt; "
        "still, follow the prompt rather than `AGENTS.md`.",
    ),
    "instead-of-host-rules-use-the-prompt": (
        "agy-host-rule-precedence",
        "`AGENTS.md` takes precedence over the prompt in `agy`. Instead of "
        "`AGENTS.md`, reviewers use the prompt.",
    ),
}
# The terms a term-only check accepted; every rejected control holds them.
AC24_TERM_ONLY = {
    "reviews-rule-on-every-lane": (
        ("reviews/",),
        ("out-of-scope-access",),
        ("every lane", "all lanes", "claude and codex"),
    ),
    "agy-host-rule-precedence": (
        LANE_ANCHOR,
        ("agents.md",),
        ("precedence", "outrank", "override"),
    ),
}


@pytest.mark.parametrize("case", list(AC24_ACCEPTED))
def test_ac24_matcher_accepts_the_contract_wording(case: str) -> None:
    """AC-24 matcher control (passes now): text that scopes the reviews/
    rule to every lane with Antigravity, or says AGENTS.md outranks the
    prompt or wins a conflict with it, describes its topic."""
    topic, text = AC24_ACCEPTED[case]
    assert any(_ac24_describes(topic, block) for block in _ac24_split(text)), (
        f"FAIL AC-24: the matcher rejects contract wording for {topic}: {text!r}"
    )


@pytest.mark.parametrize("case", list(AC24_REJECTED))
def test_ac24_matcher_rejects_omitted_lanes_and_reversed_precedence(
    case: str,
) -> None:
    """AC-24 matcher control (passes now): text that omits Antigravity from
    the reviews/ rule, narrows it to Claude and Codex, states agy's
    precedence reversed, negated or without a direction, or hands a conflict
    to the prompt by an exception or a directive describes nothing."""
    topic, text = AC24_REJECTED[case]
    blocks = _ac24_split(text)
    assert any(
        all(any(term in block for term in group) for group in AC24_TERM_ONLY[topic])
        for block in blocks
    ), f"control {case} must hold the terms a term-only check would accept"
    assert not any(_ac24_describes(topic, block) for block in blocks), (
        f"FAIL AC-24: the matcher accepts {case} wording for {topic}: {text!r}"
    )
