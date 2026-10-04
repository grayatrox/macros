"""The Win32 named-mutex calls pluginhost uses, declared once (OP #703).

Only the bindings live here, not a policy. Two callers use a named mutex in
different ways: :mod:`pluginhost.single_instance` asks only whether the name
already exists and never owns it, while :mod:`pluginhost.bootstrap` waits to
own its install lock, with a deadline and a Cancel button. Each keeps its
own policy. What they share is the ctypes signatures, and a missing
``restype = HANDLE`` would silently truncate 64-bit handles.

Standard library only: bootstrap imports this before any dependency exists.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

ERROR_ALREADY_EXISTS = 183
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


def create(name: str) -> tuple[int, bool]:
    """Open the named mutex ``name``, creating it if needed, without owning it.

    Returns:
        ``(handle, existed)``. ``existed`` is True when another handle already
        had the name. The handle must be given to :func:`close`.

    Raises:
        OSError: CreateMutexW failed (e.g. a namespace other than Local or Global).
    """
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle), ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def wait(handle: int, milliseconds: int) -> int:
    """Wait up to ``milliseconds`` to own ``handle``; 0 only polls.

    Returns:
        WaitForSingleObject's result: WAIT_OBJECT_0 (0) or WAIT_ABANDONED
        (0x80, the previous owner died), and both mean this thread now owns
        it. Otherwise WAIT_TIMEOUT or WAIT_FAILED (see ``ctypes.get_last_error``).
    """
    return int(_kernel32.WaitForSingleObject(handle, milliseconds))


def release(handle: int) -> None:
    """Give up ownership won by :func:`wait`. The handle stays open."""
    _kernel32.ReleaseMutex(handle)


def close(handle: int) -> None:
    """Close a handle from :func:`create`; Windows also does this at process exit."""
    _kernel32.CloseHandle(handle)
