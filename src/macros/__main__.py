"""``python -m macros`` (and the root Macros.py shim): start the hotkey app.

Dependencies are installed first if this is the first start; see
:mod:`macros.bootstrap`.
"""

from __future__ import annotations

import ctypes

from macros.bootstrap import BootstrapError, ensure_dependencies

MB_ICONERROR = 0x10


def main() -> int:
    """Install any missing dependencies, then run the app. Returns the exit code."""
    try:
        ensure_dependencies()
    except BootstrapError as exc:
        # Under pythonw there is no console, so report it in a native dialog.
        ctypes.windll.user32.MessageBoxW(0, str(exc), "send_to_window - cannot start", MB_ICONERROR)
        return 1

    # Only importable once the bootstrap has put the dependencies on sys.path.
    from macros.app import main as run_app  # noqa: PLC0415 - must follow the bootstrap

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
