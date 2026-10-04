"""Tests for launcher.pyw's interpreter choice, script discovery and folder box."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets


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
    # The acceptance case: every app lives in plugins/<app>/.
    assert _find(launcher, ROOT / "plugins") == [
        "macros/Macros.py",
        "recipe_calc/Recpe_Calc.py",
    ]


def test_this_repo_has_no_apps_outside_plugins(launcher: ModuleType) -> None:
    assert _find(launcher, ROOT) == []


def test_undeclared_python_files_are_not_apps(launcher: ModuleType, tmp_path: Path) -> None:
    _app(tmp_path / "main", "main.py")
    _touch(tmp_path / "main" / "helper.py")
    _touch(tmp_path / "loose.py")
    _touch(tmp_path / "pkg" / "__init__.py")
    _touch(tmp_path / "pkg" / "app.py")
    assert _find(launcher, tmp_path) == ["main/main.py"]


def test_only_apps_one_level_down_are_found(launcher: ModuleType, tmp_path: Path) -> None:
    # plugins/<app>/launcher-app.toml and nothing else: not a manifest in the
    # scanned folder itself, nor one nested deeper, nor one outside it.
    plugins = tmp_path / "plugins"
    _app(plugins / "app")
    _app(plugins, "top.py")
    _app(plugins / "app" / "inner", "inner.py")
    _app(plugins / "a" / "b" / "Deep App", "deep.py")
    _app(tmp_path / "outside", "outside.py")
    assert _find(launcher, plugins) == ["app/app.py"]


@pytest.mark.parametrize("noise", ["__pycache__", ".venv", "venv", "env", ".git", "site-packages"])
def test_a_folder_named_like_noise_above_the_plugins_dir_does_not_hide_apps(
    launcher: ModuleType, tmp_path: Path, noise: str
) -> None:
    # Only the plugins dir's direct children are looked at, so where that dir
    # sits on disk cannot hide an app.
    plugins = tmp_path / noise / "plugins"
    _app(plugins / "keep")
    assert _find(launcher, plugins) == ["keep/app.py"]


def test_pyw_and_a_script_in_a_subfolder_of_the_manifest_are_accepted(
    launcher: ModuleType, tmp_path: Path
) -> None:
    _app(tmp_path / "gui", "gui.pyw")
    _touch(tmp_path / "tool" / "bin" / "run.py")
    _manifest(tmp_path / "tool", 'script = "bin/run.py"')
    assert _find(launcher, tmp_path) == ["gui/gui.pyw", "tool/bin/run.py"]


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
    entry: QtWidgets.QLineEdit
    errors: list[str]
    warnings: list[str]


def _listed(window: _Window) -> list[str]:
    tree = window.app.tree
    folders = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    return sorted(
        folder.child(j).text(0).strip() for folder in folders for j in range(folder.childCount())
    )


def _type(window: _Window, text: str) -> None:
    window.entry.setText(text)


def _commit_by_leaving(window: _Window) -> None:
    # Real focus changes need a window holding OS focus, which a test cannot
    # rely on; the FocusOut event is delivered straight to the widget instead.
    focus_out = QtGui.QFocusEvent(QtCore.QEvent.Type.FocusOut, QtCore.Qt.FocusReason.TabFocusReason)
    QtWidgets.QApplication.sendEvent(window.entry, focus_out)


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    # No real windows during a test run. Set before the QApplication exists,
    # which is never destroyed: Qt allows only one per process.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert isinstance(app, QtWidgets.QApplication)
    return app


@pytest.fixture
def window(
    launcher: ModuleType,
    qapp: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_Window]:
    start = tmp_path / "start"
    _app(start / "first", "first.py")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"last_folder": str(start)}), encoding="utf-8")
    monkeypatch.setattr(launcher, "SETTINGS_FILE", settings)
    errors: list[str] = []
    monkeypatch.setattr(launcher, "show_error", lambda _parent, _title, msg: errors.append(msg))
    warnings: list[str] = []
    monkeypatch.setattr(launcher, "show_warning", lambda _parent, _title, msg: warnings.append(msg))

    app = launcher.PythonLauncherGUI()
    try:
        (entry,) = app.findChildren(QtWidgets.QLineEdit)
        yield _Window(app, entry, errors, warnings)
    finally:
        app.poll_timer.stop()
        app.deleteLater()


def test_with_no_saved_folder_the_repos_plugins_dir_is_scanned(
    launcher: ModuleType,
    qapp: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher, "SETTINGS_FILE", tmp_path / "never-saved.json")
    errors: list[str] = []
    monkeypatch.setattr(launcher, "show_error", lambda _parent, _title, msg: errors.append(msg))
    monkeypatch.setattr(launcher, "show_warning", lambda _parent, _title, msg: errors.append(msg))
    app = launcher.PythonLauncherGUI()
    try:
        (entry,) = app.findChildren(QtWidgets.QLineEdit)
        assert entry.text() == str(ROOT / "plugins")
        assert _listed(_Window(app, entry, errors, [])) == ["Macros.py", "Recpe_Calc.py"]
        assert errors == []
    finally:
        app.poll_timer.stop()
        app.deleteLater()


def test_folder_box_starts_with_the_saved_folder_scanned(window: _Window, tmp_path: Path) -> None:
    assert window.entry.text() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_typing_a_folder_and_leaving_the_box_rescans_it(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _app(other / "second", "second.py")
    _type(window, str(other))
    _commit_by_leaving(window)
    assert window.errors == []
    assert window.entry.text() == str(other)
    assert _listed(window) == ["second.py"]


def test_enter_commits_the_typed_folder(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _app(other / "second", "second.py")
    _type(window, str(other))
    QtTest.QTest.keyClick(window.entry, QtCore.Qt.Key.Key_Return)
    assert _listed(window) == ["second.py"]


def test_enter_rescans_even_when_the_folder_is_unchanged(window: _Window, tmp_path: Path) -> None:
    _app(tmp_path / "start" / "added", "added.py")
    window.app.commit_folder()
    assert _listed(window) == ["added.py", "first.py"]


def test_pasted_path_in_quotes_is_accepted(window: _Window, tmp_path: Path) -> None:
    other = tmp_path / "other"
    _app(other / "second", "second.py")
    _type(window, f'  "{other}"  ')
    _commit_by_leaving(window)
    assert window.errors == []
    assert window.entry.text() == str(other)
    assert _listed(window) == ["second.py"]


def test_missing_folder_is_rejected_and_the_previous_one_kept(
    window: _Window, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"
    _type(window, str(missing))
    _commit_by_leaving(window)
    assert len(window.errors) == 1
    assert str(missing) in window.errors[0]
    assert window.entry.text() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_a_file_is_rejected_as_a_folder(window: _Window, tmp_path: Path) -> None:
    _type(window, str(tmp_path / "start" / "first" / "first.py"))
    _commit_by_leaving(window)
    assert len(window.errors) == 1
    assert window.entry.text() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


@pytest.mark.parametrize("text", ["", "   ", '""'])
def test_empty_folder_box_is_rejected_and_the_previous_one_kept(
    window: _Window, tmp_path: Path, text: str
) -> None:
    _type(window, text)
    _commit_by_leaving(window)
    assert window.errors == ["Enter a folder to scan."]
    assert window.entry.text() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_scan_button_with_a_bad_typed_path_keeps_the_previous_folder(
    window: _Window, tmp_path: Path
) -> None:
    _type(window, str(tmp_path / "does-not-exist"))
    window.app.scan_folder()
    assert len(window.errors) == 1
    assert window.entry.text() == str(tmp_path / "start")
    assert _listed(window) == ["first.py"]


def test_leaving_the_box_unchanged_does_not_rescan(window: _Window, tmp_path: Path) -> None:
    _app(tmp_path / "start" / "added", "added.py")
    _commit_by_leaving(window)
    assert _listed(window) == ["first.py"]


def test_browse_still_updates_the_box_and_rescans(
    launcher: ModuleType, window: _Window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    _app(other / "second", "second.py")
    monkeypatch.setattr(launcher, "ask_directory", lambda _parent, _title, _start: str(other))
    window.app.pick_folder()
    assert window.entry.text() == str(other)
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
    _touch(other / "nested" / "deeper" / "deep.py")
    _manifest(other / "nested" / "deeper", 'script = "deep.py"')
    _type(window, str(other))
    window.app.commit_folder()
    assert window.errors == []
    assert window.warnings == []
    assert _listed(window) == []


# ── launching ────────────────────────────────────────────────────────────────


class _FakeProc:
    """Stands in for a launched script's Popen handle."""

    pid = 4242

    def __init__(self) -> None:
        self.alive = True

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def started(launcher: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, _FakeProc]]:
    procs: list[tuple[Any, _FakeProc]] = []

    def popen(args: Any, **_kw: Any) -> _FakeProc:
        proc = _FakeProc()
        procs.append((args, proc))
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    return procs


def _launch_first(window: _Window, tmp_path: Path) -> str:
    script = str(tmp_path / "start" / "first" / "first.py")
    window.app.tree.setCurrentItem(window.app.items_by_path[script])
    window.app.launch_button.click()
    return script


def test_launch_shows_the_pid_and_stop_clears_it(
    launcher: ModuleType,
    window: _Window,
    tmp_path: Path,
    started: list[tuple[Any, _FakeProc]],
) -> None:
    assert not window.app.stop_button.isEnabled()
    script = _launch_first(window, tmp_path)
    ((args, proc),) = started
    assert args == [launcher.PYTHON_EXE, script]
    item = window.app.items_by_path[script]
    assert item.text(1) == "PID 4242"
    assert window.app.stop_button.isEnabled()
    assert window.app.launch_button.text() == "Restart Selected File"

    window.app.stop_button.click()
    assert not proc.alive
    assert item.text(1) == ""
    assert not window.app.stop_button.isEnabled()
    assert window.app.launch_button.text() == "Launch Selected File"


def test_a_script_that_exits_is_cleared_by_the_poll(
    window: _Window, tmp_path: Path, started: list[tuple[Any, _FakeProc]]
) -> None:
    script = _launch_first(window, tmp_path)
    ((_args, proc),) = started
    proc.alive = False
    window.app.poll_processes()
    assert window.app.items_by_path[script].text(1) == ""
    assert not window.app.stop_button.isEnabled()


# ── first start ──────────────────────────────────────────────────────────────


def test_a_failed_first_start_install_is_shown_and_exits(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> None:
        raise launcher.BootstrapError("no network")

    monkeypatch.setattr(launcher, "ensure_dependencies", fail)
    shown: list[str] = []
    monkeypatch.setattr(launcher, "show_native_error", lambda _title, msg: shown.append(msg))
    with pytest.raises(SystemExit) as exc:
        launcher.bootstrap()
    assert exc.value.code == 1
    assert shown == ["no network"]
