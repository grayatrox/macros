"""Every plugin declares its own dependencies, and every one is hash-pinned.

See docs/adr/0003-per-plugin-pyproject.md. A plugin's ``pyproject.toml`` says
what that app needs; the root ``pyproject.toml`` is what the locks are compiled
from, so a plugin requirement missing from the root would never be pinned.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

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


ROOT_RUNTIME, ROOT_EXTRAS = _requirements(_project(ROOT / "pyproject.toml"))


def test_this_repo_has_both_plugins() -> None:
    assert [p.name for p in _plugin_dirs()] == ["macros", "recipe_calc"]


@pytest.mark.parametrize("plugin", _plugin_dirs(), ids=lambda p: p.name)
def test_each_plugin_has_a_pyproject_declaring_its_dependencies(plugin: Path) -> None:
    project = _project(plugin / "pyproject.toml")

    assert project["name"]
    assert project["version"]
    assert project["requires-python"] == ">=3.12"
    assert isinstance(project["dependencies"], list)


@pytest.mark.parametrize("plugin", _plugin_dirs(), ids=lambda p: p.name)
def test_plugin_requirements_match_the_root(plugin: Path) -> None:
    runtime, extras = _requirements(_project(plugin / "pyproject.toml"))

    for requirement in runtime:
        assert requirement in ROOT_RUNTIME, f"{requirement} is not a root runtime dependency"
    for requirement in extras:
        assert requirement in ROOT_RUNTIME + ROOT_EXTRAS, f"{requirement} is not in the root"


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
