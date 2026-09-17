"""Pure byte-navigation contracts; no source discovery, provider or workflow."""

import hashlib
import json

import pytest

from heddle.gate.input_navigation import navigation_index


def assert_partition(text, index):
    raw = text.encode("utf-8")
    assert index["bytes"] == len(raw)
    assert index["sha256"] == hashlib.sha256(raw).hexdigest()
    cursor, chunks = 0, []
    for row in index["ranges"]:
        assert row["start"] == cursor < row["end"] <= len(raw)
        assert row["end"] - row["start"] <= 32_768
        chunk = raw[row["start"] : row["end"]]
        assert chunk.decode("utf-8").encode("utf-8") == chunk
        chunks.append(chunk)
        cursor = row["end"]
    assert cursor == len(raw) and b"".join(chunks) == raw
    assert sorted(index["read_order"]) == list(range(len(chunks)))
    # Reading out of source order does not lose, duplicate or mutate a slice.
    read = {i: chunks[i] for i in index["read_order"]}
    assert b"".join(read[i] for i in sorted(read)) == raw


def framed(diff, *, prefix="Instructions and required context.\n"):
    return (
        prefix
        + "\n===== BEGIN AUTHORITATIVE DIFF =====\n"
        + diff
        + "\n===== END AUTHORITATIVE DIFF ====="
    )


@pytest.mark.parametrize("text", ["", "one line", "é🚀\r\n" * 20_000, "x" * 100_001])
def test_fallback_partitions_every_utf8_byte_including_huge_lines(text):
    encoded = navigation_index(text, "")
    assert navigation_index(text, "") == encoded
    index = json.loads(encoded)
    assert index["classification"] == "source-order-fallback"
    assert index["read_order"] == list(range(len(index["ranges"])))
    assert_partition(text, index)


def test_terminal_diff_navigation_reads_implementation_before_retained_evidence():
    diff = (
        "diff --git a/docs/proposals/evidence/history.json "
        "b/docs/proposals/evidence/history.json\n"
        "--- a/docs/proposals/evidence/history.json\n"
        "+++ b/docs/proposals/evidence/history.json\n"
        "@@ -1 +1 @@\n"
        "+" + "é🚀" * 20_000 + "\n"
        "+===== BEGIN AUTHORITATIVE DIFF =====\n"
        "+diff --git a/fake.py b/fake.py\n"
        "diff --git a/docs/workflow/rules.md b/docs/workflow/rules.md\n"
        "+Required delivered guidance.\n"
        "diff --git a/heddle/core.py b/heddle/core.py\n"
        "+answer = 42\n"
        "diff --git a/tests/test_core.py b/tests/test_core.py\n"
        "+assert answer == 42"
    )
    # An earlier embedded example has matching framing, but is not the terminal
    # native diff. The prefix itself is still completely accounted for.
    text = framed(diff, prefix=framed("diff --git a/old.py b/old.py\n+old") + "\n")
    index = json.loads(navigation_index(text, diff))
    assert index["classification"] == "terminal-authoritative-diff"
    assert_partition(text, index)
    ordered = [index["ranges"][i] for i in index["read_order"]]
    kinds = list(dict.fromkeys(row["kind"] for row in ordered))
    assert kinds == ["context", "implementation", "tests", "guidance", "evidence"]
    assert {row["path"] for row in ordered if row["path"]} == {
        "docs/proposals/evidence/history.json",
        "docs/workflow/rules.md",
        "heddle/core.py",
        "tests/test_core.py",
    }


@pytest.mark.parametrize("fault", ["suffix", "headers", "nested-marker", "no-header"])
def test_ambiguous_or_mismatched_diff_never_selects_an_arbitrary_region(fault):
    diff = "diff --git a/core.py b/core.py\n+changed"
    text = framed(diff)
    if fault == "suffix":
        text += "\nAnother runtime section"
    elif fault == "headers":
        diff = "diff --git a/another.py b/another.py\n+other"
    elif fault == "nested-marker":
        diff += "\n===== BEGIN AUTHORITATIVE DIFF =====\nnot a diff"
        text = framed(diff)
    else:
        diff = "No diff headers"
        text = framed(diff)
    index = json.loads(navigation_index(text, diff))
    assert index["classification"] == "source-order-fallback"
    assert_partition(text, index)


@pytest.mark.parametrize(
    ("header", "path"),
    [
        ("diff --git a/src/with spaces.py b/src/with spaces.py", "src/with spaces.py"),
        ('diff --git "a/src/café.py" "b/src/café.py"', "src/café.py"),
        ('diff --git "a/src/a\\tname.py" "b/src/a\\tname.py"', "src/a\tname.py"),
        (r'diff --git "a/src/\303\251.py" "b/src/\303\251.py"', None),
        ("diff --git a/old.py b/new.py", "new.py"),
        ("diff --git unknown header", None),
        ("diff --git a/src/a b/ambiguous.py b/src/a b/ambiguous.py", None),
    ],
)
def test_path_labels_do_not_change_byte_ownership(header, path):
    diff = header + "\r\nBinary files differ"
    text = framed(diff)
    index = json.loads(navigation_index(text, diff))
    assert_partition(text, index)
    changes = [row for row in index["ranges"] if row["kind"] != "context"]
    assert len(changes) == 1 and changes[0]["path"] == path
    assert changes[0]["kind"] == ("unclassified" if path is None else "implementation")
