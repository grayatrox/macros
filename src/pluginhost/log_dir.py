"""The one folder every app's log files go in: ``%LOCALAPPDATA%\\Macros\\logs``.

Each app names its own file in it (launcher.log, launcher-error.log,
recipe_calc.log), so the user has one place to look whichever app failed.

Standard library only, like the rest of pluginhost: the launcher needs it for
its crash log before any dependency is installed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pluginhost.bootstrap import data_dir

LOGS = "logs"


def _writable(folder: Path) -> bool:
    """Create ``folder`` if need be and prove a file can be made in it.

    A real file rather than os.access: on Windows that only reads the
    read-only attribute, not the ACLs that actually refuse the write.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        return False
    return True


def log_dir() -> Path:
    """Return the folder to write log files in, creating it.

    ``%LOCALAPPDATA%\\Macros\\logs``, beside the dependency installs. If that
    cannot be written, ``Macros\\logs`` in the temp folder instead, so a broken
    profile folder does not also lose the log that would explain it.

    Never raises. When neither folder is writable it returns the first one
    anyway: opening a log file there then fails, and the caller reports that
    with the path the user would look in.
    """
    preferred = data_dir() / LOGS
    for folder in (preferred, Path(tempfile.gettempdir()) / "Macros" / LOGS):
        if _writable(folder):
            return folder
    return preferred
