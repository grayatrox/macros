"""Tests for launcher.pyw's interpreter choice, script discovery and folder box."""

from __future__ import annotations

import json
import sys
import tkinter as tk
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from types import ModuleType
from typing import Any

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


def test_find_scripts_lists_py_and_pyw_recursively(launcher: ModuleType, tmp_path: Path) -> None:
    _touch(tmp_path / "a.py")
    _touch(tmp_path / "gui.pyw")
    _touch(tmp_path / "sub" / "b.py")
    _touch(tmp_path / "notes.txt")
    assert _find(launcher, tmp_path) == ["a.py", "gui.pyw", "sub/b.py"]


@pytest.mark.parametrize("skip", ["__pycache__", ".venv", "venv", ".git", "site-packages"])
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


# ── folder box ───────────────────────────────────────────────────────────────


@dataclass
class _Window:
    app: Any
    entry: ttk.Entry
    errors: list[str]


def _entries(widget: tk.Misc) -> list[ttk.Entry]:
    found = [widget] if isinstance(widget, ttk.Entry) else []
    for child in widget.winfo_children():
        found += _entries(child)
    return found


def _listed(window: _Window) -> list[str]:
    tree = window.app.tree
    return sorted(
        tree.item(item, "text").strip()
        for folder in tree.get_children()
        for item in tree.get_children(folder)
    )


def _type(window: _Window, text: str) -> None:
    window.entry.delete(0, "end")
    window.entry.insert(0, text)


def _commit_by_leaving(window: _Window) -> None:
    # Generated key events only reach the window holding OS focus, which a test
    # cannot rely on; FocusOut is delivered straight to the widget.
    window.entry.event_generate("<FocusOut>")


@pytest.fixture(scope="module")
def tk_root() -> Iterator[tk.Tk]:
    # One interpreter for the module: creating a fresh tk.Tk per test
    # intermittently fails on Windows to read Tcl's own init scripts.
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(
    launcher: ModuleType, tk_root: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_Window]:
    start = tmp_path / "start"
    _touch(start / "first.py")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"last_folder": str(start)}), encoding="utf-8")
    monkeypatch.setattr(launcher, "SETTINGS_FILE", settings)
    errors: list[str] = []
    monkeypatch.setattr(launcher.messagebox, "showerror", lambda _title, msg: errors.append(msg))

    top = tk.Toplevel(tk_root)
    top.withdraw()
    try:
        app = launcher.PythonLauncherGUI(top)
        (entry,) = _entries(top)
        yield _Window(app, entry, errors)
    finally:
        top.destroy()


def test_folder_box_starts_with_the_saved_folder_scanned(window: _Window, tmp_path: Path) -> None:
    assert window.entry.get() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_typing_a_folder_and_leaving_the_box_rescans_it(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _touch(other / "second.py")
    _type(window, str(other))
    _commit_by_leaving(window)
    assert window.errors == []
    assert window.app.selected_folder.get() == str(other)
    assert _listed(window) == ["second.py"]


def test_enter_commits_the_typed_folder(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _touch(other / "second.py")
    assert window.entry.bind("<Return>")
    _type(window, str(other))
    window.app.commit_folder()
    assert _listed(window) == ["second.py"]


def test_enter_rescans_even_when_the_folder_is_unchanged(window: _Window, tmp_path: Path) -> None:
    _touch(tmp_path / "start" / "added.py")
    window.app.commit_folder()
    assert _listed(window) == ["added.py", "first.py"]


def test_pasted_path_in_quotes_is_accepted(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _touch(other / "second.py")
    _type(window, f'  "{other}"  ')
    _commit_by_leaving(window)
    assert window.errors == []
    assert window.entry.get() == str(other)
    assert _listed(window) == ["second.py"]


def test_missing_folder_is_rejected_and_the_previous_one_kept(
    window: _Window, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"
    _type(window, str(missing))
    _commit_by_leaving(window)
    assert len(window.errors) == 1
    assert str(missing) in window.errors[0]
    assert window.entry.get() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_a_file_is_rejected_as_a_folder(window: _Window, tmp_path: Path) -> None:
    _type(window, str(tmp_path / "start" / "first.py"))
    _commit_by_leaving(window)
    assert len(window.errors) == 1
    assert window.entry.get() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


@pytest.mark.parametrize("text", ["", "   ", '""'])
def test_empty_folder_box_is_rejected_and_the_previous_one_kept(
    window: _Window, tmp_path: Path, text: str
) -> None:
    _type(window, text)
    _commit_by_leaving(window)
    assert window.errors == ["Enter a folder to scan."]
    assert window.entry.get() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_scan_button_with_a_bad_typed_path_keeps_the_previous_folder(
    window: _Window, tmp_path: Path
) -> None:
    _type(window, str(tmp_path / "does-not-exist"))
    window.app.scan_folder()
    assert len(window.errors) == 1
    assert window.entry.get() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_leaving_the_box_unchanged_does_not_rescan(window: _Window, tmp_path: Path) -> None:
    _touch(tmp_path / "start" / "added.py")
    _commit_by_leaving(window)
    assert _listed(window) == ["first.py"]


def test_browse_still_updates_the_box_and_rescans(
    launcher: ModuleType, window: _Window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    _touch(other / "second.py")
    monkeypatch.setattr(launcher.filedialog, "askdirectory", lambda **_kw: str(other))
    window.app.pick_folder()
    assert window.entry.get() == str(other)
    assert _listed(window) == ["second.py"]
