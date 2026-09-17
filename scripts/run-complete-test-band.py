#!/usr/bin/env python3
"""Run formal proof for every collected test in one hermetic band."""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import sys
from pathlib import Path

import pytest


class BandCollector:
    def __init__(self) -> None:
        self.nodes: list[str] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.nodes = [item.nodeid for item in session.items]


def _batches(nodes: list[str], *, byte_limit: int = 64 * 1024) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for node in nodes:
        node_size = len(node.encode()) + 1
        if current and size + node_size > byte_limit:
            batches.append(current)
            current = []
            size = 0
        current.append(node)
        size += node_size
    if current:
        batches.append(current)
    return batches


def _proof_targets(nodes: list[str], *, byte_limit: int = 64 * 1024) -> list[str]:
    oversized_files = {
        node.partition("::")[0] for node in nodes if len(node.encode()) + 1 > byte_limit
    }
    targets: list[str] = []
    emitted_files: set[str] = set()
    for node in nodes:
        path = node.partition("::")[0]
        if path not in oversized_files:
            targets.append(node)
        elif path not in emitted_files:
            targets.append(path)
            emitted_files.add(path)
    return targets


def _assert_complete_coverage(nodes: list[str], targets: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for node in nodes:
        if node in seen:
            duplicates.append(node)
        seen.add(node)
    duplicates = sorted(set(duplicates))
    missing = [
        node
        for node in nodes
        if not any(
            target == node or ("::" not in target and node.startswith(target + "::"))
            for target in targets
        )
    ]
    empty_targets = [
        target
        for target in targets
        if not any(
            target == node or ("::" not in target and node.startswith(target + "::"))
            for node in nodes
        )
    ]
    if duplicates or missing or empty_targets:
        raise RuntimeError(
            "complete-band target derivation is not exact: "
            f"duplicates={duplicates!r} missing={missing!r} "
            f"empty_targets={empty_targets!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("band", choices=("fast", "toolchain"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    collector = BandCollector()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        collection = pytest.main(
            ["--collect-only", "-q", "--test-band", args.band],
            plugins=[collector],
        )
    if collection != pytest.ExitCode.OK or not collector.nodes:
        sys.stderr.write(captured.getvalue())
        print(f"ERROR: could not collect complete {args.band} band", file=sys.stderr)
        return int(collection or pytest.ExitCode.NO_TESTS_COLLECTED)
    targets = _proof_targets(collector.nodes)
    try:
        _assert_complete_coverage(collector.nodes, targets)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    batches = _batches(targets)
    for index, nodes in enumerate(batches, start=1):
        print(
            f"complete {args.band} proof batch {index}/{len(batches)} "
            f"({len(nodes)} nodes)"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.proof_runner",
                "--test-band",
                args.band,
                *nodes,
            ],
            cwd=root,
            check=False,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
