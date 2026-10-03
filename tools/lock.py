"""Regenerate ``requirements.lock`` and ``requirements-dev.lock`` from ``pyproject.toml``.

Both are hashed, so ``pip install --require-hashes`` refuses any download that
does not match a digest committed here. ``requirements-dev.lock`` is constrained
against ``requirements.lock``, so the runtime pins are identical in both.

Run after changing dependencies in ``pyproject.toml``::

    python tools/lock.py

Requires ``uv`` (a dev dependency). Regenerate on **Windows**: pywin32 and
pillow ship platform-specific wheels, and a lock produced elsewhere will not
install here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
RUNTIME_LOCK = ROOT / "requirements.lock"
DEV_LOCK = ROOT / "requirements-dev.lock"


def _rel(path: Path) -> str:
    """``path`` relative to the repo root.

    The compiler copies source paths into each ``# via`` annotation; an
    absolute path would commit this machine's layout, username included.
    """
    return path.relative_to(ROOT).as_posix()


def _compile(*, extra: str | None, constraint: Path | None, output: Path) -> int:
    argv = [
        sys.executable,
        "-m",
        "uv",
        "pip",
        "compile",
        "--generate-hashes",
        "--no-header",
        "--python-platform",
        "windows",
        "--output-file",
        _rel(output),
    ]
    if extra:
        argv += ["--extra", extra]
    if constraint:
        argv += ["--constraint", _rel(constraint)]
    argv.append(_rel(PYPROJECT))

    print(" ".join(argv))
    try:
        subprocess.check_call(argv, cwd=ROOT)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


def main() -> int:
    if os.name != "nt":
        print("WARNING: this project only runs on Windows.", file=sys.stderr)

    if (status := _compile(extra=None, constraint=None, output=RUNTIME_LOCK)) != 0:
        return status
    print(f"Wrote {_rel(RUNTIME_LOCK)}")

    if (status := _compile(extra="dev", constraint=RUNTIME_LOCK, output=DEV_LOCK)) != 0:
        return status
    print(f"Wrote {_rel(DEV_LOCK)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
