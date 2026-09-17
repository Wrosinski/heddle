"""Thin CLI wrapper over the engine aggregate renderer.

The single rendering implementation lives in
``heddle.runtime.trajectory.write_aggregate`` (the canonical aggregate writer
— the archival close surface regenerates the same aggregate); this wrapper
keeps the ad-hoc `--since` reporting entry point.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from heddle.runtime.trajectory import parse_iso_datetime, write_aggregate


def main() -> int:
    args = _parse_args()
    write_aggregate(args.trajectories_root, since=args.since)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=_parse_since)
    parser.add_argument(
        "--trajectories-root", type=Path, default=Path("docs/gate-trajectories")
    )
    return parser.parse_args()


def _parse_since(value: str) -> datetime:
    try:
        return parse_iso_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --since {value!r}: expected ISO date or timestamp"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
