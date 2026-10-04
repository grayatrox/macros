"""Tests for launcher.pyw's interpreter choice, script discovery, folder box and
installing each app's dependencies."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from pluginhost.plugin_deps import PluginDeps, read_pins


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
        "recipe_calc/Recipe_Calc.py",
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


class _Pip:
    """Stands in for pip: 'installs' the lock's pins as package metadata.

    ``release`` can hold an install open, and ``fail`` / ``provides`` make it
    fail or install nothing. Never touches the network.
    """

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.release = threading.Event()
        self.release.set()
        self.fail = False
        self.provides = True

    def __call__(self, lock: Path, target: Path) -> None:
        self.calls.append(lock)
        assert self.release.wait(10), "the test never released the install"
        if self.fail:
            raise subprocess.CalledProcessError(1, ["pip"], output="ERROR: no network")
        target.mkdir(parents=True, exist_ok=True)
        if self.provides:
            for name, version in read_pins(lock).items():
                info = target / f"{name}-{version}.dist-info"
                info.mkdir()
                (info / "METADATA").write_text(
                    f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
                    encoding="utf-8",
                )


@pytest.fixture
def pip() -> _Pip:
    return _Pip()


@pytest.fixture
def deps(tmp_path: Path, pip: _Pip) -> PluginDeps:
    lock = tmp_path / "requirements.lock"
    lock.write_text("fakepkg==1.0 \\\n    --hash=sha256:aaaa\n", encoding="utf-8")
    return PluginDeps(lock=lock, target=tmp_path / "deps-py312", install=pip)


@pytest.fixture
def window(
    launcher: ModuleType,
    qapp: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deps: PluginDeps,
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

    app = launcher.PythonLauncherGUI(deps=deps)
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
    # The real lock: this environment already satisfies both apps, so nothing installs.
    pip = _Pip()
    app = launcher.PythonLauncherGUI(deps=PluginDeps(target=tmp_path / "deps", install=pip))
    try:
        (entry,) = app.findChildren(QtWidgets.QLineEdit)
        assert entry.text() == str(ROOT / "plugins")
        assert _listed(_Window(app, entry, errors, [])) == ["Macros.py", "Recipe_Calc.py"]
        assert errors == []
        assert not app.deps_installing
        assert pip.calls == []
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
    def fail(*, app_name: str, app_dir: Path) -> None:
        raise launcher.BootstrapError("no network")

    monkeypatch.setattr(launcher, "ensure_dependencies", fail)
    shown: list[str] = []
    monkeypatch.setattr(launcher, "show_native_error", lambda _title, msg: shown.append(msg))
    with pytest.raises(SystemExit) as exc:
        launcher.bootstrap()
    assert exc.value.code == 1
    assert shown == ["no network"]


def test_the_first_start_install_is_named_after_the_launcher(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        launcher,
        "ensure_dependencies",
        lambda *, app_name, app_dir: calls.append((app_name, app_dir)),
    )
    launcher.bootstrap()
    # The root pyproject.toml, which declares only the launcher's own needs: not
    # Macros' packages (#689), and no second list kept beside it (#666).
    assert calls == [("Python File Launcher", ROOT)]


# ── dependencies ─────────────────────────────────────────────────────────────


def _needy(folder: Path, *requirements: str) -> str:
    """An app whose pyproject.toml declares ``requirements``; returns its script."""
    _app(folder, f"{folder.name}.py")
    listed = ", ".join(f'"{r}"' for r in requirements)
    (folder / "pyproject.toml").write_text(
        f"[project]\ndependencies = [{listed}]\n", encoding="utf-8"
    )
    return str(folder / f"{folder.name}.py")


def _wait_for_install(app: Any) -> None:
    """Let the GUI thread receive the worker's result."""
    deadline = time.monotonic() + 10
    while app.deps_installing:
        assert time.monotonic() < deadline, "the install never reported back"
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The keyword arguments of every Popen the launcher makes."""
    calls: list[dict[str, Any]] = []

    def popen(_args: Any, **kwargs: Any) -> _FakeProc:
        calls.append(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    return calls


def test_a_missing_dependency_installs_in_the_background_then_the_app_launches(
    window: _Window,
    tmp_path: Path,
    pip: _Pip,
    deps: PluginDeps,
    launched: list[dict[str, Any]],
) -> None:
    pip.release.clear()
    script = _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()

    item = window.app.items_by_path[script]
    assert item.text(1) == "installing..."
    window.app.tree.setCurrentItem(item)
    window.app.launch_button.click()
    assert launched == []
    assert "still being installed" in window.warnings[-1]

    pip.release.set()
    _wait_for_install(window.app)
    assert pip.calls == [deps.lock]
    assert window.errors == []
    assert item.text(1) == ""

    window.app.launch_button.click()
    (kwargs,) = launched
    assert kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(deps.target)


def test_apps_with_nothing_missing_launch_without_waiting(
    window: _Window, tmp_path: Path, pip: _Pip, launched: list[dict[str, Any]]
) -> None:
    pip.release.clear()
    _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()

    first = window.app.items_by_path[str(tmp_path / "start" / "first" / "first.py")]
    assert first.text(1) == ""
    window.app.tree.setCurrentItem(first)
    window.app.launch_button.click()
    (kwargs,) = launched
    assert kwargs["env"] is None  # nothing installed yet, so nothing to add
    pip.release.set()
    _wait_for_install(window.app)


def test_rescanning_during_an_install_does_not_start_another(
    window: _Window, tmp_path: Path, pip: _Pip
) -> None:
    pip.release.clear()
    _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()
    window.app.commit_folder()
    pip.release.set()
    _wait_for_install(window.app)
    assert len(pip.calls) == 1
    assert window.errors == []


def test_a_satisfied_app_is_not_reinstalled_on_the_next_scan(
    window: _Window, tmp_path: Path, pip: _Pip
) -> None:
    _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()
    _wait_for_install(window.app)
    window.app.commit_folder()
    assert not window.app.deps_installing
    assert len(pip.calls) == 1


def test_a_dependency_the_lock_does_not_pin_is_reported_and_never_installed(
    window: _Window, tmp_path: Path, pip: _Pip
) -> None:
    script = _needy(tmp_path / "start" / "needy", "requests>=2")
    window.app.commit_folder()
    (warning,) = window.warnings
    assert "needy needs requests>=2" in warning
    assert "tools/lock.py" in warning
    assert not window.app.deps_installing
    assert pip.calls == []
    assert window.app.items_by_path[script].text(1) == ""


def test_an_unreadable_pyproject_is_reported(window: _Window, tmp_path: Path, pip: _Pip) -> None:
    _app(tmp_path / "start" / "broken", "broken.py")
    (tmp_path / "start" / "broken" / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    window.app.commit_folder()
    (warning,) = window.warnings
    assert "broken: its dependencies could not be checked" in warning
    assert pip.calls == []


def test_a_failed_install_is_shown_and_the_app_is_no_longer_held(
    window: _Window, tmp_path: Path, pip: _Pip
) -> None:
    pip.fail = True
    script = _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()
    _wait_for_install(window.app)
    (error,) = window.errors
    assert "ERROR: no network" in error
    assert window.app.items_by_path[script].text(1) == ""
    assert not window.app.deps_active


def test_a_dependency_still_missing_after_install_is_reported(
    window: _Window, tmp_path: Path, pip: _Pip, deps: PluginDeps
) -> None:
    pip.provides = False
    _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()
    _wait_for_install(window.app)
    (error,) = window.errors
    assert "needy: fakepkg==1.0" in error
    assert str(deps.target) in error


# ── closing during an install (#672) ─────────────────────────────────────────


def _close_and_wait(app: Any) -> None:
    deadline = time.monotonic() + 10
    while app.isVisible():
        assert time.monotonic() < deadline, "the launcher never closed"
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)


def test_closing_during_an_install_waits_for_pip_instead_of_orphaning_it(
    window: _Window, tmp_path: Path, pip: _Pip
) -> None:
    pip.release.clear()
    _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()
    window.app.show()

    assert window.app.close() is False  # pip is still running: the window stays up
    assert window.app.isVisible()
    assert "before closing" in window.app.status_label.text()

    pip.release.set()
    _close_and_wait(window.app)  # closes once the install has finished
    assert not window.app.deps_installing
    assert window.errors == []


def test_closing_during_a_failing_install_still_closes_without_a_dialog(
    window: _Window, tmp_path: Path, pip: _Pip
) -> None:
    pip.release.clear()
    pip.fail = True
    _needy(tmp_path / "start" / "needy", "fakepkg>=1")
    window.app.commit_folder()
    window.app.show()

    assert window.app.close() is False
    pip.release.set()
    _close_and_wait(window.app)
    assert window.errors == []  # the user is leaving; nobody is there to read it


def test_closing_with_no_install_running_closes_at_once(window: _Window) -> None:
    window.app.show()
    assert window.app.close() is True


# --- Swallowed errors are logged, not discarded (#690) ----------------------


@pytest.fixture
def settings_file(
    launcher: ModuleType,
    qapp: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """A settings path the launcher is pointed at; the test decides what is in it."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(launcher, "SETTINGS_FILE", path)
    monkeypatch.setattr(launcher, "show_error", lambda _parent, _title, _msg: None)
    return path


@pytest.mark.parametrize("content", ["{ not json", "[1, 2]"], ids=["unparseable", "not-an-object"])
def test_a_corrupt_settings_file_is_reported_and_its_contents_kept(
    launcher: ModuleType,
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    content: str,
) -> None:
    settings = settings_file
    settings.write_text(content, encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(launcher, "show_warning", lambda _parent, _title, msg: warnings.append(msg))

    with caplog.at_level("WARNING", logger="launcher"):
        app = launcher.PythonLauncherGUI(
            deps=PluginDeps(target=settings.parent / "deps", install=_Pip())
        )
    try:
        backup = settings.with_name("settings.json.bad")
        assert backup.read_text(encoding="utf-8") == content
        assert not settings.exists()  # nothing overwrote it; only a later save writes a new one
        (warning,) = warnings
        assert str(settings) in warning
        assert str(backup) in warning
        assert any(str(settings) in r.getMessage() for r in caplog.records)
        assert app.settings == launcher.DEFAULT_SETTINGS
    finally:
        app.poll_timer.stop()
        app.deleteLater()


def test_a_failed_dark_titlebar_call_is_logged_at_debug(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Window:
        def winId(self) -> int:  # noqa: N802 - mirrors the Qt method name
            return 1

    class _NoDwm:
        @property
        def dwmapi(self) -> object:
            raise OSError("no dwmapi")

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher.ctypes, "windll", _NoDwm(), raising=False)
    with caplog.at_level("DEBUG", logger="launcher"):
        launcher.apply_dark_titlebar(cast(QtWidgets.QWidget, _Window()))
    assert any("title bar" in r.getMessage() and r.levelname == "DEBUG" for r in caplog.records)
