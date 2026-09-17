"""Formal repository proof: pytest success must include completed item proof."""

from __future__ import annotations

import sys

import pytest

from tests import pytest_policy


class Completion:
    session: pytest.Session | None = None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.session = session

    def succeeded(self) -> bool:
        if self.session is None:
            return False
        proof = self.session.config.stash.get(pytest_policy.PROOF, None)
        return proof is not None and proof.succeeded


def main(args: list[str] | None = None) -> int:
    completion = Completion()
    result = pytest.main(
        ["--strict-proof", *(sys.argv[1:] if args is None else args)],
        plugins=[pytest_policy, completion],
    )
    if result != pytest.ExitCode.OK:
        return int(result)
    if not completion.succeeded():
        print(
            "ERROR: strict proof did not complete; pytest zero exit is not proof.",
            file=sys.stderr,
        )
        return int(pytest.ExitCode.TESTS_FAILED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
