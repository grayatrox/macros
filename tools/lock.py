"""Regenerate the hashed lock files.

``requirements.lock`` and ``requirements-dev.lock`` are compiled together from the
root ``pyproject.toml`` (the launcher, plus the ``dev`` extra) and every plugin's
own ``plugins/<app>/pyproject.toml``, so one resolve pins what every app needs
(docs/adr/0005-root-declares-only-the-launcher.md);
``requirements-build.lock`` (the build backend the first-start bootstrap needs
for sdist-only dependencies) comes from ``requirements-build.in``.

Both are hashed, so ``pip install --require-hashes`` refuses any download that
does not match a digest committed here. ``requirements-dev.lock`` is constrained
against ``requirements.lock``, so the runtime pins are identical in both.

Run after changing dependencies in any of those ``pyproject.toml`` files::

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
PLUGINS = ROOT / "plugins"
MANIFEST_NAME = "launcher-app.toml"
RUNTIME_LOCK = ROOT / "requirements.lock"
DEV_LOCK = ROOT / "requirements-dev.lock"
BUILD_IN = ROOT / "requirements-build.in"
BUILD_LOCK = ROOT / "requirements-build.lock"


def _rel(path: Path) -> str:
    """``path`` relative to the repo root.

    The compiler copies source paths into each ``# via`` annotation; an
    absolute path would commit this machine's layout, username included.
    """
    return path.relative_to(ROOT).as_posix()


def plugin_projects(plugins: Path = PLUGINS) -> list[Path]:
    """The ``pyproject.toml`` of every app in ``plugins``, sorted.

    An app is a folder with a ``launcher-app.toml``, as launcher.pyw lists them.

    Raises:
        FileNotFoundError: an app has no ``pyproject.toml``, so its
            dependencies would silently go unpinned.
    """
    projects = []
    for manifest in sorted(plugins.glob(f"*/{MANIFEST_NAME}")):
        project = manifest.with_name("pyproject.toml")
        if not project.is_file():
            raise FileNotFoundError(f"{manifest.parent} has no pyproject.toml")
        projects.append(project)
    return projects


def _compile(
    *,
    extra: str | None,
    constraint: Path | None,
    output: Path,
    sources: tuple[Path, ...] = (PYPROJECT,),
) -> int:
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
    argv += [_rel(source) for source in sources]

    print(" ".join(argv))
    try:
        subprocess.check_call(argv, cwd=ROOT)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


def main() -> int:
    if os.name != "nt":
        print("WARNING: this project only runs on Windows.", file=sys.stderr)

    sources = (PYPROJECT, *plugin_projects())

    if (status := _compile(extra=None, constraint=None, output=RUNTIME_LOCK, sources=sources)) != 0:
        return status
    print(f"Wrote {_rel(RUNTIME_LOCK)}")

    if (
        status := _compile(extra="dev", constraint=RUNTIME_LOCK, output=DEV_LOCK, sources=sources)
    ) != 0:
        return status
    print(f"Wrote {_rel(DEV_LOCK)}")

    if (
        status := _compile(extra=None, constraint=None, output=BUILD_LOCK, sources=(BUILD_IN,))
    ) != 0:
        return status
    print(f"Wrote {_rel(BUILD_LOCK)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
