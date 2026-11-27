"""The lane-neutral `reviews/` check over one call's context.

A reviewer may read its own run temp directory but no other path under the
feature's ``reviews/`` directory, which holds sibling and earlier review
output. Each runner's parser hands this module one call at a time: the paths
its tool parameters name, the command text it executed (lane wrapper
removed), and what the call showed. The check reports:

- **named** access: a named path, or a word of the command text, resolves to
  ``reviews/`` itself or a path under it, outside the run temp directory;
- **observed** access: a shown search hit or listing entry lies under
  ``reviews/``, outside the run temp directory. Structured entries always
  count. Shell output counts only when every output-producing element of the
  command is a search or listing command (a pipeline counts by its first
  stage), since printed file content is not an entry;
- **ambiguous** context: a relative name needed a directory that is unknown,
  so it grounds no refusal.

Relative names resolve against the call's effective directory: its starting
directory, moved by each parseable ``cd`` list element in the command. ``~``,
``$HOME`` and ``..`` are normalized. A ``cd`` whose target cannot be parsed,
or a relative ``cd`` from an unknown directory, leaves the directory unknown;
a parseable absolute, ``~`` or ``$HOME`` ``cd`` re-establishes it. A search
command's pattern is not a named path.

The shell walker (``simple_commands``, ``directory_after``) and the rule for
which of a command's words name a path (``named_words``) are public so other
audits resolve command text by the same rules.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# Shell commands whose output lists paths: their path-led lines are entries.
SEARCH_COMMANDS = frozenset(
    {"rg", "grep", "egrep", "fgrep", "find", "fd", "fdfind", "ls", "tree"}
)
GIT_SEARCH_SUBCOMMANDS = frozenset({"grep", "ls-files"})
# Search commands whose first operand is a pattern, not a path, unless an
# option supplied the pattern. Common options that take a value are listed so
# their value is not taken for the pattern.
PATTERN_COMMANDS = frozenset({"rg", "grep", "egrep", "fgrep"})
_PATTERN_SUPPLIERS = frozenset({"-e", "--regexp", "-f", "--file", "--files"})
_PATTERN_VALUE_OPTIONS = frozenset({"-e", "--regexp"})
_VALUE_OPTIONS = frozenset(
    {"-A", "-B", "-C", "-m", "-g", "-t", "--glob", "--type", "--max-count"}
)
# Shells whose ``-c`` script is the command a lane actually ran.
SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_OPERATOR_CHARS = frozenset("();<>|&\n")
_BLANKS = frozenset(" \t\r")
_HOME_PREFIX = re.compile(r"^(?:~|\$HOME|\$\{HOME\})(?=/|$)")


@dataclass(frozen=True)
class Word:
    """One shell word after quote removal. An unquoted or double-quoted
    ``~``, ``$HOME`` or ``${HOME}`` prefix is expanded from ``Path.home()``;
    single-quoted text stays literal. ``quoted`` is set when any part of the
    word was quoted or escaped."""

    text: str
    quoted: bool


@dataclass(frozen=True)
class SimpleCommand:
    """One simple command of a list or pipeline: the directory it starts in
    (None when unknown) and its words, redirection operators removed and
    their targets kept."""

    cwd: Path | None
    words: tuple[Word, ...]


@dataclass(frozen=True)
class ScopeResult:
    """One call's verdict. ``directory`` is where the command leaves its
    shell (the starting directory when it has no ``cd``; None when unknown),
    for lanes whose shell keeps its directory between calls."""

    named: bool
    observed: bool
    ambiguous: bool
    directory: Path | None


def reviews_access(
    *,
    reviews_dir: Path,
    temp_dir: Path,
    cwd: Path | None,
    paths: Iterable[str] = (),
    command: str | None = None,
    output: str | None = None,
    entries: Iterable[str] = (),
) -> ScopeResult:
    """Check one call's context against the feature's ``reviews/`` directory.

    ``cwd`` is the call's starting directory, None when unknown; ``paths``
    are named path parameters; ``command`` is executed text with any lane
    wrapper removed; ``output`` is shown shell output; ``entries`` are shown
    structured result entries.
    """
    scope = _Scope(_normalized(reviews_dir), _normalized(temp_dir))
    start = None if cwd is None else _normalized(cwd)
    for name in paths:
        scope.name(_expand_home(name), start)
    for entry in entries:
        scope.show(entry, start)
    directory = start
    if command:
        directory = scope.command(command, start, output)
    return ScopeResult(scope.named, scope.observed, scope.ambiguous, directory)


def simple_commands(command: str, cwd: Path | None) -> list[SimpleCommand]:
    """Each simple command of ``command``'s ``&&``, ``||``, ``;`` or ``|``
    list in order, each with the directory it starts in."""
    return [stage for pipeline in _walk(command, cwd)[0] for stage in pipeline]


def directory_after(command: str, cwd: Path | None) -> Path | None:
    """The directory ``command`` leaves its shell in, None when unknown."""
    return _walk(command, cwd)[1]


def unwrap_shell(command: str) -> str:
    """The script inside a shell wrapper such as ``bash -lc '...'``, or the
    command unchanged when it carries none."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if len(words) < 3 or os.path.basename(words[0]) not in SHELLS:
        return command
    flags, script = words[1:-1], words[-1]
    if any(flag.startswith("-") and "c" in flag for flag in flags) and all(
        flag.startswith("-") for flag in flags
    ):
        return script
    return command


def resolve_name(name: str, cwd: Path | None) -> Path | None:
    """``name``'s normalized location: absolute, ``~`` and ``$HOME`` names
    on their own, relative names against ``cwd``. None when it holds another
    variable or a substitution, or is relative and ``cwd`` is unknown."""
    return _place(_expand_home(name), cwd)


def ambiguous_warning(lane: str, call: str) -> str:
    """The gate-log line for a relative name in an unknown directory."""
    return (
        f"ReviewScope: ambiguous context on the {lane} lane: {call} names a "
        "relative path in an unknown directory, so the reviews/ check could "
        "not place it"
    )


def unrecognized_warning(lane: str, kind: str) -> str:
    """The gate-log line for a call whose result is in no recognized shape."""
    return (
        f"ReviewScope: unrecognized result shape on the {lane} lane for a "
        f"{kind} call; its shown entries were not inspected"
    )


class _Scope:
    def __init__(self, reviews: Path, temp: Path) -> None:
        self.reviews = reviews
        self.temp = temp
        self.named = False
        self.observed = False
        self.ambiguous = False

    def _allowed(self, location: Path) -> bool:
        return _within(location, self.temp) or not _within(location, self.reviews)

    def name(self, text: str, cwd: Path | None) -> None:
        """A named path, its home prefix already expanded."""
        location = self._locate(text, cwd)
        if location is not None and not self._allowed(location):
            self.named = True

    def show(self, line: str, base: Path | None) -> None:
        """A shown entry: the line, or its path-led prefix before a colon."""
        text = line.strip()
        if not text:
            return
        location = self._locate(_expand_home(text.split(":", 1)[0]), base)
        if (
            location is not None
            and location != self.reviews
            and not self._allowed(location)
        ):
            self.observed = True

    def _locate(self, text: str, cwd: Path | None) -> Path | None:
        location = _place(text, cwd)
        if location is None and cwd is None and _placeable_relative(text):
            self.ambiguous = True
        return location

    def command(
        self, command: str, cwd: Path | None, output: str | None
    ) -> Path | None:
        pipelines, directory = _walk(command, cwd)
        bases: list[Path | None] = []
        listing = False
        scanned = True
        for pipeline in pipelines:
            first = pipeline[0]
            if len(pipeline) == 1 and _is_cd(first):
                target = _cd_target(first)
                if target is not None:
                    self.name(target.text, first.cwd)
                continue
            for stage in pipeline:
                for word in named_words(stage.words):
                    self.name(word.text, stage.cwd)
            kind = _search_kind([word.text for word in first.words])
            if kind is None:
                scanned = False
            else:
                bases.append(first.cwd)
                listing = listing or kind == "ls"
        if output and scanned and bases:
            for line, base in _shown_lines(output, bases, listing):
                self.show(line, base)
        return directory


def _walk(
    command: str, cwd: Path | None
) -> tuple[list[tuple[SimpleCommand, ...]], Path | None]:
    """The command's pipelines in order, each simple command carrying the
    directory it starts in, and the directory the shell is left in. Only a
    ``cd`` that is a whole list element moves the directory."""
    directory = None if cwd is None else _normalized(cwd)
    pipelines: list[tuple[SimpleCommand, ...]] = []
    for stages in _pipelines(_lex(command)):
        pipeline = tuple(SimpleCommand(directory, words) for words in stages)
        pipelines.append(pipeline)
        if len(pipeline) == 1 and _is_cd(pipeline[0]):
            directory = _changed(_cd_target(pipeline[0]), directory)
    return pipelines, directory


def _lex(command: str) -> list[Word | str]:
    """Shell words and operator tokens (plain strings: runs of ``();<>|&``
    and newlines). Quotes and backslashes are removed; a ``#`` that starts a
    word comments out the rest of its line."""
    tokens: list[Word | str] = []
    chars: list[str] = []
    in_word = quoted = literal_head = False
    quote: str | None = None
    in_operator = False
    index = 0

    def end_word() -> None:
        nonlocal in_word, quoted, literal_head
        if in_word:
            text = "".join(chars)
            tokens.append(Word(text if literal_head else _expand_home(text), quoted))
        chars.clear()
        in_word = quoted = literal_head = False

    while index < len(command):
        char = command[index]
        index += 1
        if quote == "'":
            if char == "'":
                quote = None
            else:
                chars.append(char)
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char == "\\" and command[index : index + 1] in {'"', "\\", "$", "`"}:
                chars.append(command[index])
                index += 1
            else:
                chars.append(char)
            continue
        if char in _OPERATOR_CHARS:
            end_word()
            if in_operator:
                tokens[-1] = f"{tokens[-1]}{char}"
            else:
                tokens.append(char)
            in_operator = True
            continue
        in_operator = False
        if char in _BLANKS:
            end_word()
        elif char == "#" and not in_word:
            while index < len(command) and command[index] != "\n":
                index += 1
        elif char in "'\"":
            if not in_word:
                literal_head = char == "'"
            in_word = quoted = True
            quote = char
        elif char == "\\":
            if command[index : index + 1] not in {"", "\n"}:
                chars.append(command[index])
                in_word = quoted = True
            index += 1
        else:
            chars.append(char)
            in_word = True
    end_word()
    return tokens


def _pipelines(tokens: list[Word | str]) -> Iterator[list[tuple[Word, ...]]]:
    """List elements (split at ``&&``, ``||``, ``;``, ``&``, newlines and
    parentheses), each as its non-empty pipeline stages. Redirection
    operators are dropped; their targets stay as words."""
    stages: list[list[Word]] = [[]]
    for token in tokens:
        if isinstance(token, Word):
            stages[-1].append(token)
        elif token in {"|", "|&"}:
            stages.append([])
        elif not set(token) & {"<", ">"}:
            kept = [tuple(stage) for stage in stages if stage]
            if kept:
                yield kept
            stages = [[]]
    kept = [tuple(stage) for stage in stages if stage]
    if kept:
        yield kept


def _is_cd(command: SimpleCommand) -> bool:
    return bool(command.words) and command.words[0].text == "cd"


def _cd_target(command: SimpleCommand) -> Word | None:
    """The ``cd`` target: its first non-flag word, or ``-``."""
    return next(
        (
            word
            for word in command.words[1:]
            if word.text == "-" or not word.text.startswith("-")
        ),
        None,
    )


def named_words(words: tuple[Word, ...]) -> Iterator[Word]:
    """The words that may name a path: arguments and redirection targets,
    not flags, a bare command word or a search pattern."""
    unnamed = _search_operands([word.text for word in words])
    for index, word in enumerate(words):
        if index in unnamed:
            continue
        if index == 0 and "/" not in word.text:
            continue
        if word.text.startswith("-"):
            continue
        yield word


def _search_operands(words: list[str]) -> set[int]:
    """Indexes of a search command's pattern and leading option values, which
    name no path. The first operand of ``rg``, ``grep``, ``egrep``, ``fgrep``
    or ``git grep`` is its pattern unless ``-e``, ``-f`` (or their long
    forms) supplied it, or ``rg --files`` takes none."""
    if not words:
        return set()
    program = os.path.basename(words[0])
    start = 1
    if program == "git":
        start = next((i for i, w in enumerate(words) if i and w[:1] != "-"), 0) + 1
        if start == 1 or words[start - 1] != "grep":
            return set()
    elif program not in PATTERN_COMMANDS:
        return set()
    unnamed: set[int] = set()
    supplied = False
    index = start
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        if word in _PATTERN_VALUE_OPTIONS or word in _VALUE_OPTIONS:
            unnamed.add(index + 1)
            index += 1
        elif not word.startswith("-"):
            break
        supplied = (
            supplied
            or word.split("=", 1)[0] in _PATTERN_SUPPLIERS
            or word[:2] in {"-e", "-f"}
        )
        index += 1
    if index < len(words) and not supplied:
        unnamed.add(index)
    return unnamed


def _search_kind(words: list[str]) -> str | None:
    """The search or listing command that leads ``words``, or None."""
    if not words:
        return None
    program = os.path.basename(words[0])
    if program in SEARCH_COMMANDS:
        return program
    if program == "git":
        subcommand = next((w for w in words[1:] if not w.startswith("-")), None)
        if subcommand in GIT_SEARCH_SUBCOMMANDS:
            return f"git {subcommand}"
    return None


def _changed(target: Word | None, directory: Path | None) -> Path | None:
    """The directory after ``cd target``: unknown for an unparseable target,
    ``cd -``, or a relative one from an unknown directory."""
    if target is None:
        return _normalized(Path.home())
    if target.text == "-":
        return None
    return _place(target.text, directory)


def _shown_lines(
    output: str, bases: list[Path | None], listing: bool
) -> Iterator[tuple[str, Path | None]]:
    """Each shown line with the directory it resolves against. In a listing,
    a ``dir:`` header (``ls -R``) moves the base for the names under it."""
    header: list[Path | None] | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if listing and line.endswith(":"):
            header = [resolve_name(line[:-1], base) for base in bases]
            continue
        for base in header if header is not None else bases:
            yield line, base


def _normalized(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(path)))


def _within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _expand_home(text: str) -> str:
    match = _HOME_PREFIX.match(text)
    if match:
        return str(Path.home()) + text[match.end() :]
    return text


def _place(text: str, cwd: Path | None) -> Path | None:
    """The normalized location of already-expanded ``text``: None when it
    holds a variable or substitution, or is relative and ``cwd`` is
    unknown."""
    if not text or "$" in text or "`" in text:
        return None
    if os.path.isabs(text):
        return Path(os.path.normpath(text))
    if cwd is None:
        return None
    return Path(os.path.normpath(os.path.join(cwd, text)))


def _placeable_relative(text: str) -> bool:
    """A relative name that a known directory would place."""
    return (
        bool(text) and "$" not in text and "`" not in text and not os.path.isabs(text)
    )
