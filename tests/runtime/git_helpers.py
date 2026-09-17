"""Shared Git-repository setup for runtime test fixtures."""

from __future__ import annotations

from collections.abc import Callable


def initialize_git_repo(
    run_git: Callable[..., object],
    *,
    email: str,
    name: str,
    add_args: tuple[str, ...] = ("-A",),
    message: str = "fixture baseline",
) -> None:
    """Initialize and commit a fixture through the caller's Git adapter."""
    for args in (
        ("init", "-q"),
        ("config", "user.email", email),
        ("config", "user.name", name),
        ("add", *add_args),
        ("commit", "-qm", message),
    ):
        run_git(*args)
