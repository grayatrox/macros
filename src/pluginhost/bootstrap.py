"""Install the app's dependencies on first start.

The app needs third-party packages (pystray, pywin32, ...) that a bare Python
does not have. Rather than make the user run pip, :func:`ensure_dependencies`
installs them the first time the app starts, into a private folder. What the
app needs is read from its own ``pyproject.toml`` and checked against the lock
by :mod:`pluginhost.plugin_deps`, as launcher.pyw checks it (#666):

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
once it succeeds, so a failed download never breaks a working setup. Every app
shares the folder, so an install holds a named mutex: an app starting while
another installs waits for it, then reuses its install. The wait is bounded, so
an install that hangs makes the others report it rather than freeze with it.

Imports only the standard library at module level - it runs before any
dependency exists.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import os
import shutil
import site
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = ROOT / "requirements.lock"
BUILD_LOCK_FILE = ROOT / "requirements-build.lock"
MARKER_NAME = "requirements.lock.sha256"

#: The version of a distribution (by normalised name) that would be imported, or None.
VersionOf = Callable[[str], str | None]
Installer = Callable[[Path, Path], None]
AddSiteDir = Callable[[str], None]

# How long an app waits for another app's install before giving up. A cold
# install builds several packages from source; ten minutes is far beyond that
# on a working connection, and short enough that a hung one gets reported.
INSTALL_WAIT_SECONDS = 600.0


class BootstrapError(Exception):
    """The dependencies could not be made importable; the message says why."""


class InstallCancelledError(Exception):
    """The user cancelled while waiting for another app's install."""


class Window:
    """What the work behind a progress window uses to talk to it.

    Tests and headless callers use one with no display: ``show`` then does nothing.
    """

    def __init__(self, show: Callable[[str, bool], None] | None = None) -> None:
        self._show = show
        #: Set when the user presses Cancel; the work should stop soon after.
        self.cancelled = threading.Event()

    def show(self, message: str, *, cancellable: bool = False) -> None:
        """Replace the window's message; ``cancellable`` adds a Cancel button."""
        if self._show:
            self._show(message, cancellable)


Progress = Callable[[str, str, Callable[[Window], None]], None]  # (title, message, work)


def _nothing() -> None:
    pass


@dataclass(frozen=True)
class Wait:
    """How long, and how visibly, to wait for another app's install."""

    seconds: float
    #: Called once, when the install is found to be held by someone else.
    on_blocked: Callable[[], None] = _nothing
    #: Called once the install has been acquired after being blocked.
    on_acquired: Callable[[], None] = _nothing
    #: When set, stops the wait with :class:`InstallCancelledError`.
    cancelled: threading.Event | None = None


def deps_dir() -> Path:
    """Private folder the dependencies are installed into for this Python."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    # Named for Macros, which had the first install; renaming it would reinstall everything.
    return base / "Macros" / f"deps-py{sys.version_info.major}{sys.version_info.minor}"


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


def with_progress_window(title: str, message: str, work: Callable[[Window], None]) -> None:
    """Run ``work`` on a thread behind a small "please wait" window titled ``title``.

    A first install downloads several packages; with no window it would look
    like the app failed to start. ``work`` may change the message and offer a
    Cancel button through the :class:`Window` it is given. Whatever ``work``
    raises is re-raised here.
    """
    import tkinter as tk  # noqa: PLC0415 - GUI only when an install is actually needed
    from tkinter import ttk  # noqa: PLC0415 - ditto

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    label = tk.Label(root, text=message, font=("Segoe UI", 10), padx=24, pady=16)
    label.pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=300)
    bar.pack(padx=24, pady=(0, 20))
    bar.start(12)

    def show(text: str, cancellable: bool) -> None:
        root.after(0, lambda: apply(text, cancellable))

    window = Window(show)
    buttons: list[tk.Button] = []  # created on first use: most installs never wait

    def cancel() -> None:
        window.cancelled.set()
        buttons[0].config(state="disabled")
        label.config(text="Cancelling...")

    def apply(text: str, cancellable: bool) -> None:
        label.config(text=text)
        if cancellable and not buttons:
            buttons.append(tk.Button(root, text="Cancel", width=10, command=cancel))
        if buttons:
            if cancellable:
                buttons[0].pack(pady=(0, 16))
            else:
                buttons[0].pack_forget()

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            work(window)
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
    """Install into a staging folder, then swap it in place of ``target``.

    The old install is renamed aside rather than deleted in place: a rename
    either succeeds whole or changes nothing, whereas deleting a folder that a
    running app holds files in (Windows locks them) stops part-way and leaves
    a broken install. It is deleted only once the new one is in place.

    Raises:
        BootstrapError: ``target`` is in use and could not be replaced; it is
            left as it was.
        subprocess.CalledProcessError, OSError: the install itself failed;
            ``target`` is left as it was.
    """
    staging = target.with_name(target.name + ".new")
    retired = target.with_name(target.name + ".old")
    if retired.exists() and not target.exists():
        # A swap was interrupted between its two renames: this is the last good install.
        retired.rename(target)
    shutil.rmtree(staging, ignore_errors=True)
    install(lock, staging)
    (staging / MARKER_NAME).write_text(digest, encoding="utf-8")
    shutil.rmtree(retired, ignore_errors=True)  # left by an earlier swap; harmless if it stays
    if target.exists():
        try:
            target.rename(retired)
        except OSError as exc:
            raise BootstrapError(
                f"The components folder is in use, so it could not be updated:\n\n{target}\n\n"
                f"{exc}\n\nClose every app that uses it, then try again."
            ) from exc
    try:
        staging.rename(target)
    except OSError:
        if retired.exists():
            retired.rename(target)
        raise
    shutil.rmtree(retired, ignore_errors=True)  # a locked leftover is retried next install


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
WAIT_SLICE_SECONDS = 0.1  # how often a waiting app looks at its Cancel button


@contextmanager
def _exclusive(target: Path, wait: Wait) -> Iterator[None]:
    """Hold the install mutex for ``target`` against every other process and thread.

    A kernel mutex rather than a lock file: Windows releases it when its holder
    dies (the next waiter gets WAIT_ABANDONED, and still owns it), and it leaves
    nothing beside the install. Named after the folder, so separate folders do
    not wait for each other. Waits at most ``wait.seconds``: the mutex frees
    only when its holder finishes or dies, and a hung pip install does neither.
    Found held, it reports that through ``wait.on_blocked`` and then polls, so
    ``wait.cancelled`` is noticed within a fraction of a second.

    Raises:
        BootstrapError: another holder kept the mutex for longer than ``wait.seconds``.
        InstallCancelledError: ``wait.cancelled`` was set while waiting.
        OSError: the mutex could not be created or waited on.
    """
    key = hashlib.sha256(str(target.resolve()).lower().encode("utf-8")).hexdigest()
    handle = _kernel32.CreateMutexW(None, False, f"Local\\Macros-deps-{key}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = _kernel32.WaitForSingleObject(handle, 0)
        if result == WAIT_TIMEOUT:
            wait.on_blocked()
            deadline = time.monotonic() + wait.seconds
            while result == WAIT_TIMEOUT:
                if wait.cancelled is not None and wait.cancelled.is_set():
                    raise InstallCancelledError
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                result = _kernel32.WaitForSingleObject(
                    handle, round(min(remaining, WAIT_SLICE_SECONDS) * 1000)
                )
            else:
                wait.on_acquired()
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if result == WAIT_TIMEOUT:
            raise BootstrapError(
                "Another app is installing the required components into:\n\n"
                f"{target}\n\nand has not finished after {wait.seconds / 60:g} minutes. "
                "Wait for it to finish, then start this app again. If it seems stuck, close "
                "the other "
                "app (or end its Python process in Task Manager) and start this app again."
            )
        try:
            yield
        finally:
            _kernel32.ReleaseMutex(handle)
    finally:
        _kernel32.CloseHandle(handle)


def _install_if_stale(
    lock: Path, target: Path, digest: str, install: Installer, wait: Wait
) -> bool:
    """Install unless ``target`` already holds ``digest``, alone among installers.

    The digest is checked again once the mutex is held: another app may have
    finished this very install while this one waited. Returns whether an
    install was needed.

    Raises:
        As :func:`_install_fresh`, plus the mutex's errors (see :func:`_exclusive`).
    """
    with _exclusive(target, wait):
        if _installed_digest(target) == digest:
            return False
        _install_fresh(lock, target, digest, install)
        return True


def _lock_digest(lock: Path) -> str:
    """SHA-256 of the lock file, as stored in the install's marker.

    Raises:
        BootstrapError: the lock cannot be read.
    """
    try:
        return hashlib.sha256(lock.read_bytes()).hexdigest()
    except OSError as exc:
        raise BootstrapError(f"The dependency lock file could not be read:\n\n{exc}") from exc


def _installed_digest(target: Path) -> str | None:
    marker = target / MARKER_NAME
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else None


def _install_failed(exc: subprocess.CalledProcessError | OSError, retry: str) -> BootstrapError:
    detail = _tail(exc.output) if isinstance(exc, subprocess.CalledProcessError) else ""
    return BootstrapError(
        f"Installing the required components failed:\n\n{exc}\n{detail}\n"
        f"Check your network connection and {retry}."
    )


def is_current(lock: Path, target: Path) -> bool:
    """Whether ``target`` holds an install of exactly this ``lock``.

    Raises:
        BootstrapError: the lock cannot be read.
    """
    return _installed_digest(target) == _lock_digest(lock)


def install_lock(
    lock: Path,
    target: Path,
    install: Installer = pip_install,
    *,
    wait: float = INSTALL_WAIT_SECONDS,
) -> bool:
    """Make ``target`` hold ``lock``'s packages, installing only if it does not yet.

    The same staged, hash-checked install as the first start, without its
    window: for callers with their own UI that run it off their GUI thread.
    Waits at most ``wait`` seconds for another app's install into ``target``.
    Returns whether an install was needed.

    Raises:
        BootstrapError: the lock cannot be read, the install failed, or
            another app's install did not finish within ``wait``; an earlier
            install in ``target`` is then left as it was.
    """
    digest = _lock_digest(lock)
    if _installed_digest(target) == digest:
        return False
    try:
        return _install_if_stale(lock, target, digest, install, Wait(wait))
    except (subprocess.CalledProcessError, OSError) as exc:
        raise _install_failed(exc, "try again") from exc


@dataclass(frozen=True)
class Hooks:
    """The side effects ensure_dependencies performs; tests replace them."""

    install: Installer = pip_install
    progress: Progress = with_progress_window
    add_site_dir: AddSiteDir = site.addsitedir
    # None: look the version up on sys.path as it is at the time of each check.
    version_of: VersionOf | None = None
    # Seconds to wait for another app's install into the same folder.
    install_wait: float = INSTALL_WAIT_SECONDS


def _unmet(app_name: str, app_dir: Path, lock: Path, version_of: VersionOf | None) -> list[str]:
    """The ``name==version`` pins ``app_dir``'s pyproject.toml needs but would not get.

    Raises:
        BootstrapError: the pyproject or the lock is unusable, or the app
            needs something the lock does not pin (never installed loosely).
    """
    # plugin_deps imports this module, so importing it at the top would be circular.
    from pluginhost import plugin_deps  # noqa: PLC0415 - see above

    importlib.invalidate_caches()  # an install since the last check must be seen
    try:
        status = plugin_deps.check(
            plugin_deps.read_requirements(app_dir),
            plugin_deps.read_pins(lock),
            version_of or plugin_deps.version_on(sys.path),
        )
    except plugin_deps.PluginDepsError as exc:
        raise BootstrapError(f"{app_name}'s dependencies could not be checked:\n\n{exc}") from exc
    if status.unpinned:
        raise BootstrapError(
            f"{app_name} needs components that {lock.name} does not pin, so they "
            "cannot be installed:\n\n  "
            + "\n  ".join(status.unpinned)
            + "\n\nRegenerate the lock with: python tools/lock.py"
        )
    return status.missing


def ensure_dependencies(
    *,
    app_name: str,
    app_dir: Path,
    lock: Path = LOCK_FILE,
    target: Path | None = None,
    hooks: Hooks | None = None,
) -> None:
    """Make every runtime dependency importable, installing on first start.

    Does nothing when they are already importable (e.g. a developer's venv).
    ``app_name`` is the display name of the app that is starting; the progress
    window is titled after it. Required, because several apps share this
    install and a default would name the wrong one. ``app_dir`` is the folder
    whose ``pyproject.toml`` declares what the app needs
    (``[project].dependencies``); required for the same reason. A dependency
    counts as present only at the exact version ``lock`` pins, and one whose
    environment marker does not match this machine is not needed at all.

    Raises:
        BootstrapError: the lock or the app's pyproject.toml is unusable, the
            app needs something the lock does not pin, the install failed,
            another app's install did not finish within
            ``hooks.install_wait``, or dependencies are still missing afterwards.
        SystemExit: the user cancelled while this app waited for another app's
            install; exits with status 0, as a deliberate choice rather than a failure.
    """
    hooks = hooks or Hooks()
    if not _unmet(app_name, app_dir, lock, hooks.version_of):
        return

    target = target or deps_dir()
    digest = _lock_digest(lock)
    if _installed_digest(target) != digest:
        installing = f"Installing {app_name}'s components (first start only)..."

        def work(window: Window) -> None:
            wait = Wait(
                hooks.install_wait,
                on_blocked=lambda: window.show(
                    f"Another app is installing its components.\n{app_name} is waiting for it "
                    "to finish...",
                    cancellable=True,
                ),
                on_acquired=lambda: window.show(installing),
                cancelled=window.cancelled,
            )
            _install_if_stale(lock, target, digest, hooks.install, wait)

        try:
            hooks.progress(f"{app_name} - first start", installing, work)
        except InstallCancelledError:
            raise SystemExit(0) from None
        except (subprocess.CalledProcessError, OSError) as exc:
            raise _install_failed(exc, "start the app again") from exc

    hooks.add_site_dir(str(target))
    if still_missing := _unmet(app_name, app_dir, lock, hooks.version_of):
        raise BootstrapError(
            "These components are still missing after installation:\n\n  "
            + "\n  ".join(still_missing)
            + f"\n\nInstall folder: {target}"
        )
