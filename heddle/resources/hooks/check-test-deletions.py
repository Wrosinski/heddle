#!/usr/bin/env python3
"""Block staged test deletions without an explicit bypass.

Packaged heddle workflow hook (stdlib-only, standalone-executable,
commit-msg stage: it reads the message for rename trailers). Removing a test
must be deliberate. The hook compares test ids, ``path::[Class::...]name`` by
lexical class nesting, between HEAD and the index for the staged Python files
under the tests root:

- a test that moves to another file under the same name passes;
- a rename passes with a ``Test-Renamed: <old id> -> <new id>`` trailer in
  the message's trailer block, where the old id leaves this commit and the
  new id enters it; a class rename covers the members it keeps, and a class
  whose every test moved or was renamed passes;
- a deleted file under the root passes when every test in it moved or was
  renamed, or, for a file without tests, when Git detects it as renamed;
- anything else needs ``ALLOW_TEST_DELETION=1`` (paired with the
  ``Bypass-Justification:`` trailer contract).

The census proves definitions are kept, not pytest collection or parameter
cases. It reads tokens, so strings and comments hold no tests and syntax
newer than the running interpreter still reads; a file that does not
tokenize holds none. When a commit concludes a merge, every parent counts: a
test any parent has must stay unless some parent lacks it that one of its
merge bases with HEAD had, a deletion checked where it was made, and only a
test or file no parent had can be a move or rename target. A file only a
merged-in head had is checked through its tests. Rebase and cherry-pick
replay most commits without commit hooks, and ``--no-verify`` skips them.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tokenize
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

RENAMED = re.compile(r"^Test-Renamed:\s*(\S.*?)\s+->\s+(\S.*?)\s*$", re.IGNORECASE)


def _param(template: str) -> str:
    # A placeholder token ({{ ... }}) resolves to its default when unrendered;
    # a host adopting the hook replaces the whole token with its own value.
    if template.startswith("{{") and template.endswith("}}"):
        return template[2:-2].partition("|")[2]
    return template


TESTS_ROOT = _param("{{layout_tests|tests}}")
PATHSPEC = f":(glob){TESTS_ROOT}/**/*.py"


class GitError(Exception):
    """A git call failed, so the staged change cannot be read."""


def _git(
    *args: str, ok: Iterable[int] = (0,), stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args], input=stdin, check=False, capture_output=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git {' '.join(args)}: {error}") from error
    if result.returncode not in ok:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} exited {result.returncode}: {detail}")
    return result


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _fields(*args: str) -> list[str]:
    """The NUL-separated fields of a ``git diff --cached -z`` listing."""
    raw = _git("diff", "--cached", "--name-status", "-z", *args, "--", PATHSPEC)
    return _text(raw.stdout).split("\0")[:-1]


def _statuses(rev: str | None) -> dict[str, str]:
    """Each path under the root the index changes from ``rev`` (HEAD if None)."""
    fields = _fields("--no-renames", *([rev] if rev else []))
    return {fields[index + 1]: fields[index] for index in range(0, len(fields), 2)}


def _blobs(specs: list[str]) -> dict[str, bytes | None]:
    """The blob at each ``rev:path`` or ``:path``, or None where it is absent."""
    if not specs:
        return {}
    if any("\n" in spec for spec in specs):
        raise GitError("a path under the tests root contains a newline")
    batch = "".join(f"{spec}\n" for spec in specs).encode("utf-8", "surrogateescape")
    raw = _git("cat-file", "--batch", stdin=batch).stdout
    blobs: dict[str, bytes | None] = {}
    at = 0
    for spec in specs:
        end = raw.index(b"\n", at)
        header, at = raw[at:end], end + 1
        if header.endswith((b" missing", b" ambiguous")):
            blobs[spec] = None
            continue
        _oid, kind, size = header.split(b" ")
        blobs[spec] = raw[at : at + int(size)] if kind == b"blob" else None
        at += int(size) + 1
    return blobs


def _bases(head: str) -> list[str]:
    """The merge bases of HEAD and ``head``; none for unrelated histories."""
    return _text(_git("merge-base", "--all", "HEAD", head, ok=(0, 1)).stdout).split()


def _merge_heads() -> list[str]:
    path = Path(_text(_git("rev-parse", "--git-path", "MERGE_HEAD").stdout).strip())
    try:
        return path.read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise GitError(f"cannot read {path}: {error}") from error


def _renames_detected() -> list[tuple[str, str]]:
    """(source, destination) per rename Git's detection finds under the root."""
    fields = _fields("-M")
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        if status[:1] in {"R", "C"}:
            if status[:1] == "R":
                pairs.append((fields[index + 1], fields[index + 2]))
            index += 3
        else:
            index += 2
    return pairs


def _test_ids(path: str, source: bytes) -> set[str] | None:
    """Path-qualified test ids by lexical class nesting; None if untokenizable.

    Functions named ``test*`` and classes named ``Test*`` count wherever only
    classes enclose them; a function's own body holds no test ids. Each
    logical line that begins a definition names it, and an indented block
    takes the prefix its header line opens.
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(source).readline))
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    skipped = {tokenize.ENCODING, tokenize.NL, tokenize.COMMENT}
    ids: set[str] = set()
    blocks: list[str | None] = [path]  # the prefix in each open block, None in a def
    opens: str | None = path
    words: list[str] = []
    for token in tokens:
        if token.type == tokenize.INDENT:
            blocks.append(opens)
        elif token.type == tokenize.DEDENT:
            blocks.pop()
        elif token.type == tokenize.NEWLINE:
            opens = _header(words, blocks[-1], ids)
            words = []
        elif token.type not in skipped and len(words) < 3:
            words.append(token.string)
    return ids


def _header(words: list[str], prefix: str | None, ids: set[str]) -> str | None:
    """Record a definition a logical line begins; the prefix its block opens."""
    if words[:1] == ["async"]:
        words = words[1:]
    if prefix is None or len(words) < 2 or words[0] not in {"def", "class"}:
        return prefix
    kind, name = words[0], words[1]
    if name.startswith("test" if kind == "def" else "Test"):
        ids.add(f"{prefix}::{name}")
    return None if kind == "def" else f"{prefix}::{name}"


def _name(test_id: str) -> tuple[str, str]:
    path, _, name = test_id.partition("::")
    return path, name


def _covers(prefix: str, ids: set[str]) -> bool:
    return prefix in ids or any(i.startswith(f"{prefix}::") for i in ids)


def _account(
    removed: set[str], added: set[str], renames: list[tuple[str, str]]
) -> tuple[set[str], set[str], list[tuple[str, str]], list[tuple[str, str]]]:
    """Ids no rename or move pairs, and the conflicting and unmatched trailers.

    A trailer entry applies when its old id, or a class holding removed ids,
    leaves this commit and its new one enters it. Entries that share an
    endpoint conflict and pair nothing. Exact pairs are taken first, then a
    class rename pairs the members it keeps. A move pairs a removed id with
    the same name added in another file. A removed class whose every member
    is paired was restructured, not deleted.
    """
    original = set(removed)
    removed, added = set(removed), set(added)
    entries = list(dict.fromkeys(renames))
    candidates = [
        (old, new)
        for old, new in entries
        if _covers(old, removed) and _covers(new, added)
    ]
    olds = Counter(old for old, _ in candidates)
    news = Counter(new for _, new in candidates)
    valid = [(old, new) for old, new in candidates if olds[old] == news[new] == 1]
    conflicting = [entry for entry in candidates if entry not in valid]
    unmatched = [entry for entry in entries if entry not in candidates]
    for old, new in valid:
        if old in removed and new in added:
            removed.discard(old)
            added.discard(new)
    for old, new in valid:
        for member in sorted(r for r in removed if r.startswith(f"{old}::")):
            renamed = new + member[len(old) :]
            if renamed in added:
                removed.discard(member)
                added.discard(renamed)
    for gone in sorted(removed):
        path, name = _name(gone)
        moved = next(
            (
                new
                for new in sorted(added)
                if _name(new)[1] == name and _name(new)[0] != path
            ),
            None,
        )
        if moved is not None:
            removed.discard(gone)
            added.discard(moved)
    for gone in sorted(removed, key=lambda test_id: -test_id.count("::")):
        members = [i for i in original if i.startswith(f"{gone}::")]
        if members and not any(member in removed for member in members):
            removed.discard(gone)
    return removed, added, conflicting, unmatched


def _renames(message: Path) -> list[tuple[str, str]]:
    """``Test-Renamed`` entries in the trailer block Git will commit."""
    parsed = _text(_git("interpret-trailers", "--parse", str(message)).stdout)
    return [
        (match.group(1), match.group(2))
        for line in parsed.splitlines()
        if (match := RENAMED.match(line))
    ]


class _Census:
    """Test ids at the index and each revision it is compared with, read once.

    ``parents`` maps HEAD, and each head a merge joins, to the merge bases it
    descends from; every base descends to HEAD too. ``""`` names the index.
    """

    def __init__(self, heads: list[str]) -> None:
        own = {head: _bases(head) for head in heads}
        every = [base for bases in own.values() for base in bases]
        self.parents: dict[str, list[str]] = {"HEAD": every, **own}
        self.unreadable: set[tuple[str, str]] = set()
        self._sources: dict[str, bytes | None] = {}
        self._ids: dict[tuple[str, str], set[str]] = {}

    def read(self, revs: Iterable[str], paths: Iterable[str]) -> None:
        """Read every ``rev:path`` blob not yet read in one git call."""
        paths = list(paths)
        specs = [f"{rev}:{path}" for rev in revs for path in paths]
        self._sources.update(_blobs([s for s in specs if s not in self._sources]))

    def source(self, rev: str, path: str) -> bytes | None:
        self.read([rev], [path])
        return self._sources[f"{rev}:{path}"]

    def ids(self, rev: str, path: str) -> set[str]:
        if (rev, path) not in self._ids:
            source = self.source(rev, path)
            found = set() if source is None else _test_ids(path, source)
            if found is None:
                self.unreadable.add((rev, path))
            self._ids[rev, path] = found or set()
        return self._ids[rev, path]

    def held(self, path: str) -> set[str]:
        """The test ids any parent has in ``path``."""
        return set().union(*(self.ids(parent, path) for parent in self.parents))

    def deleted(self, test_id: str) -> bool:
        """Whether a parent lacks the test that a merge base it descends from had."""
        path = _name(test_id)[0]
        return any(
            test_id not in self.ids(parent, path)
            and any(test_id in self.ids(base, path) for base in bases)
            for parent, bases in self.parents.items()
        )

    def deleted_file(self, path: str) -> bool:
        return any(
            self.source(parent, path) is None
            and any(self.source(base, path) is not None for base in bases)
            for parent, bases in self.parents.items()
        )


def _check(message: Path) -> int:
    heads = _merge_heads()
    census = _Census(heads)
    statuses = _statuses(None)
    paths = sorted(set(statuses).union(*(_statuses(head) for head in heads)))
    census.read(["", *census.parents], paths)
    removed: set[str] = set()
    added: set[str] = set()
    for path in paths:
        held, staged = census.held(path), census.ids("", path)
        removed |= held - staged
        added |= staged - held
    ancestors = {base for bases in census.parents.values() for base in bases}
    census.read(ancestors, {_name(test_id)[0] for test_id in removed})
    removed = {test_id for test_id in removed if not census.deleted(test_id)}
    deleted = [
        path
        for path, status in statuses.items()
        if status == "D" and not census.deleted_file(path)
    ]
    unaccounted, unpaired, conflicting, unmatched = _account(
        removed, added, _renames(message)
    )
    renamed = {
        source
        for source, destination in (_renames_detected() if deleted else [])
        if all(census.source(parent, destination) is None for parent in census.parents)
    }
    flagged_files = [
        path
        for path in deleted
        if any(test_id in unaccounted for test_id in census.ids("HEAD", path))
        or (not census.ids("HEAD", path) and path not in renamed)
    ]
    python = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    for rev, path in sorted(census.unreadable, key=lambda pair: pair[::-1]):
        where = {"": "staged", "HEAD": "at HEAD"}.get(rev, f"at {rev[:12]}")
        print(
            f"note: {path} {where} does not tokenize under {python}, "
            "so it holds no tests here"
        )
    if not unaccounted and not flagged_files:
        return 0

    print("ERROR: Test deletions detected.")
    if unaccounted:
        print("\nRemoved tests:")
        for test_id in sorted(unaccounted):
            print(f"  {test_id}")
        if unpaired:
            print("\nAdded tests:")
            for test_id in sorted(unpaired):
                print(f"  {test_id}")
        print(
            "\nA test moved to another file under its own name needs nothing. "
            "For a rename, add one trailer per test to the message's trailer "
            "block (a class rename covers the members it keeps):\n"
            "  Test-Renamed: <old id> -> <new id>"
        )
    for label, entries in (
        ("Trailers that name an id another trailer also names:", conflicting),
        ("Trailers that match no removed and added test:", unmatched),
    ):
        if entries and unaccounted:
            print(label)
            for old_id, new_id in entries:
                print(f"  Test-Renamed: {old_id} -> {new_id}")
    if flagged_files:
        print("\nDeleted test files:")
        for path in flagged_files:
            print(f"  {path}")
        print(
            "A deleted file passes when every test in it moved or was renamed, "
            "or, for a file without tests, when Git detects it as renamed."
        )
    print(
        "\nTo allow a deletion, set ALLOW_TEST_DELETION=1 with a "
        "Bypass-Justification: trailer."
    )
    return 1


def main(argv: list[str]) -> int:
    if os.environ.get("ALLOW_TEST_DELETION", "0") == "1":
        return 0
    if len(argv) != 1:
        print(
            f"Usage: {Path(sys.argv[0]).name} <commit-msg-file> (wire it at the "
            "commit-msg stage, which passes the message path)"
        )
        return 1
    try:
        return _check(Path(argv[0]))
    except GitError as error:
        print(
            f"ERROR: the test-deletion check could not read the staged change: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
