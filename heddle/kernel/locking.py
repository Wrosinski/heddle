"""Read-only advisory-lock coordination shared by runtime and gate code."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

LOCKS_DIRNAME = ".gate-locks"
LOCK_IGNORE_BYTES = b"*\n"
LOCK_POLL_SECONDS = 0.1
LOCK_HEARTBEAT_SECONDS = 30.0


@contextmanager
def exclusive_descriptor_lock(
    descriptor: int,
    *,
    on_wait: Callable[[], None],
) -> Iterator[None]:
    """Hold an advisory lock and invoke ``on_wait`` immediately/periodically."""
    next_notice = 0.0
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            now = time.monotonic()
            if now >= next_notice:
                on_wait()
                next_notice = now + LOCK_HEARTBEAT_SECONDS
            time.sleep(LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
