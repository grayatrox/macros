"""Launcher apps' declared dependencies: checking and installing them (OP #666).

pip is replaced by a fake that writes real ``.dist-info`` metadata, so version
lookups go through the real importlib.metadata; folders are temp directories.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pluginhost.bootstrap import MARKER_NAME
from pluginhost.plugin_deps import (
    DepsStatus,
    PluginDeps,
    PluginDepsError,
    check,
    read_pins,
    read_requirements,
    requirement_name,
    version_on,
)

ROOT = Path(__file__).resolve().parents[1]


def _dist(folder: Path, name: str, version: str) -> None:
    """What pip leaves behind for an installed distribution, as far as metadata goes."""
    info = folder / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )


def _app(folder: Path, *requirements: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{r}"' for r in requirements)
    (folder / "pyproject.toml").write_text(
        f'[project]\nname = "app"\nversion = "0"\ndependencies = [{listed}]\n', encoding="utf-8"
    )
    return folder


class FakePip:
    """Stands in for bootstrap.pip_install: 'installs' the lock's pins as metadata."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[Path, Path]] = []
        self.fail = fail

    def __call__(self, lock: Path, target: Path) -> None:
        self.calls.append((lock, target))
        if self.fail:
            raise subprocess.CalledProcessError(1, ["pip"], output="ERROR: hash mismatch for x")
        for name, version in read_pins(lock).items():
            _dist(target, name, version)


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    path = tmp_path / "requirements.lock"
    path.write_text(
        "fakepkg==1.0 \\\n    --hash=sha256:aaaa\n    # via app\n"
        "other-pkg==2.0.post1 \\\n    --hash=sha256:bbbb\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def pip() -> FakePip:
    return FakePip()


@pytest.fixture
def deps(lock: Path, tmp_path: Path, pip: FakePip) -> PluginDeps:
    return PluginDeps(lock=lock, target=tmp_path / "deps-py312", install=pip)


# ── parsing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("requirement", "name"),
    [
        ("pillow>=11,<12", "pillow"),
        ("PySide6_Essentials>=6.11,<6.12", "pyside6-essentials"),
        ("  requests[socks] >= 2 ", "requests"),
        ("pywin32>=311; sys_platform == 'win32'", "pywin32"),
        ("zope.interface", "zope-interface"),
    ],
)
def test_requirement_name_is_the_normalised_distribution_name(requirement: str, name: str) -> None:
    assert requirement_name(requirement) == name


@pytest.mark.parametrize("requirement", ["", "   ", ">=1.0", "; python_version > '3'"])
def test_a_requirement_without_a_name_is_rejected(requirement: str) -> None:
    with pytest.raises(PluginDepsError, match="not a requirement"):
        requirement_name(requirement)


def test_read_pins_reads_name_and_version(lock: Path) -> None:
    assert read_pins(lock) == {"fakepkg": "1.0", "other-pkg": "2.0.post1"}


def test_read_pins_reads_the_real_lock() -> None:
    pins = read_pins(ROOT / "requirements.lock")
    assert pins["pillow"] == "11.3.0"
    assert "pyside6-essentials" in pins


def test_an_unreadable_lock_is_reported(tmp_path: Path) -> None:
    with pytest.raises(PluginDepsError, match="lock file could not be read"):
        read_pins(tmp_path / "absent.lock")


def test_requirements_come_from_the_apps_pyproject(tmp_path: Path) -> None:
    app = _app(tmp_path / "app", "fakepkg>=1", "other-pkg")
    assert read_requirements(app) == ["fakepkg>=1", "other-pkg"]


def test_an_app_without_a_pyproject_needs_nothing(tmp_path: Path) -> None:
    assert read_requirements(tmp_path) == []


def test_a_pyproject_without_dependencies_needs_nothing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert read_requirements(tmp_path) == []


def test_extras_are_not_requirements(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fakepkg"]\n'
        '[project.optional-dependencies]\nx = ["other-pkg"]\n',
        encoding="utf-8",
    )
    assert read_requirements(tmp_path) == ["fakepkg"]


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(PluginDepsError, match="not valid TOML"):
        read_requirements(tmp_path)


@pytest.mark.parametrize("value", ['"fakepkg"', "[1, 2]", "{a = 1}"])
def test_dependencies_that_are_not_a_list_of_strings_are_reported(
    tmp_path: Path, value: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"[project]\ndependencies = {value}\n", encoding="utf-8"
    )
    with pytest.raises(PluginDepsError, match="list of strings"):
        read_requirements(tmp_path)


# ── check ────────────────────────────────────────────────────────────────────

PINS = {"fakepkg": "1.0", "other-pkg": "2.0"}


def test_requirements_present_at_their_pinned_version_are_satisfied() -> None:
    status = check(["fakepkg>=1", "Other_Pkg"], PINS, PINS.get)
    assert status == DepsStatus(missing=[], unpinned=[])


def test_absent_or_wrong_version_is_missing_as_the_locked_pin() -> None:
    installed = {"other-pkg": "1.9"}
    status = check(["fakepkg", "other-pkg>=1"], PINS, installed.get)
    assert status == DepsStatus(missing=["fakepkg==1.0", "other-pkg==2.0"], unpinned=[])


def test_a_requirement_the_lock_does_not_pin_is_unpinned_not_missing() -> None:
    status = check(["requests>=2 "], PINS, lambda _name: None)
    assert status == DepsStatus(missing=[], unpinned=["requests>=2"])


def test_no_requirements_means_nothing_to_do() -> None:
    assert check([], PINS, lambda _name: None) == DepsStatus([], [])


def test_version_on_reads_metadata_and_the_first_folder_wins(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _dist(first, "fakepkg", "1.0")
    _dist(second, "fakepkg", "0.9")
    _dist(second, "other_pkg", "2.0")
    version_of = version_on([str(first), str(second)])
    assert version_of("fakepkg") == "1.0"
    assert version_of("other-pkg") == "2.0"
    assert version_of("absent") is None


# ── PluginDeps ───────────────────────────────────────────────────────────────


def test_this_repos_plugins_need_no_install_in_the_dev_environment(tmp_path: Path) -> None:
    # The dev lock pins the runtime packages at the runtime lock's versions, so
    # the environment the suite runs in satisfies both apps: no install on scan.
    deps = PluginDeps(target=tmp_path / "unused", install=FakePip(fail=True))
    for plugin in ("macros", "recipe_calc"):
        assert deps.status(ROOT / "plugins" / plugin, active=False) == DepsStatus([], [])


def test_status_sees_the_install_folder_only_once_active(deps: PluginDeps, tmp_path: Path) -> None:
    app = _app(tmp_path / "app", "fakepkg>=1")
    _dist(deps.target, "fakepkg", "1.0")
    assert deps.status(app, active=False).missing == ["fakepkg==1.0"]
    assert deps.status(app, active=True) == DepsStatus([], [])


def test_ensure_installed_installs_the_lock_into_the_folder(
    deps: PluginDeps, pip: FakePip, lock: Path, tmp_path: Path
) -> None:
    app = _app(tmp_path / "app", "fakepkg>=1")
    assert deps.ensure_installed(active=False) is True

    ((installed_lock, _staging),) = pip.calls
    assert installed_lock == lock
    assert (deps.target / MARKER_NAME).is_file()
    assert deps.status(app, active=True) == DepsStatus([], [])


def test_ensure_installed_does_not_reinstall_a_current_folder(
    deps: PluginDeps, pip: FakePip
) -> None:
    deps.ensure_installed(active=False)
    assert deps.ensure_installed(active=False) is False
    assert deps.ensure_installed(active=True) is False
    assert len(pip.calls) == 1


def test_a_changed_lock_reinstalls_when_the_folder_is_not_in_use(
    deps: PluginDeps, pip: FakePip, lock: Path
) -> None:
    deps.ensure_installed(active=False)
    lock.write_text("fakepkg==1.1 \\\n    --hash=sha256:cccc\n", encoding="utf-8")
    assert deps.ensure_installed(active=False) is True
    assert len(pip.calls) == 2


def test_a_folder_in_use_is_never_swapped_for_a_changed_lock(
    deps: PluginDeps, pip: FakePip, lock: Path
) -> None:
    deps.ensure_installed(active=False)
    lock.write_text("fakepkg==1.1 \\\n    --hash=sha256:cccc\n", encoding="utf-8")
    with pytest.raises(PluginDepsError, match="Restart the launcher"):
        deps.ensure_installed(active=True)
    assert len(pip.calls) == 1


def test_a_failed_install_reports_pips_reason_and_keeps_the_old_folder(
    lock: Path, tmp_path: Path
) -> None:
    target = tmp_path / "deps-py312"
    PluginDeps(lock=lock, target=target, install=FakePip()).ensure_installed(active=False)
    lock.write_text("fakepkg==1.1 \\\n    --hash=sha256:cccc\n", encoding="utf-8")

    failing = PluginDeps(lock=lock, target=target, install=FakePip(fail=True))
    with pytest.raises(PluginDepsError, match="hash mismatch for x"):
        failing.ensure_installed(active=False)
    assert (target / "fakepkg-1.0.dist-info").is_dir()


def test_a_missing_lock_is_reported_by_install(tmp_path: Path) -> None:
    deps = PluginDeps(lock=tmp_path / "absent.lock", target=tmp_path / "t", install=FakePip())
    with pytest.raises(PluginDepsError, match="lock file could not be read"):
        deps.ensure_installed(active=False)


def test_active_at_start_means_the_folder_is_on_this_process_path(
    deps: PluginDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not deps.active_at_start()
    monkeypatch.syspath_prepend(str(deps.target))
    assert deps.active_at_start()


def test_child_env_inherits_until_the_folder_is_active(deps: PluginDeps) -> None:
    assert deps.child_env(active=False, environ={"PATH": "x"}) is None
    assert deps.child_env(active=True, environ={"PATH": "x"}) == {
        "PATH": "x",
        "PYTHONPATH": str(deps.target),
    }


def test_child_env_keeps_an_existing_pythonpath_after_the_folder(deps: PluginDeps) -> None:
    env = deps.child_env(active=True, environ={"PYTHONPATH": "mine"})
    assert env == {"PYTHONPATH": os.pathsep.join([str(deps.target), "mine"])}
