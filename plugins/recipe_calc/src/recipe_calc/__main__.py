"""``python -m recipe_calc`` (and the plugins/recipe_calc/Recipe_Calc.py shim): start the window.

Dependencies are installed first if this is the first start; see
:mod:`pluginhost.bootstrap`, which the launcher provides. Nothing here may
import PySide6 before that has run: a bare Python does not have it (#677).
"""

from __future__ import annotations

import ctypes

from pluginhost.bootstrap import BootstrapError, ensure_dependencies

APP_NAME = "Recipe Calc"
MB_ICONERROR = 0x10


def main() -> int:
    """Install any missing dependencies, then run the app. Returns the exit code."""
    try:
        ensure_dependencies(app_name=APP_NAME)
    except BootstrapError as exc:
        # Under pythonw there is no console, so report it in a native dialog.
        ctypes.windll.user32.MessageBoxW(0, str(exc), f"{APP_NAME} - cannot start", MB_ICONERROR)
        return 1

    # Only importable once the bootstrap has put the dependencies on sys.path.
    from recipe_calc.app import main as run_app  # noqa: PLC0415 - must follow the bootstrap

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
