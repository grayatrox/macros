"""``python -m macros`` (and the plugins/macros/Macros.py shim): start the hotkey app.

Dependencies are installed first if this is the first start; see
:mod:`pluginhost.bootstrap`, which the launcher provides.
"""

from __future__ import annotations

import ctypes

from pluginhost.bootstrap import BootstrapError, ensure_dependencies

MB_ICONERROR = 0x10
#: Import names the app needs; one per package is enough to detect it. Keep in
#: step with this plugin's pyproject.toml.
REQUIRED_MODULES = (
    "keyboard",
    "psutil",
    "pyautogui",
    "pystray",
    "win32clipboard",
    "win32gui",
    "win32process",
    "PIL",
    "PySide6",  # the log window
)


def main() -> int:
    """Install any missing dependencies, then run the app. Returns the exit code."""
    try:
        ensure_dependencies(app_name="send_to_window", required_modules=REQUIRED_MODULES)
    except BootstrapError as exc:
        # Under pythonw there is no console, so report it in a native dialog.
        ctypes.windll.user32.MessageBoxW(0, str(exc), "send_to_window - cannot start", MB_ICONERROR)
        return 1

    # Only importable once the bootstrap has put the dependencies on sys.path.
    from macros.app import main as run_app  # noqa: PLC0415 - must follow the bootstrap

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
