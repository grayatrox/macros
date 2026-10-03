"""Install the app's dependencies on first start.

The app needs third-party packages (pystray, pywin32, ...) that a bare Python
does not have. Rather than make the user run pip, :func:`ensure_dependencies`
installs them the first time the app starts, into a private folder:

* **Only from hash-pinned locks** (``requirements.lock``) with
  ``--require-hashes``: a download that does not match a digest committed to
  this repository is refused. Several dependencies (pyautogui and friends)
  publish only source distributions, so they are built - against setuptools
  from ``requirements-build.lock``, installed first and also hash-pinned,
  with build isolation off. Isolation would fetch an unpinned setuptools.
* **Into ``%LOCALAPPDATA%\\Macros\\deps-pyXY``, not the system interpreter.** One
  folder per Python version, because the wheels are version-specific.
* **In-process.** The folder is added with ``site.addsitedir`` (which also
  runs pywin32's ``.pth`` setup) and the app continues in the same process.
  Re-executing into a separate virtualenv would change the PID, which breaks
  launcher.pyw's Stop button and its single-instance tracking.

A marker file holds the lock's SHA-256, so editing the lock reinstalls on the
next start. A new install is staged beside the old one and swapped in only
once it succeeds, so a failed download never breaks a working setup.

Imports only the standard library at module level - it runs before any
dependency exists.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import shutil
import site
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path

#: Import names the app needs; one per package is enough to detect it.
REQUIRED_MODULES = (
    "keyboard",
    "psutil",
    "pyautogui",
    "pystray",
    "win32clipboard",
    "win32gui",
    "win32process",
    "PIL",
)

ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = ROOT / "requirements.lock"
BUILD_LOCK_FILE = ROOT / "requirements-build.lock"
MARKER_NAME = "requirements.lock.sha256"

FindSpec = Callable[[str], ModuleSpec | None]
Installer = Callable[[Path, Path], None]
Progress = Callable[[str, Callable[[], None]], None]
AddSiteDir = Callable[[str], None]


class BootstrapError(Exception):
    """The dependencies could not be made importable; the message says why."""


def deps_dir() -> Path:
    """Private folder the dependencies are installed into for this Python."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Macros" / f"deps-py{sys.version_info.major}{sys.version_info.minor}"


def missing_modules(find_spec: FindSpec = importlib.util.find_spec) -> list[str]:
    """Which of :data:`REQUIRED_MODULES` cannot be imported right now."""
    return [name for name in REQUIRED_MODULES if find_spec(name) is None]


def _pip(args: list[str], env: dict[str, str] | None = None) -> None:
    """Run this interpreter's pip, capturing its output for error reports.

    Raises:
        subprocess.CalledProcessError: pip failed; ``output`` holds what it said.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        # pip's own console would flash up under pythonw otherwise.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout + result.stderr
        )


def pip_install(lock: Path, target: Path, build_lock: Path = BUILD_LOCK_FILE) -> None:
    """Install ``lock`` into ``target``, every download checked against its hash.

    Raises:
        subprocess.CalledProcessError: pip failed (network, hash mismatch...).
    """
    common = [
        "install",
        "--require-hashes",
        "--disable-pip-version-check",
        "--no-input",
        "--target",
        str(target),
    ]
    # 1. The pinned build backend, as a wheel.
    _pip([*common, "--only-binary", ":all:", "-r", str(build_lock)])
    # 2. Everything else; sdist-only packages build against step 1's setuptools,
    #    which PYTHONPATH makes visible to pip's build hooks.
    _pip(
        [*common, "--no-build-isolation", "-r", str(lock)],
        env={**os.environ, "PYTHONPATH": str(target)},
    )


def with_progress_window(message: str, work: Callable[[], None]) -> None:
    """Run ``work`` on a thread behind a small "please wait" window.

    A first install downloads several packages; with no window it would look
    like the app failed to start. Whatever ``work`` raises is re-raised here.
    """
    import tkinter as tk  # noqa: PLC0415 - GUI only when an install is actually needed
    from tkinter import ttk  # noqa: PLC0415 - ditto

    root = tk.Tk()
    root.title("send_to_window - first start")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    tk.Label(root, text=message, font=("Segoe UI", 10), padx=24, pady=16).pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=300)
    bar.pack(padx=24, pady=(0, 20))
    bar.start(12)

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            work()
        except BaseException as exc:  # re-raised on the calling thread below
            errors.append(exc)
        finally:
            root.after(0, root.destroy)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    root.mainloop()
    thread.join()
    if errors:
        raise errors[0]


def _tail(output: str | None, lines: int = 8) -> str:
    """The last few lines of pip's output - where it says what went wrong."""
    if not output:
        return ""
    return "\n" + "\n".join(output.strip().splitlines()[-lines:]) + "\n"


def _install_fresh(lock: Path, target: Path, digest: str, install: Installer) -> None:
    """Install into a staging folder, then swap it in place of ``target``."""
    staging = target.with_name(target.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    install(lock, staging)
    (staging / MARKER_NAME).write_text(digest, encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


@dataclass(frozen=True)
class Hooks:
    """The side effects ensure_dependencies performs; tests replace them."""

    install: Installer = pip_install
    progress: Progress = with_progress_window
    add_site_dir: AddSiteDir = site.addsitedir
    find_spec: FindSpec = importlib.util.find_spec


def ensure_dependencies(
    *, lock: Path = LOCK_FILE, target: Path | None = None, hooks: Hooks | None = None
) -> None:
    """Make every runtime dependency importable, installing on first start.

    Does nothing when they are already importable (e.g. a developer's venv).

    Raises:
        BootstrapError: the lock is missing, the install failed, or modules
            are still missing afterwards.
    """
    hooks = hooks or Hooks()
    if not missing_modules(hooks.find_spec):
        return

    target = target or deps_dir()
    try:
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    except OSError as exc:
        raise BootstrapError(f"The dependency lock file could not be read:\n\n{exc}") from exc

    marker = target / MARKER_NAME
    current = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    if current != digest:
        try:
            hooks.progress(
                "Installing send_to_window's components (first start only)...",
                lambda: _install_fresh(lock, target, digest, hooks.install),
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            detail = _tail(exc.output) if isinstance(exc, subprocess.CalledProcessError) else ""
            raise BootstrapError(
                f"Installing the required components failed:\n\n{exc}\n{detail}\n"
                "Check your network connection and start the app again."
            ) from exc

    hooks.add_site_dir(str(target))
    importlib.invalidate_caches()
    if still_missing := missing_modules(hooks.find_spec):
        raise BootstrapError(
            "These components are still missing after installation:\n\n  "
            + "\n  ".join(still_missing)
            + f"\n\nInstall folder: {target}"
        )
