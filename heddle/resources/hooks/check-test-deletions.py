#!/usr/bin/env python3
"""Block staged test deletions without an explicit bypass.

Packaged heddle workflow hook (stdlib-only, standalone-executable,
commit-msg stage: it reads the message for rename trailers). Removing a test
must be deliberate. The hook compares test ids, ``path::[Class::...]name`` by
lexical class nesting, between HEAD and the index for the staged Python files
under the tests root:

- a test that moves to another file under the same name passes;
- a rename passes with a ``Test-Renamed: <old id> -> <new id>`` trailer,
  where the old id leaves this commit and the new id enters it; a class
  rename covers the methods it keeps;
- any other removed test, and a deleted file under the root unless every
  test in it moved or was renamed, needs ``ALLOW_TEST_DELETION=1`` (paired
  with the ``Bypass-Justification:`` trailer contract).

The census proves definitions are kept, not pytest collection or parameter
cases. A file that does not parse is checked by its removed definition lines
instead. A commit concluding a merge is skipped: the merged branch's
deletions were checked where they were made. Git's sequencer (rebase,
cherry-pick) and ``--no-verify`` skip commit hooks altogether.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

REMOVED_TEST = re.compile(r"^-\s*(def test_|async def test_|class Test)")
RENAMED = re.compile(r"^Test-Renamed:\s*(\S.*?)\s+->\s+(\S.*?)\s*$")


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


def _has_head() -> bool:
    return (
        _git("rev-parse", "--verify", "--quiet", "HEAD^{commit}", ok=(0, 1)).returncode
        == 0
    )


def _merging() -> bool:
    path = _text(_git("rev-parse", "--git-path", "MERGE_HEAD").stdout).strip()
    return os.path.exists(path)


def _staged_changes() -> list[tuple[str, str]]:
    """(status, path) per staged Python file under the root; renames split."""
    raw = _git(
        "diff", "--cached", "--name-status", "--no-renames", "-z", "--", PATHSPEC
    ).stdout
    fields = _text(raw).split("\0")[:-1]
    return list(zip(fields[::2], fields[1::2], strict=True))


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


def _account(
    removed: set[str], added: set[str], renames: list[tuple[str, str]]
) -> tuple[set[str], set[str], list[tuple[str, str]]]:
    """Removed and added ids no rename or move pairs, and the unused trailers.

    A rename pairs one removed id with one added id, and a class rename
    covers the members it keeps; an entry naming an id twice, or an id this
    commit does not remove or add, accounts for nothing. A move pairs a
    removed id with the same name added in another file.
    """
    removed, added = set(removed), set(added)
    olds = [old for old, _ in renames]
    news = [new for _, new in renames]
    unused: list[tuple[str, str]] = []
    for old, new in renames:
        if olds.count(old) > 1 or news.count(new) > 1:
            unused.append((old, new))
        elif old in removed and new in added:
            removed.discard(old)
            added.discard(new)
            for member in sorted(r for r in removed if r.startswith(f"{old}::")):
                renamed = new + member[len(old) :]
                if renamed in added:
                    removed.discard(member)
                    added.discard(renamed)
        else:
            unused.append((old, new))
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
    return removed, added, unused


def _renames(message: Path) -> list[tuple[str, str]]:
    try:
        text = message.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [
        (match.group(1), match.group(2))
        for line in text.splitlines()
        if (match := RENAMED.match(line))
    ]


def _removed_lines(path: str) -> list[str]:
    diff = _text(_git("diff", "--cached", "-U0", "--", path).stdout)
    return [line for line in diff.splitlines() if REMOVED_TEST.match(line)]


def _check(message: Path) -> int:
    if _merging() or not _has_head():
        return 0
    before: dict[str, set[str]] = {}
    after: dict[str, set[str]] = {}
    deleted: list[str] = []
    unparsed: dict[str, list[str]] = {}
    for status, path in _staged_changes():
        old = None if status == "A" else _git("cat-file", "blob", f"HEAD:{path}").stdout
        new = None if status == "D" else _git("cat-file", "blob", f":{path}").stdout
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
    unaccounted, unpaired, unused = _account(removed, added, _renames(message))
    flagged_files = [
        path
        for path in deleted
        if path in unparsed
        or not before[path]
        or any(i in unaccounted for i in before[path])
    ]
    unparsed_lines = {path: lines for path, lines in unparsed.items() if lines}
    for path in sorted(unparsed):
        print(f"note: {path} does not parse; checked its removed definition lines")
    if not unaccounted and not flagged_files and not unparsed_lines:
        return 0

    print("ERROR: Test deletions detected.")
    if unaccounted:
        print("\nRemoved tests:")
        for test_id in sorted(unaccounted):
            print(f"  {test_id}")
    if flagged_files:
        print("\nDeleted test files:")
        for path in flagged_files:
            print(f"  {path}")
    if unparsed_lines:
        print("\nRemoved test definitions in files that do not parse:")
        for path, lines in sorted(unparsed_lines.items()):
            for line in lines:
                print(f"  {path}: {line}")
    if unaccounted and unpaired:
        print("\nAdded tests:")
        for test_id in sorted(unpaired):
            print(f"  {test_id}")
    print(
        "\nA test moved to another file under its own name needs nothing. For a "
        "rename, add one trailer per test (a class rename covers the methods it "
        "keeps):\n  Test-Renamed: <old id> -> <new id>"
    )
    if unused:
        print("Trailers that matched no removed and added pair:")
        for old_id, new_id in unused:
            print(f"  Test-Renamed: {old_id} -> {new_id}")
    print(
        "To allow a deletion, set ALLOW_TEST_DELETION=1 with a "
        "Bypass-Justification: trailer."
    )
    return 1


def main(argv: list[str]) -> int:
    if os.environ.get("ALLOW_TEST_DELETION", "0") == "1":
        return 0
    if len(argv) != 1:
        print(f"Usage: {Path(sys.argv[0]).name} <commit-msg-file>")
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
