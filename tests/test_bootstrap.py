"""First-start dependency install (OP #643).

pip, the import system and the progress window are replaced with in-memory
fakes; the install folders are real temp directories.
"""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
import tkinter
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path
from tkinter import ttk

import pytest

from macros import __main__ as entry
from macros import bootstrap
from macros.bootstrap import (
    MARKER_NAME,
    REQUIRED_MODULES,
    BootstrapError,
    Hooks,
    ensure_dependencies,
)


class World:
    """Which modules import, and what installs and site dirs happened."""

    def __init__(self, *, installed: bool = False) -> None:
        self.importable: set[str] = set(REQUIRED_MODULES) if installed else set()
        self.installs: list[tuple[Path, Path]] = []
        self.site_dirs: list[str] = []
        self.install_fails = False
        self.install_provides = True
        self.windows: list[tuple[str, str]] = []  # (title, message) of each progress window

    def find_spec(self, name: str) -> ModuleSpec | None:
        return ModuleSpec(name, None) if name in self.importable else None

    def install(self, lock: Path, target: Path) -> None:
        if self.install_fails:
            raise subprocess.CalledProcessError(1, ["pip", "install"])
        self.installs.append((lock, target))
        target.mkdir(parents=True)
        (target / "keyboard.py").write_text("", encoding="utf-8")

    def add_site_dir(self, path: str) -> None:
        self.site_dirs.append(path)
        if self.install_provides:
            self.importable.update(REQUIRED_MODULES)

    def run(self, lock: Path, target: Path, *, app_name: str = "send_to_window") -> None:
        hooks = Hooks(
            install=self.install,
            progress=self.progress,
            add_site_dir=self.add_site_dir,
            find_spec=self.find_spec,
        )
        ensure_dependencies(app_name=app_name, lock=lock, target=target, hooks=hooks)

    def progress(self, title: str, message: str, work: Callable[[], None]) -> None:
        self.windows.append((title, message))
        work()


def _no_window(_title: str, _message: str, work: Callable[[], None]) -> None:
    work()


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    path = tmp_path / "requirements.lock"
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


def test_missing_lock_is_reported(tmp_path: Path, target: Path) -> None:
    with pytest.raises(BootstrapError, match="lock file"):
        World().run(tmp_path / "absent.lock", target)


def test_still_missing_after_install_is_reported(lock: Path, target: Path) -> None:
    world = World()
    world.install_provides = False
    with pytest.raises(BootstrapError, match="still missing") as caught:
        world.run(lock, target)
    assert "pystray" in str(caught.value)


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
    hooks = Hooks(
        install=bootstrap.pip_install,
        progress=_no_window,
        add_site_dir=world.add_site_dir,
        find_spec=world.find_spec,
    )
    with pytest.raises(BootstrapError, match="No matching distribution found for x"):
        ensure_dependencies(app_name="send_to_window", lock=lock, target=target, hooks=hooks)


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


# ── the window itself, and who calls it (#663) ───────────────────────────────


class FakeTk:
    """Stands in for tkinter.Tk: records the title, runs scheduled calls at once."""

    def __init__(self) -> None:
        self.titles: list[str] = []

    def title(self, text: str) -> None:
        self.titles.append(text)

    def resizable(self, *_args: object) -> None: ...

    def attributes(self, *_args: object) -> None: ...

    def after(self, _ms: int, callback: Callable[[], None]) -> None:
        callback()

    def destroy(self) -> None: ...

    def mainloop(self) -> None: ...


class FakeWidget:
    def __init__(self, *_args: object, **_kwargs: object) -> None: ...

    def pack(self, **_kwargs: object) -> None: ...

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
        "Python File Launcher - first start", "Installing...", lambda: ran.append(True)
    )
    assert ran == [True]
    [root] = roots
    assert root.titles == ["Python File Launcher - first start"]


def test_macros_starts_its_first_start_under_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names: list[str] = []

    def stop(*, app_name: str) -> None:
        names.append(app_name)
        raise BootstrapError("stop before the app runs")

    monkeypatch.setattr(entry, "ensure_dependencies", stop)
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *_args: 0)
    assert entry.main() == 1
    assert names == ["send_to_window"]
