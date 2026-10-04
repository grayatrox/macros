"""First-start dependency install (OP #643, #666).

pip, the installed-package lookup and the progress window are replaced with
in-memory fakes; the install folders, locks and app pyproject.toml files are
real temp files.
"""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
import threading
import tkinter
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import TextIO

import pytest

from macros import __main__ as entry
from pluginhost import bootstrap
from pluginhost.bootstrap import (
    MARKER_NAME,
    BootstrapError,
    Hooks,
    Window,
    ensure_dependencies,
)
from pluginhost.plugin_deps import read_pins

ROOT = Path(__file__).resolve().parents[1]


def _app(folder: Path, *requirements: str) -> Path:
    """An app folder whose pyproject.toml declares ``requirements``."""
    folder.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f"'{r}'" for r in requirements)
    (folder / "pyproject.toml").write_text(
        f"[project]\nname = 'app'\ndependencies = [{listed}]\n", encoding="utf-8"
    )
    return folder


def _default_app(lock: Path) -> Path:
    """The app the ``lock`` fixture declares, kept out of the folder the install goes in.

    A sibling of the test's tmp_path rather than a child, so the tests that list
    the install folder's neighbours see only what the install left there.
    """
    return lock.parent.with_name(lock.parent.name + "-app")


class World:
    """Which locked packages are visible, and what installs and site dirs happened."""

    def __init__(self, *, installed: bool = False) -> None:
        self.installed = installed  # every package the lock pins is visible at its pin
        self.visible: set[str] = set()  # ...or only these, by normalised name
        self.lock: Path | None = None
        self.installs: list[tuple[Path, Path]] = []
        self.site_dirs: list[str] = []
        self.install_fails = False
        self.install_provides = True
        self.windows: list[tuple[str, str]] = []  # (title, message) of each progress window

    def version_of(self, name: str) -> str | None:
        assert self.lock is not None, "hooks() records the lock"
        if not self.installed and name not in self.visible:
            return None
        return read_pins(self.lock).get(name)

    def install(self, lock: Path, target: Path) -> None:
        if self.install_fails:
            raise subprocess.CalledProcessError(1, ["pip", "install"])
        self.installs.append((lock, target))
        target.mkdir(parents=True)
        (target / "keyboard.py").write_text("", encoding="utf-8")

    def add_site_dir(self, path: str) -> None:
        self.site_dirs.append(path)
        if self.install_provides:
            self.installed = True

    def hooks(
        self,
        lock: Path,
        *,
        install: bootstrap.Installer | None = None,
        progress: bootstrap.Progress | None = None,
        install_wait: float = bootstrap.INSTALL_WAIT_SECONDS,
    ) -> Hooks:
        self.lock = lock
        return Hooks(
            install=install or self.install,
            progress=progress or self.progress,
            add_site_dir=self.add_site_dir,
            version_of=self.version_of,
            install_wait=install_wait,
        )

    def run(
        self,
        lock: Path,
        target: Path,
        *,
        app_dir: Path | None = None,
        app_name: str = "send_to_window",
    ) -> None:
        """First start of the app in ``app_dir`` (default: the ``lock`` fixture's app)."""
        ensure_dependencies(
            app_name=app_name,
            app_dir=app_dir or _default_app(lock),
            lock=lock,
            target=target,
            hooks=self.hooks(lock),
        )

    def progress(self, title: str, message: str, work: Callable[[Window], None]) -> None:
        self.windows.append((title, message))
        work(Window())


def _no_window(_title: str, _message: str, work: Callable[[Window], None]) -> None:
    work(Window())


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    """The lock, and the app World.run starts by default: one needing pystray."""
    path = tmp_path / "requirements.lock"
    _app(_default_app(path), "pystray>=0.19")
    path.write_text("pystray==0.19.5 --hash=sha256:aaaa\n", encoding="utf-8")
    return path


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "deps-py312"


def _digest(lock: Path) -> str:
    return hashlib.sha256(lock.read_bytes()).hexdigest()


def test_nothing_happens_when_dependencies_already_import(lock: Path, target: Path) -> None:
    world = World(installed=True)
    world.run(lock, target)
    assert world.installs == []
    assert world.site_dirs == []
    assert not target.exists()


def test_only_what_the_apps_pyproject_declares_is_checked(tmp_path: Path, target: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "pyside6-essentials==6.11.0 --hash=sha256:aaaa\npystray==0.19.5 --hash=sha256:bbbb\n",
        encoding="utf-8",
    )
    world = World()
    world.visible.add("pyside6-essentials")
    world.run(lock, target, app_dir=_app(tmp_path / "launcher", "PySide6-Essentials>=6.11"))
    assert world.installs == []  # Macros' other packages are none of the launcher's business


def test_a_package_the_app_declares_triggers_the_install(tmp_path: Path, target: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "pyside6-essentials==6.11.0 --hash=sha256:aaaa\npillow==11.3.0 --hash=sha256:bbbb\n",
        encoding="utf-8",
    )
    world = World()
    world.visible.add("pyside6-essentials")
    world.install_provides = False
    app = _app(tmp_path / "recipe_calc", "PySide6-Essentials>=6.11", "pillow>=11")
    with pytest.raises(BootstrapError, match=r"  pillow==11\.3\.0") as caught:
        world.run(lock, target, app_dir=app, app_name="Recipe Calc")
    assert "pyside6" not in str(caught.value)
    assert len(world.installs) == 1


def test_first_start_installs_from_the_lock_and_adds_the_folder(lock: Path, target: Path) -> None:
    world = World()
    world.run(lock, target)
    assert len(world.installs) == 1
    assert world.installs[0][0] == lock
    assert (target / "keyboard.py").is_file()  # staging folder swapped into place
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == _digest(lock)
    assert world.site_dirs == [str(target)]
    assert not target.with_name(target.name + ".new").exists()


# Both the launcher and Macros run the first start, so the window must name the
# caller rather than always saying send_to_window (#663).
@pytest.mark.parametrize("app_name", ["Python File Launcher", "send_to_window"])
def test_the_progress_window_names_the_app_that_is_starting(
    lock: Path, target: Path, app_name: str
) -> None:
    world = World()
    world.run(lock, target, app_name=app_name)
    [(title, message)] = world.windows
    assert title == f"{app_name} - first start"
    assert message == f"Installing {app_name}'s components (first start only)..."


def test_later_starts_reuse_the_install(lock: Path, target: Path) -> None:
    World().run(lock, target)
    world = World()  # a fresh interpreter: nothing imports until the folder is added
    world.run(lock, target)
    assert world.installs == []
    assert world.site_dirs == [str(target)]


def test_changed_lock_reinstalls(lock: Path, target: Path) -> None:
    World().run(lock, target)
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    world = World()
    world.run(lock, target)
    assert len(world.installs) == 1
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == _digest(lock)


def test_failed_install_keeps_the_previous_one(lock: Path, target: Path) -> None:
    World().run(lock, target)
    old_marker = (target / MARKER_NAME).read_text(encoding="utf-8")
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    world = World()
    world.install_fails = True
    with pytest.raises(BootstrapError, match="network"):
        world.run(lock, target)
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == old_marker
    assert world.site_dirs == []


# ── the swap must never leave the install half-deleted (#675) ────────────────
# An open handle is a real Windows lock: the file can be neither deleted nor
# its folder renamed while it is held, as when a running app has it loaded.


def test_locked_install_is_left_whole_and_reported_as_in_use(lock: Path, target: Path) -> None:
    World().run(lock, target)
    old = sorted(p.name for p in target.iterdir())
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    world = World()
    with (
        (target / "keyboard.py").open(encoding="utf-8"),
        pytest.raises(BootstrapError, match="in use") as caught,
    ):
        world.run(lock, target)
    assert "network" not in str(caught.value)
    assert sorted(p.name for p in target.iterdir()) == old
    assert world.site_dirs == []


def test_failed_move_into_place_restores_the_previous_install(lock: Path, target: Path) -> None:
    World().run(lock, target)
    old_marker = (target / MARKER_NAME).read_text(encoding="utf-8")
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    held: list[TextIO] = []

    def install_and_hold(lock_: Path, staging: Path) -> None:
        World().install(lock_, staging)
        # e.g. a virus scanner still reading the new folder when it is moved in
        held.append((staging / "keyboard.py").open(encoding="utf-8"))

    try:
        with pytest.raises(BootstrapError):
            bootstrap.install_lock(lock, target, install_and_hold)
    finally:
        for handle in held:
            handle.close()
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == old_marker
    assert (target / "keyboard.py").is_file()


def test_install_left_aside_by_an_interrupted_swap_is_recovered(lock: Path, target: Path) -> None:
    World().run(lock, target)
    old_marker = (target / MARKER_NAME).read_text(encoding="utf-8")
    target.rename(target.with_name(target.name + ".old"))  # stopped mid-swap
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    world = World()
    world.install_fails = True
    with pytest.raises(BootstrapError, match="network"):
        world.run(lock, target)
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == old_marker


def test_successful_swap_leaves_no_folders_behind(lock: Path, target: Path) -> None:
    World().run(lock, target)
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    World().run(lock, target)
    assert sorted(p.name for p in target.parent.iterdir()) == ["deps-py312", "requirements.lock"]


def test_missing_lock_is_reported(tmp_path: Path, target: Path) -> None:
    with pytest.raises(BootstrapError, match="lock file"):
        World().run(tmp_path / "absent.lock", target)


def test_still_missing_after_install_is_reported(lock: Path, target: Path) -> None:
    world = World()
    world.install_provides = False
    with pytest.raises(BootstrapError, match="still missing") as caught:
        world.run(lock, target)
    assert "pystray" in str(caught.value)


# ── the app's pyproject.toml is the only list (#666) ─────────────────────────


def test_a_dependency_added_to_the_pyproject_and_lock_is_required_with_no_other_edit(
    tmp_path: Path, target: Path
) -> None:
    app = _app(tmp_path / "app", "pystray>=0.19")
    lock = tmp_path / "requirements.lock"
    lock.write_text("pystray==0.19.5 --hash=sha256:aaaa\n", encoding="utf-8")
    world = World()
    world.visible.add("pystray")
    world.run(lock, target, app_dir=app)
    assert world.installs == []

    _app(app, "pystray>=0.19", "keyboard>=0.13")  # the only two edits: pyproject...
    with lock.open("a", encoding="utf-8") as text:  # ...and the lock
        text.write("keyboard==0.13.5 --hash=sha256:bbbb\n")
    world.install_provides = False
    with pytest.raises(BootstrapError, match="still missing") as caught:
        world.run(lock, target, app_dir=app)
    assert "keyboard==0.13.5" in str(caught.value)
    assert len(world.installs) == 1


def test_a_dependency_whose_marker_excludes_this_machine_is_neither_required_nor_installed(
    tmp_path: Path, target: Path
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("pystray==0.19.5 --hash=sha256:aaaa\n", encoding="utf-8")
    # Not visible and not even pinned: either would fail the start if it were checked.
    app = _app(tmp_path / "app", "pystray>=0.19", 'pyobjc>=10; sys_platform == "no-such-os"')
    world = World()
    world.visible.add("pystray")
    world.run(lock, target, app_dir=app)
    assert world.installs == []
    assert world.site_dirs == []


def test_a_dependency_the_lock_does_not_pin_is_named_and_never_installed(
    tmp_path: Path, target: Path
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("pystray==0.19.5 --hash=sha256:aaaa\n", encoding="utf-8")
    app = _app(tmp_path / "app", "pystray>=0.19", "requests>=2")
    world = World()
    with pytest.raises(BootstrapError, match="does not pin") as caught:
        world.run(lock, target, app_dir=app, app_name="Recipe Calc")
    message = str(caught.value)
    assert "requests>=2" in message
    assert "Recipe Calc" in message
    assert "tools/lock.py" in message
    assert world.installs == []
    assert not target.exists()


def test_a_malformed_pyproject_is_reported(tmp_path: Path, lock: Path, target: Path) -> None:
    app = tmp_path / "broken"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(BootstrapError, match="not valid TOML"):
        World().run(lock, target, app_dir=app)


def _dist_info(site: Path, name: str, version: str, module: str) -> None:
    """An installed distribution ``name`` whose import name is ``module``."""
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    (site / module).mkdir()
    (site / module / "__init__.py").write_text("", encoding="utf-8")


def _real_lookup_hooks(world: World) -> Hooks:
    """World's fakes, but versions looked up in real package metadata on sys.path."""
    return Hooks(install=world.install, progress=_no_window, add_site_dir=world.add_site_dir)


def test_a_package_is_found_by_its_distribution_name_not_its_import_name(
    tmp_path: Path, target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pillow imports as PIL. A version no real install has, so only the fake counts.
    site = tmp_path / "site"
    _dist_info(site, "Pillow", "99.0.1", module="PIL")
    monkeypatch.syspath_prepend(str(site))
    lock = tmp_path / "requirements.lock"
    lock.write_text("pillow==99.0.1 --hash=sha256:aaaa\n", encoding="utf-8")
    world = World()
    ensure_dependencies(
        app_name="Recipe Calc",
        app_dir=_app(tmp_path / "app", "Pillow>=11"),
        lock=lock,
        target=target,
        hooks=_real_lookup_hooks(world),
    )
    assert world.installs == []


def test_a_package_at_another_version_than_the_lock_pins_is_missing(
    tmp_path: Path, target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    _dist_info(site, "Pillow", "99.0.0", module="PIL")  # PIL imports, but not the pinned one
    monkeypatch.syspath_prepend(str(site))
    lock = tmp_path / "requirements.lock"
    lock.write_text("pillow==99.0.1 --hash=sha256:aaaa\n", encoding="utf-8")
    world = World()
    with pytest.raises(BootstrapError, match="still missing") as caught:
        ensure_dependencies(
            app_name="Recipe Calc",
            app_dir=_app(tmp_path / "app", "Pillow>=11"),
            lock=lock,
            target=target,
            hooks=_real_lookup_hooks(world),
        )
    assert "pillow==99.0.1" in str(caught.value)
    assert len(world.installs) == 1


def test_deps_dir_is_per_user_and_per_python_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tag = f"deps-py{sys.version_info.major}{sys.version_info.minor}"
    assert bootstrap.deps_dir() == tmp_path / "Macros" / tag


class FakePip:
    """Stands in for subprocess.run; records each pip invocation."""

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self.fail_with = fail_with

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        self.calls.append((list(argv), env if isinstance(env, dict) else None))
        code = 1 if self.fail_with else 0
        return subprocess.CompletedProcess(argv, code, stdout="", stderr=self.fail_with or "")


def test_pip_install_checks_every_download_against_its_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pip = FakePip()
    monkeypatch.setattr(subprocess, "run", pip)
    lock, build, out = tmp_path / "req.lock", tmp_path / "build.lock", tmp_path / "out"
    bootstrap.pip_install(lock, out, build)

    (backend, _), (deps, deps_env) = pip.calls
    for argv in (backend, deps):
        assert argv[:4] == [sys.executable, "-m", "pip", "install"]
        assert "--require-hashes" in argv
        assert argv[argv.index("--target") + 1] == str(out)
    # The build backend first, as a wheel only...
    assert argv_value(backend, "--only-binary") == ":all:"
    assert argv_value(backend, "-r") == str(build)
    # ...then the rest, built against it rather than an unpinned isolated copy.
    assert "--no-build-isolation" in deps
    assert argv_value(deps, "-r") == str(lock)
    assert deps_env is not None
    assert deps_env["PYTHONPATH"] == str(out)


def test_pip_failure_reaches_the_user_with_pips_reason(
    lock: Path, target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", FakePip(fail_with="ERROR: No matching distribution found for x")
    )
    world = World()
    with pytest.raises(BootstrapError, match="No matching distribution found for x"):
        ensure_dependencies(
            app_name="send_to_window",
            app_dir=_default_app(lock),
            lock=lock,
            target=target,
            hooks=world.hooks(lock, install=bootstrap.pip_install, progress=_no_window),
        )


def argv_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ── install_lock: the same install, for callers with their own UI (#666) ─────


def test_install_lock_installs_a_stale_folder_once(lock: Path, target: Path) -> None:
    world = World()
    assert not bootstrap.is_current(lock, target)
    assert bootstrap.install_lock(lock, target, world.install) is True
    assert bootstrap.is_current(lock, target)
    assert bootstrap.install_lock(lock, target, world.install) is False
    assert len(world.installs) == 1
    assert (target / "keyboard.py").is_file()


def test_install_lock_failure_keeps_the_previous_install(lock: Path, target: Path) -> None:
    bootstrap.install_lock(lock, target, World().install)
    old_marker = (target / MARKER_NAME).read_text(encoding="utf-8")
    lock.write_text("pystray==0.19.6 --hash=sha256:bbbb\n", encoding="utf-8")
    world = World()
    world.install_fails = True
    with pytest.raises(BootstrapError, match="try again"):
        bootstrap.install_lock(lock, target, world.install)
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == old_marker


def test_install_lock_reports_a_missing_lock(tmp_path: Path, target: Path) -> None:
    with pytest.raises(BootstrapError, match="lock file"):
        bootstrap.install_lock(tmp_path / "absent.lock", target, World().install)


# ── concurrent installs into the one shared folder (#687) ────────────────────
# The launcher and Macros both install on first start; started together, each
# would wipe the other's staging folder and run pip into it.


class OverlapProbe:
    """An installer that holds its install open to see whether another starts meanwhile."""

    def __init__(self) -> None:
        self.world = World()
        self.entered = threading.Event()  # the first install has begun
        self.overlapped = threading.Event()  # a second began before the first finished
        self._active = 0
        self._count = threading.Lock()

    def install(self, lock: Path, staging: Path) -> None:
        with self._count:
            self._active += 1
            if self._active > 1:
                self.overlapped.set()
        self.entered.set()
        try:
            self.overlapped.wait(timeout=1.0)  # time for a racing second install to start
            self.world.install(lock, staging)
        finally:
            with self._count:
                self._active -= 1


def _install_lock(lock: Path, target: Path, install: bootstrap.Installer) -> None:
    bootstrap.install_lock(lock, target, install)


def _first_start(lock: Path, target: Path, install: bootstrap.Installer) -> None:
    world = World()  # its own interpreter: nothing imports until the folder is added
    ensure_dependencies(
        app_name="send_to_window",
        app_dir=_default_app(lock),
        lock=lock,
        target=target,
        hooks=world.hooks(lock, install=install, progress=_no_window),
    )


@pytest.mark.parametrize("second", [_install_lock, _first_start], ids=["install_lock", "startup"])
def test_concurrent_installs_into_one_folder_install_once(
    lock: Path, target: Path, second: Callable[[Path, Path, bootstrap.Installer], None]
) -> None:
    probe = OverlapProbe()
    errors: list[BaseException] = []

    def run(installer: Callable[[Path, Path, bootstrap.Installer], None]) -> None:
        try:
            installer(lock, target, probe.install)
        except BaseException as exc:  # reported by the assertion below
            errors.append(exc)

    first = threading.Thread(target=run, args=(_install_lock,))
    first.start()
    assert probe.entered.wait(timeout=5)
    racer = threading.Thread(target=run, args=(second,))
    racer.start()
    first.join(timeout=10)
    racer.join(timeout=10)

    assert errors == []
    assert not probe.overlapped.is_set()
    assert len(probe.world.installs) == 1  # the second reused the first's install
    assert (target / "keyboard.py").is_file()
    assert bootstrap.is_current(lock, target)


# ── a hung install must not freeze every other app (#693) ────────────────────
# The mutex frees only when its holder finishes or dies; a pip install that
# hangs would otherwise keep every app that starts after it waiting forever.


def _install_lock_waiting(lock: Path, target: Path, wait: float) -> None:
    bootstrap.install_lock(lock, target, World().install, wait=wait)


def _first_start_waiting(lock: Path, target: Path, wait: float) -> None:
    world = World()
    ensure_dependencies(
        app_name="send_to_window",
        app_dir=_default_app(lock),
        lock=lock,
        target=target,
        hooks=world.hooks(lock, progress=_no_window, install_wait=wait),
    )


@pytest.mark.parametrize(
    "waiter", [_install_lock_waiting, _first_start_waiting], ids=["install_lock", "startup"]
)
def test_waiting_for_a_hung_install_gives_up_and_says_why(
    lock: Path, target: Path, waiter: Callable[[Path, Path, float], None]
) -> None:
    held = threading.Event()
    release = threading.Event()

    def hung_pip(lock: Path, staging: Path) -> None:  # a pip install that never returns
        held.set()
        release.wait(timeout=30)
        World().install(lock, staging)

    holder = threading.Thread(target=_install_lock, args=(lock, target, hung_pip), daemon=True)
    holder.start()
    assert held.wait(timeout=5)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            waiter(lock, target, 0.2)
        except BaseException as exc:  # inspected below
            errors.append(exc)

    second = threading.Thread(target=run, daemon=True)
    try:
        second.start()
        second.join(timeout=5)
        gave_up = not second.is_alive()
        installed_meanwhile = bootstrap.is_current(lock, target)
    finally:
        release.set()
        holder.join(timeout=5)
        second.join(timeout=5)

    assert gave_up, "the second app is still waiting on the hung install"
    assert not installed_meanwhile
    assert len(errors) == 1
    assert isinstance(errors[0], BootstrapError)
    message = str(errors[0])
    assert "Another app is installing" in message
    assert "has not finished after 0.2 seconds." in message
    assert "start this app again" in message


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (600.0, "10 minutes"),
        (60.0, "1 minute"),
        (120.0, "2 minutes"),
        (90.0, "1 minute 30 seconds"),
        (150.0, "2 minutes 30 seconds"),
        (2.0, "2 seconds"),
        (1.0, "1 second"),
        (0.2, "0.2 seconds"),
        (59.0, "59 seconds"),
        (90.4, "1 minute 30 seconds"),
    ],
)
def test_wait_limit_is_described_in_readable_units(seconds: float, expected: str) -> None:
    assert bootstrap._describe_duration(seconds) == expected


# ── waiting for another app is shown, and can be cancelled (#695) ────────────

WAITING = "Another app is installing its components.\nsend_to_window is waiting for it to finish..."
INSTALLING = "Installing send_to_window's components (first start only)..."


class _Holder:
    """Another app whose install is under way until ``release`` is called."""

    def __init__(self, lock: Path, target: Path) -> None:
        self.held = threading.Event()
        self._release = threading.Event()
        self.thread = threading.Thread(
            target=_install_lock, args=(lock, target, self._install), daemon=True
        )
        self.world = World()

    def _install(self, lock: Path, staging: Path) -> None:
        self.held.set()
        self._release.wait(timeout=30)
        self.world.install(lock, staging)

    def __enter__(self) -> _Holder:
        self.thread.start()
        assert self.held.wait(timeout=5)
        return self

    def release(self) -> None:
        self._release.set()

    def __exit__(self, *_exc: object) -> None:
        self.release()
        self.thread.join(timeout=5)


def _start_while_waiting(
    lock: Path,
    target: Path,
    on_shown: Callable[[str, bool], None],
    shown: list[tuple[str, bool]],
) -> None:
    """First start of an app whose window reports to ``shown`` and then to ``on_shown``."""

    def progress(_title: str, _message: str, work: Callable[[Window], None]) -> None:
        def show(message: str, cancellable: bool) -> None:
            shown.append((message, cancellable))
            on_shown(message, cancellable)

        work(window := Window(show))
        assert not window.cancelled.is_set()

    ensure_dependencies(
        app_name="send_to_window",
        app_dir=_default_app(lock),
        lock=lock,
        target=target,
        hooks=World().hooks(lock, progress=progress),
    )


def test_waiting_for_another_apps_install_says_so_then_goes_back_to_installing(
    lock: Path, target: Path
) -> None:
    shown: list[tuple[str, bool]] = []
    with _Holder(lock, target) as holder:
        _start_while_waiting(
            lock,
            target,
            lambda message, _cancellable: holder.release() if message == WAITING else None,
            shown,
        )
    assert shown == [(WAITING, True), (INSTALLING, False)]
    assert bootstrap.is_current(lock, target)
    assert len(holder.world.installs) == 1  # this app reused the other's install


def test_cancelling_the_wait_exits_cleanly_and_leaves_the_install_alone(
    lock: Path, target: Path
) -> None:
    world = World()
    shown: list[tuple[str, bool]] = []

    def progress(_title: str, _message: str, work: Callable[[Window], None]) -> None:
        window = Window(lambda message, cancellable: shown.append((message, cancellable)))
        # The user presses Cancel as soon as the window says it is waiting.
        window.cancelled.set()
        work(window)

    with _Holder(lock, target) as holder:
        with pytest.raises(SystemExit) as exited:
            ensure_dependencies(
                app_name="send_to_window",
                app_dir=_default_app(lock),
                lock=lock,
                target=target,
                hooks=world.hooks(lock, progress=progress),
            )
        still_held = not bootstrap.is_current(lock, target)
    assert exited.value.code == 0
    assert shown == [(WAITING, True)]  # never switched back to Installing
    assert still_held
    assert world.installs == []
    assert world.site_dirs == []
    # The mutex was not left behind: the other app finished, and a later start works.
    assert bootstrap.is_current(lock, target)
    assert len(holder.world.installs) == 1
    assert not target.with_name(target.name + ".new").exists()


def test_an_uncontended_first_start_never_mentions_waiting(lock: Path, target: Path) -> None:
    shown: list[tuple[str, bool]] = []
    _start_while_waiting(lock, target, lambda *_args: None, shown)
    assert shown == []
    assert bootstrap.is_current(lock, target)


# ── the window itself, and who calls it (#663) ───────────────────────────────


class FakeTk:
    """Stands in for tkinter.Tk: records the title, runs scheduled calls at once."""

    def __init__(self) -> None:
        self.titles: list[str] = []
        self.protocols: dict[str, Callable[[], None]] = {}
        self.destroyed = 0

    def protocol(self, name: str, callback: Callable[[], None]) -> None:
        self.protocols[name] = callback

    def title(self, text: str) -> None:
        self.titles.append(text)

    def resizable(self, *_args: object) -> None: ...

    def attributes(self, *_args: object) -> None: ...

    def after(self, _ms: int, callback: Callable[[], None]) -> None:
        callback()

    def destroy(self) -> None:
        self.destroyed += 1

    def mainloop(self) -> None: ...


class FakeWidget:
    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.options = dict(kwargs)
        self.packed = False

    def press(self) -> None:
        command = self.options["command"]
        assert callable(command)
        command()

    def pack(self, **_kwargs: object) -> None:
        self.packed = True

    def pack_forget(self) -> None:
        self.packed = False

    def config(self, **kwargs: object) -> None:
        self.options.update(kwargs)

    def start(self, *_args: object) -> None: ...


def test_with_progress_window_titles_the_window_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    roots: list[FakeTk] = []

    def make_root() -> FakeTk:
        roots.append(FakeTk())
        return roots[-1]

    monkeypatch.setattr(tkinter, "Tk", make_root)
    monkeypatch.setattr(tkinter, "Label", FakeWidget)
    monkeypatch.setattr(ttk, "Progressbar", FakeWidget)
    ran: list[bool] = []
    bootstrap.with_progress_window(
        "Python File Launcher - first start", "Installing...", lambda _window: ran.append(True)
    )
    assert ran == [True]
    [root] = roots
    assert root.titles == ["Python File Launcher - first start"]


def test_the_progress_window_offers_cancel_only_while_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widgets: dict[str, list[FakeWidget]] = {"label": [], "button": []}

    def make(kind: str) -> Callable[..., FakeWidget]:
        def build(*args: object, **kwargs: object) -> FakeWidget:
            widgets[kind].append(FakeWidget(*args, **kwargs))
            return widgets[kind][-1]

        return build

    monkeypatch.setattr(tkinter, "Tk", FakeTk)
    monkeypatch.setattr(tkinter, "Label", make("label"))
    monkeypatch.setattr(tkinter, "Button", make("button"))
    monkeypatch.setattr(ttk, "Progressbar", FakeWidget)
    seen: list[tuple[str, bool]] = []  # (label text, is Cancel packed) after each change

    def work(window: Window) -> None:
        [label] = widgets["label"]
        assert widgets["button"] == [], "no Cancel button until the app waits"
        window.show("waiting", cancellable=True)
        [button] = widgets["button"]
        seen.append((str(label.options["text"]), button.packed))
        button.press()  # the user presses Cancel
        seen.append((str(label.options["text"]), button.packed))
        assert window.cancelled.is_set()
        assert button.options["state"] == "disabled"
        window.show("installing")
        seen.append((str(label.options["text"]), button.packed))

    bootstrap.with_progress_window("t", "Installing...", work)
    assert seen == [("waiting", True), ("Cancelling...", True), ("installing", False)]


def _run_window(
    monkeypatch: pytest.MonkeyPatch, work: Callable[[Window, FakeTk, list[FakeWidget]], None]
) -> list[FakeWidget]:
    """Run ``work`` behind a window built from fakes; return the Labels it made."""
    labels: list[FakeWidget] = []
    roots: list[FakeTk] = []

    def make_root() -> FakeTk:
        roots.append(FakeTk())
        return roots[-1]

    def make_label(*args: object, **kwargs: object) -> FakeWidget:
        labels.append(FakeWidget(*args, **kwargs))
        return labels[-1]

    monkeypatch.setattr(tkinter, "Tk", make_root)
    monkeypatch.setattr(tkinter, "Label", make_label)
    monkeypatch.setattr(tkinter, "Button", FakeWidget)
    monkeypatch.setattr(ttk, "Progressbar", FakeWidget)
    bootstrap.with_progress_window("t", "Installing...", lambda w: work(w, roots[0], labels))
    return labels


def test_closing_the_window_mid_install_is_refused_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def work(window: Window, root: FakeTk, labels: list[FakeWidget]) -> None:
        close = root.protocols["WM_DELETE_WINDOW"]  # KeyError: X is not handled at all
        destroyed_before = root.destroyed
        close()  # the user clicks X while pip is running
        seen["destroyed"] = root.destroyed - destroyed_before
        seen["cancelled"] = window.cancelled.is_set()
        seen["text"] = str(labels[0].options["text"])

    _run_window(monkeypatch, work)
    assert seen["destroyed"] == 0  # the window stays up: the install cannot stop part-way
    assert seen["cancelled"] is False
    assert "Installing..." in str(seen["text"])
    assert "cannot be closed" in str(seen["text"])


def test_closing_the_window_while_waiting_is_the_same_as_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def work(window: Window, root: FakeTk, labels: list[FakeWidget]) -> None:
        window.show("waiting", cancellable=True)
        root.protocols["WM_DELETE_WINDOW"]()
        seen["cancelled"] = window.cancelled.is_set()
        seen["text"] = str(labels[0].options["text"])

    _run_window(monkeypatch, work)
    assert seen == {"cancelled": True, "text": "Cancelling..."}


def test_closing_the_window_after_a_refused_close_does_not_stack_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def work(_window: Window, root: FakeTk, labels: list[FakeWidget]) -> None:
        for _ in range(2):
            root.protocols["WM_DELETE_WINDOW"]()
        seen.append(str(labels[0].options["text"]))

    _run_window(monkeypatch, work)
    assert seen[0].count("cannot be closed") == 1


def test_macros_starts_its_first_start_under_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Path]] = []

    def stop(*, app_name: str, app_dir: Path) -> None:
        calls.append((app_name, app_dir))
        raise BootstrapError("stop before the app runs")

    monkeypatch.setattr(entry, "ensure_dependencies", stop)
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *_args: 0)
    assert entry.main() == 1
    # Its own pyproject.toml is the list it checks: no second list beside it (#666).
    assert calls == [("send_to_window", ROOT / "plugins" / "macros")]
