"""First-start dependency install (OP #643).

pip, the import system and the progress window are replaced with in-memory
fakes; the install folders are real temp directories.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

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

    def run(self, lock: Path, target: Path) -> None:
        hooks = Hooks(
            install=self.install,
            progress=_no_window,
            add_site_dir=self.add_site_dir,
            find_spec=self.find_spec,
        )
        ensure_dependencies(lock=lock, target=target, hooks=hooks)


def _no_window(_message: str, work: Callable[[], None]) -> None:
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
        ensure_dependencies(lock=lock, target=target, hooks=hooks)


def argv_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]
