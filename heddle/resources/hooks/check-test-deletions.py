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
cases. A file that does not parse under the running interpreter is checked
by its removed definition lines instead. When a commit concludes a merge,
only tests and files every parent kept count: what one side dropped was
checked where it was dropped. Rebase and cherry-pick replay most commits
without commit hooks, and ``--no-verify`` skips them.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

REMOVED_TEST = re.compile(r"^-\s*(def test_|async def test_|class Test)")
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


def _git(*args: str, ok: Iterable[int] = (0,)) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args], check=False, capture_output=True, timeout=30
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


def _blob(spec: str) -> bytes | None:
    """The blob at ``rev:path`` or ``:path``, or None where it is absent."""
    if _git("rev-parse", "--verify", "--quiet", spec, ok=(0, 1)).returncode:
        return None
    return _git("cat-file", "blob", spec).stdout


def _merge_heads() -> list[str]:
    path = Path(_text(_git("rev-parse", "--git-path", "MERGE_HEAD").stdout).strip())
    try:
        return path.read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise GitError(f"cannot read {path}: {error}") from error


def _renamed_sources() -> set[str]:
    """Paths under the root that Git's rename detection pairs with a new path."""
    fields = _fields("-M")
    sources: set[str] = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        if status[:1] in {"R", "C"}:
            if status[:1] == "R":
                sources.add(fields[index + 1])
            index += 3
        else:
            index += 2
    return sources


def _test_ids(path: str, source: bytes) -> set[str] | None:
    """Path-qualified test ids by lexical class nesting; None if unparsable.

    Functions named ``test*`` and classes named ``Test*`` count wherever only
    classes enclose them; a function's own body holds no test ids.
    """
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return None
    ids: set[str] = set()

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qualified = f"{prefix}::{child.name}"
                if child.name.startswith("Test"):
                    ids.add(qualified)
                visit(child, qualified)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test"):
                    ids.add(f"{prefix}::{child.name}")
            elif not isinstance(child, ast.expr):
                visit(child, prefix)

    visit(tree, path)
    return ids


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


def _removed_lines(path: str) -> list[str]:
    diff = _git(
        "--literal-pathspecs",
        "diff",
        "--cached",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "-U0",
        "--",
        path,
    )
    return [
        line for line in _text(diff.stdout).splitlines() if REMOVED_TEST.match(line)
    ]


def _kept_by_every_parent(
    heads: list[str], removed: set[str], deleted: list[str]
) -> tuple[set[str], list[str]]:
    """The removed ids and deleted files that every merge parent kept."""
    paths = {_name(test_id)[0] for test_id in removed} | set(deleted)
    sources = {
        (head, path): _blob(f"{head}:{path}") for head in heads for path in paths
    }
    ids = {
        key: None if source is None else _test_ids(key[1], source)
        for key, source in sources.items()
    }

    def kept(head: str, test_id: str) -> bool:
        key = (head, _name(test_id)[0])
        held = ids[key]
        # A side that cannot be parsed is taken to keep the test.
        return sources[key] is not None and (held is None or test_id in held)

    return (
        {i for i in removed if all(kept(head, i) for head in heads)},
        [
            path
            for path in deleted
            if all(sources[head, path] is not None for head in heads)
        ],
    )


def _check(message: Path) -> int:
    before: dict[str, set[str]] = {}
    after: dict[str, set[str]] = {}
    deleted: list[str] = []
    unparsed: dict[str, list[str]] = {}
    fields = _fields("--no-renames")
    for index in range(0, len(fields), 2):
        status, path = fields[index], fields[index + 1]
        old = None if status == "A" else _blob(f"HEAD:{path}")
        new = None if status == "D" else _blob(f":{path}")
        old_ids = set() if old is None else _test_ids(path, old)
        new_ids = set() if new is None else _test_ids(path, new)
        if status == "D":
            deleted.append(path)
        if old_ids is None or new_ids is None:
            unparsed[path] = [] if status == "D" else _removed_lines(path)
            continue
        before[path], after[path] = old_ids, new_ids
    removed = {i for path, ids in before.items() for i in ids - after[path]}
    added = {i for path, ids in after.items() for i in ids - before[path]}
    heads = _merge_heads()
    if heads:
        removed, deleted = _kept_by_every_parent(heads, removed, deleted)
    unaccounted, unpaired, conflicting, unmatched = _account(
        removed, added, _renames(message)
    )
    renamed = _renamed_sources() if deleted else set()
    flagged_files = [
        path
        for path in deleted
        if path in unparsed
        or any(i in unaccounted for i in before[path])
        or (not before[path] and path not in renamed)
    ]
    unparsed_lines = {path: lines for path, lines in unparsed.items() if lines}
    python = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    for path in sorted(unparsed):
        print(
            f"note: {path} does not parse under {python}; "
            + (
                "it counts as a deleted test file"
                if path in deleted
                else "its removed definition lines were checked instead, and "
                "trailers do not apply to them"
            )
        )
    if not unaccounted and not flagged_files and not unparsed_lines:
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
    if unparsed_lines:
        print("\nRemoved test definitions in files that do not parse:")
        for path, lines in sorted(unparsed_lines.items()):
            for line in lines:
                print(f"  {path}: {line}")
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
