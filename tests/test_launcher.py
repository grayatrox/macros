"""Tests for launcher.pyw's interpreter choice and script discovery."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


# ── resolve_console_python ───────────────────────────────────────────────────


def test_pythonw_is_swapped_for_python_beside_it(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pythonw, python = _touch(tmp_path / "pythonw.exe"), _touch(tmp_path / "python.exe")
    monkeypatch.setattr(sys, "executable", str(pythonw))
    monkeypatch.setattr(sys, "platform", "win32")
    assert launcher.resolve_console_python() == str(python)


def test_pythonw_kept_when_no_console_python_exists(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pythonw = _touch(tmp_path / "pythonw.exe")
    monkeypatch.setattr(sys, "executable", str(pythonw))
    monkeypatch.setattr(sys, "platform", "win32")
    assert launcher.resolve_console_python() == str(pythonw)


def test_console_python_is_used_as_is(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = _touch(tmp_path / "python.exe")
    _touch(tmp_path / "pythonw.exe")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(sys, "platform", "win32")
    assert launcher.resolve_console_python() == str(python)


# ── find_scripts ─────────────────────────────────────────────────────────────


def _find(launcher: ModuleType, folder: Path) -> list[str]:
    # find_scripts reads no instance state, so the Tk window is not needed.
    found = launcher.PythonLauncherGUI.find_scripts(None, folder)
    return sorted(p.relative_to(folder).as_posix() for p in found)


def test_find_scripts_lists_py_and_pyw_recursively(
    launcher: ModuleType, tmp_path: Path
) -> None:
    _touch(tmp_path / "a.py")
    _touch(tmp_path / "gui.pyw")
    _touch(tmp_path / "sub" / "b.py")
    _touch(tmp_path / "notes.txt")
    assert _find(launcher, tmp_path) == ["a.py", "gui.pyw", "sub/b.py"]


@pytest.mark.parametrize(
    "skip", ["__pycache__", ".venv", "venv", ".git", "site-packages"]
)
def test_find_scripts_skips_noise_directories(
    launcher: ModuleType, tmp_path: Path, skip: str
) -> None:
    _touch(tmp_path / "keep.py")
    _touch(tmp_path / skip / "nested" / "hidden.py")
    assert _find(launcher, tmp_path) == ["keep.py"]


def test_find_scripts_excludes_the_launcher_itself(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = _touch(tmp_path / "launcher.pyw")
    _touch(tmp_path / "other.py")
    monkeypatch.setattr(launcher, "SELF_PATH", me.resolve())
    assert _find(launcher, tmp_path) == ["other.py"]


def test_find_scripts_empty_folder(launcher: ModuleType, tmp_path: Path) -> None:
    assert _find(launcher, tmp_path) == []
