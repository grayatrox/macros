"""Every plugin declares its own dependencies, and every one is hash-pinned.

See docs/adr/0003-per-plugin-pyproject.md and docs/adr/0005-root-declares-only-the-launcher.md.
A plugin's ``pyproject.toml`` says what that app needs; the root
``pyproject.toml`` says what the launcher needs and nothing more. The locks are
compiled from all of them together (``tools/lock.py``).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tools import lock

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


def _plugin_dirs() -> list[Path]:
    return sorted(manifest.parent for manifest in PLUGINS.glob("*/launcher-app.toml"))


def _project(pyproject: Path) -> dict[str, Any]:
    table: dict[str, Any] = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return table


def _requirements(project: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(runtime dependencies, requirements of every optional extra)."""
    extras: dict[str, list[str]] = project.get("optional-dependencies", {})
    return list(project["dependencies"]), [req for reqs in extras.values() for req in reqs]


def _name(requirement: str) -> str:
    """PEP 503-normalised distribution name of a requirement string."""
    match = re.match(r"[A-Za-z0-9._-]+", requirement)
    assert match, requirement
    return re.sub(r"[-_.]+", "-", match.group()).lower()


def _locked_names(lock: Path) -> set[str]:
    pins = re.findall(r"^([A-Za-z0-9._-]+)==", lock.read_text(encoding="utf-8"), re.MULTILINE)
    return {_name(pin) for pin in pins}


ROOT_RUNTIME, _ = _requirements(_project(ROOT / "pyproject.toml"))


def test_this_repo_has_both_plugins() -> None:
    assert [p.name for p in _plugin_dirs()] == ["macros", "recipe_calc"]


@pytest.mark.parametrize("plugin", _plugin_dirs(), ids=lambda p: p.name)
def test_each_plugin_has_a_pyproject_declaring_its_dependencies(plugin: Path) -> None:
    project = _project(plugin / "pyproject.toml")

    assert project["name"]
    assert project["version"]
    assert project["requires-python"] == ">=3.12"
    assert isinstance(project["dependencies"], list)


def test_the_root_declares_only_the_launcher() -> None:
    # launcher.pyw's own runtime need is its Qt GUI; every app's needs are in its plugin.
    assert {_name(r) for r in ROOT_RUNTIME} == {"pyside6-essentials"}
    assert set(_project(ROOT / "pyproject.toml").get("optional-dependencies", {})) == {"dev"}


@pytest.mark.parametrize("plugin", _plugin_dirs(), ids=lambda p: p.name)
def test_the_locks_are_compiled_from_each_plugins_pyproject(plugin: Path) -> None:
    # uv records each requirement's source as "name (path)"; a lock compiled
    # without the plugin's file would not name it.
    source = f"({(plugin / 'pyproject.toml').relative_to(ROOT).as_posix()})"
    for name in ("requirements.lock", "requirements-dev.lock"):
        assert source in (ROOT / name).read_text(encoding="utf-8"), f"{name} lacks {source}"


def test_lock_compiles_every_plugin_that_has_a_manifest(tmp_path: Path) -> None:
    for name, files in {
        "app": ("launcher-app.toml", "pyproject.toml"),
        "no_manifest": ("pyproject.toml",),
        "no_pyproject": ("launcher-app.toml",),
    }.items():
        (tmp_path / name).mkdir()
        for file in files:
            (tmp_path / name / file).write_text("", encoding="utf-8")

    # A manifest without a pyproject.toml is a plugin that fails the test above;
    # lock.py must not hide that by skipping it silently.
    with pytest.raises(FileNotFoundError, match="no_pyproject"):
        lock.plugin_projects(tmp_path)

    (tmp_path / "no_pyproject" / "launcher-app.toml").unlink()
    assert lock.plugin_projects(tmp_path) == [tmp_path / "app" / "pyproject.toml"]


def test_lock_finds_this_repos_plugins() -> None:
    assert lock.plugin_projects() == [p / "pyproject.toml" for p in _plugin_dirs()]


@pytest.mark.parametrize("plugin", _plugin_dirs(), ids=lambda p: p.name)
def test_plugin_requirements_are_hash_pinned(plugin: Path) -> None:
    runtime, extras = _requirements(_project(plugin / "pyproject.toml"))

    runtime_lock = _locked_names(ROOT / "requirements.lock")
    dev_lock = _locked_names(ROOT / "requirements-dev.lock")
    assert {_name(r) for r in runtime} <= runtime_lock
    assert {_name(r) for r in extras} <= dev_lock


def test_the_lock_parser_reads_pins() -> None:
    assert "pyside6-essentials" in _locked_names(ROOT / "requirements.lock")
    assert _name("PySide6_Essentials>=6.11,<6.12") == "pyside6-essentials"
