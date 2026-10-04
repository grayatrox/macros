"""One running copy of an app at a time, enforced by a named Windows mutex.

A kernel mutex rather than a PID file: it still works after an app has replaced
its own process (Macros' Reload), and Windows releases it when the holder dies,
so a crash never leaves a stale lock behind.

Standard library only, like the rest of pluginhost. The Win32 calls are
:mod:`pluginhost.named_mutex`'s.
"""

from __future__ import annotations

import time

from pluginhost import named_mutex

POLL_SECONDS = 0.1


def acquire_single_instance(name: str, wait_seconds: float = 0) -> int | None:
    """Claim the named mutex ``name`` for this process.

    Only existence is checked, not ownership: the first process to create the
    name wins, and every later one sees ERROR_ALREADY_EXISTS until the winner
    closes its handle or exits.

    Args:
        name: the mutex name, e.g. ``"Local\\\\my-app"`` (one per app, per session).
        wait_seconds: keep retrying this long before giving up; 0 tries once.
            For a restarted process waiting for its predecessor to exit.

    Returns:
        The handle, which must stay open for the life of the process (Windows
        closes it on exit), or None if another holder still has the name.

    Raises:
        OSError: CreateMutexW itself failed (e.g. an invalid name).
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        handle, existed = named_mutex.create(name)
        if not existed:
            return handle
        named_mutex.close(handle)
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_SECONDS)


def release_single_instance(handle: int) -> None:
    """Give up a handle returned by :func:`acquire_single_instance`.

    Only needed to release the name before the process exits.
    """
    named_mutex.close(handle)
