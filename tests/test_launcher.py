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


# ── find_apps ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]


def _app(folder: Path, script: str = "app.py") -> Path:
    """Make an app: a script plus the launcher-app.toml that declares it."""
    _touch(folder / script)
    return _manifest(folder, f'script = "{script}"')


def _manifest(folder: Path, text: str) -> Path:
    path = folder / "launcher-app.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _find(launcher: ModuleType, folder: Path) -> list[str]:
    found = launcher.find_apps(folder)
    assert found.problems == []
    return sorted(p.relative_to(folder).as_posix() for p in found.scripts)


def _problems(launcher: ModuleType, folder: Path) -> list[str]:
    return list(launcher.find_apps(folder).problems)


def test_this_repo_lists_only_its_declared_apps(launcher: ModuleType) -> None:
    # The acceptance case: nothing under src/, tests/ or tools/, apps at the
    # root and in a subfolder still found.
    assert _find(launcher, ROOT) == ["Macros.py", "Recipe Calc/Recpe_Calc.py"]


def test_undeclared_python_files_are_not_apps(launcher: ModuleType, tmp_path: Path) -> None:
    _app(tmp_path, "main.py")
    _touch(tmp_path / "helper.py")
    _touch(tmp_path / "src" / "pkg" / "__init__.py")
    _touch(tmp_path / "src" / "pkg" / "app.py")
    _touch(tmp_path / "tests" / "test_app.py")
    _touch(tmp_path / "tools" / "tasks.py")
    assert _find(launcher, tmp_path) == ["main.py"]


def test_an_app_nested_several_levels_deep_is_found(launcher: ModuleType, tmp_path: Path) -> None:
    _app(tmp_path / "a" / "b" / "c" / "Deep App", "deep.py")
    assert _find(launcher, tmp_path) == ["a/b/c/Deep App/deep.py"]


def test_pyw_and_a_script_in_a_subfolder_of_the_manifest_are_accepted(
    launcher: ModuleType, tmp_path: Path
) -> None:
    _app(tmp_path / "gui", "gui.pyw")
    _touch(tmp_path / "tool" / "bin" / "run.py")
    _manifest(tmp_path / "tool", 'script = "bin/run.py"')
    assert _find(launcher, tmp_path) == ["gui/gui.pyw", "tool/bin/run.py"]


@pytest.mark.parametrize("skip", ["__pycache__", ".venv", "venv", ".git", "site-packages"])
def test_find_apps_skips_noise_directories(launcher: ModuleType, tmp_path: Path, skip: str) -> None:
    _app(tmp_path / "keep")
    _app(tmp_path / skip / "nested")
    assert _find(launcher, tmp_path) == ["keep/app.py"]


def test_find_apps_excludes_the_launcher_itself(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = _app(tmp_path / "me", "launcher.pyw").with_name("launcher.pyw")
    _app(tmp_path / "other")
    monkeypatch.setattr(launcher, "SELF_PATH", me.resolve())
    assert _find(launcher, tmp_path) == ["other/app.py"]


def test_find_apps_empty_folder(launcher: ModuleType, tmp_path: Path) -> None:
    assert _find(launcher, tmp_path) == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("script = ", "not valid TOML"),
        ("name = 'x'", "no 'script'"),
        ("script = 3", "no 'script'"),
        ("script = ''", "no 'script'"),
        ("script = 'notes.txt'", ".py or .pyw"),
        ("script = 'missing.py'", "does not exist"),
        ("script = '../outside.py'", "inside"),
        ("script = 'C:/Windows/evil.py'", "relative"),
        ("script = '/evil.py'", "relative"),
    ],
)
def test_a_bad_manifest_is_reported_and_good_apps_still_listed(
    launcher: ModuleType, tmp_path: Path, text: str, reason: str
) -> None:
    _app(tmp_path / "good")
    _touch(tmp_path / "outside.py")
    _touch(tmp_path / "bad" / "notes.txt")
    bad = _manifest(tmp_path / "bad", text)
    found = launcher.find_apps(tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in found.scripts] == ["good/app.py"]
    (problem,) = found.problems
    assert str(bad) in problem
    assert reason in problem


def test_a_manifest_naming_a_folder_is_reported(launcher: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "bad" / "dir.py").mkdir(parents=True)
    _manifest(tmp_path / "bad", "script = 'dir.py'")
    (problem,) = _problems(launcher, tmp_path)
    assert "does not exist" in problem


# ── folder box ───────────────────────────────────────────────────────────────


@dataclass
class _Window:
    app: Any
    entry: ttk.Entry
    errors: list[str]
    warnings: list[str]


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
    _app(start, "first.py")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"last_folder": str(start)}), encoding="utf-8")
    monkeypatch.setattr(launcher, "SETTINGS_FILE", settings)
    errors: list[str] = []
    monkeypatch.setattr(launcher.messagebox, "showerror", lambda _title, msg: errors.append(msg))
    warnings: list[str] = []
    monkeypatch.setattr(
        launcher.messagebox, "showwarning", lambda _title, msg: warnings.append(msg)
    )

    top = tk.Toplevel(tk_root)
    top.withdraw()
    try:
        app = launcher.PythonLauncherGUI(top)
        (entry,) = _entries(top)
        yield _Window(app, entry, errors, warnings)
    finally:
        top.destroy()


def test_folder_box_starts_with_the_saved_folder_scanned(window: _Window, tmp_path: Path) -> None:
    assert window.entry.get() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_typing_a_folder_and_leaving_the_box_rescans_it(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _app(other, "second.py")
    _type(window, str(other))
    _commit_by_leaving(window)
    assert window.errors == []
    assert window.app.selected_folder.get() == str(other)
    assert _listed(window) == ["second.py"]


def test_enter_commits_the_typed_folder(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _app(other, "second.py")
    assert window.entry.bind("<Return>")
    _type(window, str(other))
    window.app.commit_folder()
    assert _listed(window) == ["second.py"]


def test_enter_rescans_even_when_the_folder_is_unchanged(window: _Window, tmp_path: Path) -> None:
    _app(tmp_path / "start" / "added", "added.py")
    window.app.commit_folder()
    assert _listed(window) == ["added.py", "first.py"]


def test_pasted_path_in_quotes_is_accepted(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _app(other, "second.py")
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
    _app(tmp_path / "start" / "added", "added.py")
    _commit_by_leaving(window)
    assert _listed(window) == ["first.py"]


def test_browse_still_updates_the_box_and_rescans(
    launcher: ModuleType, window: _Window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    _app(other, "second.py")
    monkeypatch.setattr(launcher.filedialog, "askdirectory", lambda **_kw: str(other))
    window.app.pick_folder()
    assert window.entry.get() == str(other)
    assert _listed(window) == ["second.py"]


def test_bad_manifests_are_shown_in_a_warning_and_good_apps_still_listed(
    window: _Window, tmp_path: Path
) -> None:
    bad = _manifest(tmp_path / "start" / "broken", "script = 'gone.py'")
    window.app.commit_folder()
    assert window.errors == []
    (warning,) = window.warnings
    assert str(bad) in warning
    assert _listed(window) == ["first.py"]


def test_a_folder_with_no_apps_lists_nothing_and_does_not_warn(
    window: _Window, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    _touch(other / "loose.py")
    _type(window, str(other))
    window.app.commit_folder()
    assert window.errors == []
    assert window.warnings == []
    assert _listed(window) == []
