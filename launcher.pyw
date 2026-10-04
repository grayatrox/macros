#!/usr/bin/env pythonw
"""
Python File Launcher GUI
A dark-themed PySide6 (Qt) application to scan folders for Python files and launch them.

Saved as .pyw so double-clicking runs it under pythonw.exe with no console
window. Run `python launcher.pyw` from a terminal if you want tracebacks on
stderr; otherwise startup and callback errors are reported in a dialog.

PySide6 is a third-party package. Run as a script, the launcher first makes it
importable through the same first-start install as Macros.py (hash-pinned, into
a private folder; see src/macros/bootstrap.py), so a bare Python still works.

Single-instance mode (on by default) keeps one running process per script. The
launcher holds the Popen handle for everything it starts, so a PID is only ever
considered "alive" if that specific child is still running -- no PID reuse races.
"""

import contextlib
import ctypes
import json
import subprocess
import sys
import tomllib
import traceback
from pathlib import Path, PurePath
from types import TracebackType
from typing import Any, NamedTuple, override

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from macros.bootstrap import BootstrapError, ensure_dependencies  # needs src on sys.path

MB_ICONERROR = 0x10


def show_native_error(title: str, message: str) -> None:
    """A Windows message box: the only dialog available before PySide6 is."""
    ctypes.windll.user32.MessageBoxW(0, message, title, MB_ICONERROR)


def bootstrap() -> None:
    """Make PySide6 importable, installing it on first start.

    Raises:
        SystemExit: the install failed; the reason has been shown in a dialog.
    """
    try:
        ensure_dependencies()
    except BootstrapError as exc:
        show_native_error("Python File Launcher - cannot start", str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    bootstrap()

from PySide6 import (  # noqa: E402 - importable only once bootstrap() has run
    QtCore,
    QtGui,
    QtWidgets,
)

# Settings file location
SETTINGS_FILE = Path.home() / ".python_launcher_settings.json"

# Default settings
DEFAULT_SETTINGS: dict[str, Any] = {
    "last_folder": str(Path.cwd()),
    "last_file": "",
    "launch_mode": "console",
    "single_instance": True,
}

# Directories that never contain anything worth launching
SKIP_DIRS = {"__pycache__", ".venv", "venv", "env", ".git", ".idea", "site-packages"}

# An app is a script declared by this file in its folder; nothing else is listed.
# See docs/adr/0001-launcher-app-manifests.md.
MANIFEST_NAME = "launcher-app.toml"
SCRIPT_SUFFIXES = {".py", ".pyw"}

# This file, so the launcher never lists itself as something to launch
SELF_PATH = Path(__file__).resolve()

# --- Dark palette -----------------------------------------------------------
BG = "#1b1d21"  # window background
SURFACE = "#24272c"  # panels, entries, tree
ELEVATED = "#2f333a"  # hover / headings
BORDER = "#3a3f47"
TEXT = "#e4e6eb"
MUTED = "#9099a5"
ACCENT = "#4c8dff"  # primary action (blue)
ACCENT_HOVER = "#5f9bff"
GREEN = "#3fa86b"
GREEN_HOVER = "#4bbd7b"
RED = "#c8503f"
RED_HOVER = "#d9614f"
POLL_MS = 1000  # how often running processes are re-checked


def resolve_console_python() -> str:
    """Return an interpreter that can own a console window.

    Because this file is a .pyw, sys.executable is normally pythonw.exe -- a
    GUI-subsystem binary that never gets a console, even with CREATE_NEW_CONSOLE,
    and whose sys.stdout is None. Launching child scripts with it would send
    their output nowhere, so swap to the python.exe sitting beside it.
    """
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.stem.lower().endswith("w"):
        console_exe = exe.with_name(exe.stem[:-1] + exe.suffix)
        if console_exe.exists():
            return str(console_exe)
    return str(exe)


# Interpreter used for every launch. GUI mode hides the console with
# CREATE_NO_WINDOW rather than switching to pythonw, so the child still has a
# real (if invisible) stdout and a stray print() cannot blow up.
PYTHON_EXE = resolve_console_python()


class ManifestError(Exception):
    """A launcher-app.toml that does not declare a usable script."""


class AppScan(NamedTuple):
    """What find_apps found: the declared scripts, and one message per bad manifest."""

    scripts: list[Path]
    problems: list[str]


def read_manifest(manifest: Path) -> Path:
    """Return the script a launcher-app.toml declares, as a path under its folder.

    The script must be a relative path that stays inside the manifest's folder,
    so a manifest can only list its own app, never something elsewhere on disk.

    Raises:
        ManifestError: the manifest is unreadable, invalid TOML, or its
            ``script`` is missing, not a .py/.pyw, outside its folder, or absent.
    """
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except OSError as e:
        raise ManifestError(f"could not be read: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"is not valid TOML: {e}") from e

    script = data.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ManifestError("has no 'script' naming the file to run.")
    # anchor, not is_absolute(): on Windows "/x.py" has a root but no drive and
    # is not "absolute", yet it still points outside the folder.
    if PurePath(script).anchor:
        raise ManifestError(f"script {script!r} must be a relative path.")
    if PurePath(script).suffix.lower() not in SCRIPT_SUFFIXES:
        raise ManifestError(f"script {script!r} must be a .py or .pyw file.")

    # Resolved so neither '..' nor a symlink can lead out of the folder.
    home = manifest.parent.resolve()
    target = (home / script).resolve()
    if not target.is_relative_to(home):
        raise ManifestError(f"script {script!r} must be inside {home}.")
    if not target.is_file():
        raise ManifestError(f"script {script!r} does not exist.")
    return manifest.parent / target.relative_to(home)


def find_apps(folder: Path) -> AppScan:
    """Find every app declared by a launcher-app.toml anywhere under folder.

    Searches all subfolders except SKIP_DIRS, and never lists the launcher
    itself. A bad manifest is reported in ``problems`` rather than raised, so
    one broken app does not hide the others.
    """
    scripts: list[Path] = []
    problems: list[str] = []
    seen: set[Path] = set()
    for manifest in sorted(folder.rglob(MANIFEST_NAME)):
        if SKIP_DIRS.intersection(manifest.parts) or not manifest.is_file():
            continue
        try:
            script = read_manifest(manifest)
        except ManifestError as e:
            problems.append(f"{manifest} {e}")
            continue
        resolved = script.resolve()
        if resolved == SELF_PATH or resolved in seen:
            continue
        seen.add(resolved)
        scripts.append(script)
    return AppScan(scripts, problems)


# Where a file row keeps its full path; folder rows have none
PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole
FILE_COLUMN, STATUS_COLUMN = 0, 1


def make_window_icon() -> QtGui.QIcon:
    """Draw the launcher's icon: an accent play-triangle on a dark rounded tile.

    Pixel art drawn in code, so the launcher ships no image file. '.' is left
    transparent to round the corners. Needs a QApplication.
    """
    rows = [
        ".DDDDDDDDDDDDDD.",
        "DDDDDDDDDDDDDDDD",
        "DDDDDDDDDDDDDDDD",
        "DDDDDADDDDDDDDDD",
        "DDDDDAADDDDDDDDD",
        "DDDDDAAAADDDDDDD",
        "DDDDDAAAAADDDDDD",
        "DDDDDAAAAAADDDDD",
        "DDDDDAAAAAADDDDD",
        "DDDDDAAAAADDDDDD",
        "DDDDDAAAADDDDDDD",
        "DDDDDAADDDDDDDDD",
        "DDDDDADDDDDDDDDD",
        "DDDDDDDDDDDDDDDD",
        "DDDDDDDDDDDDDDDD",
        ".DDDDDDDDDDDDDD.",
    ]
    colors = {"D": QtGui.QColor(SURFACE), "A": QtGui.QColor(ACCENT)}
    image = QtGui.QImage(16, 16, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char in colors:
                image.setPixelColor(x, y, colors[char])
    # The default (nearest-neighbour) scaling keeps the pixels crisp
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image.scaled(64, 64)))


def pick_font_family() -> str:
    """Return the nicest UI font family available on this platform."""
    families = set(QtGui.QFontDatabase.families())
    for family in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans", "Arial"):
        if family in families:
            return family
    return QtWidgets.QApplication.font().family()


def stylesheet() -> str:
    """The launcher's Qt style sheet, built from the dark palette."""
    rules = [
        f"QWidget {{ background: {BG}; color: {TEXT}; }}",
        f'QLabel[role="heading"] {{ color: {MUTED}; font-size: 9pt; }}',
        f"QLabel#status {{ background: {SURFACE}; color: {MUTED}; font-size: 9pt;"
        " padding: 6px 10px; }",
        # Buttons -- flat, no 3D bevel
        f"QPushButton {{ background: {ELEVATED}; color: {TEXT}; border: none;"
        " padding: 7px 14px; }",
        f"QPushButton:hover {{ background: {BORDER}; }}",
        f"QPushButton:pressed {{ background: {ELEVATED}; }}",
    ]
    for role, fill, hover in (
        ("accent", GREEN, GREEN_HOVER),
        ("launch", ACCENT, ACCENT_HOVER),
        ("danger", RED, RED_HOVER),
    ):
        selector = f'QPushButton[role="{role}"]'
        rules += [
            f"{selector} {{ background: {fill}; color: #ffffff; }}",
            f"{selector}:hover {{ background: {hover}; }}",
            f"{selector}:pressed {{ background: {fill}; }}",
            f"{selector}:disabled {{ background: {SURFACE}; color: {MUTED}; }}",
        ]
    rules += [
        f"QPushButton:disabled {{ background: {SURFACE}; color: {MUTED}; }}",
        'QPushButton[role="launch"] { font-weight: bold; padding: 10px 14px; }',
        # Editable path entry
        f"QLineEdit {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 6px;"
        f" selection-background-color: {ACCENT}; selection-color: #ffffff; }}",
        f"QLineEdit:focus {{ border-color: {ACCENT}; }}",
        # File tree -- flat dark rows, readable selection
        f"QTreeWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; }}",
        "QTreeWidget::item { height: 24px; }",
        f"QTreeWidget::item:selected {{ background: {ACCENT}; color: #ffffff; }}",
        f"QHeaderView::section {{ background: {ELEVATED}; color: {MUTED}; border: none;"
        " padding: 6px 8px; font-size: 9pt; }",
        # Scrollbar
        f"QScrollBar:vertical {{ background: {SURFACE}; width: 12px; margin: 0; }}",
        f"QScrollBar::handle:vertical {{ background: {ELEVATED}; min-height: 24px; }}",
        f"QScrollBar::handle:vertical:hover {{ background: {BORDER}; }}",
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }",
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }",
        "QRadioButton, QCheckBox { padding: 3px 0; }",
    ]
    return "\n".join(rules)


def apply_dark_theme(app: QtWidgets.QApplication) -> None:
    """Fusion with the dark palette, so dialogs match the window too."""
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    role = QtGui.QPalette.ColorRole
    for color_role, color in (
        (role.Window, BG),
        (role.WindowText, TEXT),
        (role.Base, SURFACE),
        (role.AlternateBase, ELEVATED),
        (role.Text, TEXT),
        (role.Button, ELEVATED),
        (role.ButtonText, TEXT),
        (role.Highlight, ACCENT),
        (role.HighlightedText, "#ffffff"),
        (role.ToolTipBase, SURFACE),
        (role.ToolTipText, TEXT),
        (role.PlaceholderText, MUTED),
    ):
        palette.setColor(color_role, QtGui.QColor(color))
    for color_role in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, color_role, QtGui.QColor(MUTED))
    app.setPalette(palette)
    app.setFont(QtGui.QFont(pick_font_family(), 10))
    app.setStyleSheet(stylesheet())


def apply_dark_titlebar(window: QtWidgets.QWidget) -> None:
    """Ask DWM for a dark title bar (Windows 10 1809+); no-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        enabled = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE, 19 on pre-20H1 builds
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
            if result == 0:
                break
    except Exception:
        pass


# --- Dialogs ------------------------------------------------------------------
# Every dialog goes through one of these, so tests can stand in for the user.


def show_error(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    QtWidgets.QMessageBox.critical(parent, title, message)


def show_warning(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    QtWidgets.QMessageBox.warning(parent, title, message)


def ask_yes_no(parent: QtWidgets.QWidget | None, title: str, message: str) -> bool:
    answer = QtWidgets.QMessageBox.question(parent, title, message)
    return answer == QtWidgets.QMessageBox.StandardButton.Yes


def ask_directory(parent: QtWidgets.QWidget | None, title: str, start: str) -> str:
    """Pick a folder; returns "" if cancelled. Uses Windows-style separators."""
    folder = QtWidgets.QFileDialog.getExistingDirectory(parent, title, start)
    return QtCore.QDir.toNativeSeparators(folder) if folder else ""


class PythonLauncherGUI(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Python File Launcher")
        self.resize(760, 620)
        self.setMinimumSize(560, 460)
        self.setWindowIcon(make_window_icon())

        self.settings = self.load_settings()

        self.selected_file: str | None = None
        # What the folder box goes back to when a typed path is rejected
        self.committed_folder = str(self.settings["last_folder"])

        # path -> list of Popen handles this launcher started that are still
        # alive. Reaped by poll_processes(). With single-instance off a script
        # can legitimately have several, and all of them stay stoppable.
        self.processes: dict[str, list[subprocess.Popen[bytes]]] = {}
        # path -> tree row, so status cells can be updated in place
        self.items_by_path: dict[str, QtWidgets.QTreeWidgetItem] = {}

        self.setup_ui()

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(POLL_MS)
        self.poll_timer.timeout.connect(self.poll_processes)
        self.poll_timer.start()

        if Path(self.committed_folder).is_dir():
            self.scan_folder()

    # --- Settings ----------------------------------------------------------

    def load_settings(self) -> dict[str, Any]:
        """Load settings, filling in any keys missing from an older file."""
        settings = DEFAULT_SETTINGS.copy()
        if SETTINGS_FILE.exists():
            try:
                with SETTINGS_FILE.open(encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except Exception as e:
                print(f"Error loading settings: {e}")
        return settings

    def save_settings(self) -> None:
        """Save current settings to JSON file."""
        try:
            self.settings["last_folder"] = self.path_entry.text()
            self.settings["last_file"] = self.selected_file or ""
            self.settings["launch_mode"] = self.launch_mode()
            self.settings["single_instance"] = self.single_instance.isChecked()

            with SETTINGS_FILE.open("w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            show_error(self, "Error", f"Failed to save settings: {e}")

    def launch_mode(self) -> str:
        return "gui" if self.gui_mode.isChecked() else "console"

    # --- UI ----------------------------------------------------------------

    @staticmethod
    def heading(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "heading")
        return label

    @staticmethod
    def button(text: str, role: str | None = None) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        if role:
            button.setProperty("role", role)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        return button

    def setup_ui(self) -> None:
        """Setup the user interface."""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(4)
        for section in (self.folder_section, self.tree_section, self.options_section):
            section(layout)
            layout.addSpacing(14)
        self.actions_section(layout)
        layout.addSpacing(8)

        # Status bar
        self.status_label = QtWidgets.QLabel("Ready")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

    def folder_section(self, layout: QtWidgets.QVBoxLayout) -> None:
        layout.addWidget(self.heading("FOLDER"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.path_entry = QtWidgets.QLineEdit(self.committed_folder)
        self.path_entry.returnPressed.connect(self.commit_folder)
        # Leaving the box commits it too; see eventFilter
        self.path_entry.installEventFilter(self)
        row.addWidget(self.path_entry, 1)
        browse = self.button("Browse")
        browse.clicked.connect(self.pick_folder)
        row.addWidget(browse)
        scan = self.button("Scan", "accent")
        scan.clicked.connect(self.scan_folder)
        row.addWidget(scan)
        layout.addLayout(row)

    def tree_section(self, layout: QtWidgets.QVBoxLayout) -> None:
        layout.addWidget(self.heading("PYTHON FILES"))
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["File", "Status"])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(FILE_COLUMN, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(STATUS_COLUMN, 130)
        self.tree.itemSelectionChanged.connect(self.on_tree_select)
        self.tree.itemDoubleClicked.connect(self.on_tree_double_click)
        layout.addWidget(self.tree, 1)

    def options_section(self, layout: QtWidgets.QVBoxLayout) -> None:
        layout.addWidget(self.heading("LAUNCH MODE"))
        self.console_mode = QtWidgets.QRadioButton("Console")
        self.gui_mode = QtWidgets.QRadioButton("GUI (no console window)")
        gui = self.settings["launch_mode"] == "gui"
        (self.gui_mode if gui else self.console_mode).setChecked(True)
        layout.addWidget(self.console_mode)
        layout.addWidget(self.gui_mode)
        self.single_instance = QtWidgets.QCheckBox("Single instance (don't launch a script twice)")
        self.single_instance.setChecked(bool(self.settings["single_instance"]))
        layout.addSpacing(8)
        layout.addWidget(self.single_instance)

    def actions_section(self, layout: QtWidgets.QVBoxLayout) -> None:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.launch_button = self.button("Launch Selected File", "launch")
        self.launch_button.clicked.connect(self.launch_file)
        row.addWidget(self.launch_button, 1)
        self.stop_button = self.button("Stop", "danger")
        self.stop_button.clicked.connect(self.stop_selected)
        self.stop_button.setEnabled(False)
        row.addWidget(self.stop_button)
        layout.addLayout(row)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # --- Folder scanning ---------------------------------------------------

    def pick_folder(self) -> None:
        """Open folder selection dialog."""
        folder = ask_directory(self, "Select Python Project Folder", self.path_entry.text())
        if folder:
            self.path_entry.setText(folder)
            self.scan_folder()

    def commit_folder(self) -> None:
        """Scan whatever has been typed into the folder box (Enter)."""
        self.scan_folder()

    @override
    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.path_entry and event.type() == QtCore.QEvent.Type.FocusOut:
            self.on_folder_focus_out()
        return super().eventFilter(watched, event)

    def on_folder_focus_out(self) -> None:
        # Only an edit is worth a rescan here. This also stops the error dialog
        # from a rejected path re-triggering itself when it takes the focus.
        if self.path_entry.text() != self.committed_folder:
            self.scan_folder()

    def scan_folder(self) -> None:
        """Scan the folder in the folder box for apps (see find_apps).

        A blank or missing folder is reported and the box reverts to the last
        folder that scanned, leaving the file list as it was. Bad manifests are
        shown in a warning; the apps that are fine are still listed.
        """
        # Explorer's "Copy as path" wraps the path in double quotes
        text = self.path_entry.text().strip().strip('"').strip()
        # Checked before Path(): Path("") is "." and would scan the working directory
        if not text:
            self.reject_folder("Enter a folder to scan.")
            return
        folder = Path(text)
        if not folder.is_dir():
            self.reject_folder(f"Folder not found:\n{text}")
            return

        self.path_entry.setText(text)
        self.committed_folder = text

        self.tree.clear()
        self.items_by_path.clear()

        scan = find_apps(folder)

        # Organise files by directory
        file_dict: dict[str, list[tuple[str, str]]] = {}
        count = 0
        for script in sorted(scan.scripts):
            rel_path = script.relative_to(folder)
            parent_dir = str(rel_path.parent) if rel_path.parent != Path() else "Root"
            file_dict.setdefault(parent_dir, []).append((script.name, str(script)))
            count += 1

        if scan.problems:
            show_warning(self, "Some apps could not be listed", "\n\n".join(scan.problems))

        if not count:
            self.selected_file = None
            self.update_buttons()
            self.set_status(f"No apps found: none of these folders has a {MANIFEST_NAME}.")
            return

        for dir_name in sorted(file_dict):
            dir_item = QtWidgets.QTreeWidgetItem(self.tree, [dir_name, ""])
            dir_item.setForeground(FILE_COLUMN, QtGui.QColor(MUTED))
            for file_name, file_path in sorted(file_dict[dir_name]):
                item = QtWidgets.QTreeWidgetItem(dir_item, [file_name, ""])
                item.setData(FILE_COLUMN, PATH_ROLE, file_path)
                self.items_by_path[file_path] = item
            dir_item.setExpanded(True)

        self.refresh_status_cells()
        self.set_status(f"Found {count} app(s). Select one to launch.")

        # Re-select whatever was last launched, if it is still around
        last_file = self.settings.get("last_file", "")
        if last_file in self.items_by_path:
            item = self.items_by_path[last_file]
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    def reject_folder(self, message: str) -> None:
        # Revert before the dialog: it takes the focus, and the box's FocusOut
        # must then see nothing left to commit.
        self.path_entry.setText(self.committed_folder)
        show_error(self, "Error", message)

    # --- Selection ---------------------------------------------------------

    def current_selection_path(self) -> str | None:
        selection = self.tree.selectedItems()
        if not selection:
            return None
        path = selection[0].data(FILE_COLUMN, PATH_ROLE)
        return str(path) if path else None

    def on_tree_select(self) -> None:
        """Handle tree view selection."""
        path = self.current_selection_path()
        self.selected_file = path
        if path:
            procs = self.processes.get(path)
            name = Path(path).name
            if procs:
                pids = ", ".join(str(p.pid) for p in procs)
                self.set_status(f"Selected: {name}  ·  running as PID {pids}")
            else:
                self.set_status(f"Selected: {name}")
        else:
            self.set_status("Select a Python file (not a folder).")
        self.update_buttons()

    def on_tree_double_click(self, *_args: object) -> None:
        if self.current_selection_path():
            self.launch_file()

    def update_buttons(self) -> None:
        running = bool(self.selected_file and self.processes.get(self.selected_file))
        self.stop_button.setEnabled(running)
        self.launch_button.setText(
            "Restart Selected File"
            if running and self.single_instance.isChecked()
            else "Launch Selected File"
        )

    # --- Process tracking --------------------------------------------------

    def poll_processes(self) -> None:
        """Reap finished children and refresh the Status column."""
        changed = False
        for path, procs in list(self.processes.items()):
            alive = [p for p in procs if p.poll() is None]
            if len(alive) != len(procs):
                changed = True
                if alive:
                    self.processes[path] = alive
                else:
                    del self.processes[path]
        if changed:
            self.refresh_status_cells()
            self.update_buttons()

    def refresh_status_cells(self) -> None:
        """Write the live PID(s) next to every file we currently have running."""
        running = QtGui.QBrush(QtGui.QColor(GREEN_HOVER))
        for path, item in self.items_by_path.items():
            procs = self.processes.get(path)
            if not procs:
                text = ""
            elif len(procs) == 1:
                text = f"PID {procs[0].pid}"
            else:
                text = f"{len(procs)} running"
            item.setText(STATUS_COLUMN, text)
            for column in (FILE_COLUMN, STATUS_COLUMN):
                # An empty brush falls back to the palette's text colour
                item.setForeground(column, running if procs else QtGui.QBrush())

    def terminate(self, path: str, timeout: float = 5) -> bool:
        """Stop every tracked process for a path, killing any that won't exit."""
        survivors: list[subprocess.Popen[bytes]] = []
        for proc in self.processes.get(path, []):
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=timeout)
                except Exception as e:
                    show_error(self, "Error", f"Could not stop PID {proc.pid}: {e}")
                    survivors.append(proc)
            except Exception as e:
                show_error(self, "Error", f"Could not stop PID {proc.pid}: {e}")
                survivors.append(proc)

        if survivors:
            self.processes[path] = survivors
        else:
            self.processes.pop(path, None)
        self.refresh_status_cells()
        self.update_buttons()
        return not survivors

    def stop_selected(self) -> None:
        """Stop the running process(es) for the selected file."""
        path = self.selected_file
        procs = self.processes.get(path) if path else None
        if not path or not procs:
            return
        pids = ", ".join(str(p.pid) for p in procs)
        if self.terminate(path):
            self.set_status(f"Stopped {Path(path).name} (PID {pids}).")

    # --- Launching ---------------------------------------------------------

    def launch_file(self) -> None:
        """Launch the selected Python file."""
        path = self.selected_file
        if not path:
            show_warning(self, "Warning", "Please select a Python file first.")
            return

        if not Path(path).exists():
            show_error(self, "Error", "Selected file no longer exists.")
            self.scan_folder()
            return

        name = Path(path).name

        # Single-instance check: we only ever compare against PIDs we started
        # ourselves, so a recycled PID can never be mistaken for a live script.
        if self.single_instance.isChecked():
            procs = self.processes.get(path)
            if procs:
                pids = ", ".join(str(p.pid) for p in procs)
                restart = ask_yes_no(
                    self,
                    "Already running",
                    f"{name} is already running as PID {pids}.\n\nRestart it?",
                )
                if not restart:
                    self.set_status(f"{name} is already running (PID {pids}).")
                    return
                if not self.terminate(path):
                    return

        self.save_settings()

        try:
            if sys.platform != "win32":
                proc = subprocess.Popen([PYTHON_EXE, path])
            elif self.launch_mode() == "gui":
                proc = subprocess.Popen(
                    [PYTHON_EXE, path], creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                proc = subprocess.Popen(
                    [PYTHON_EXE, path], creationflags=subprocess.CREATE_NEW_CONSOLE
                )
        except Exception as e:
            show_error(self, "Error", f"Failed to launch file: {e}")
            return

        self.processes.setdefault(path, []).append(proc)
        self.refresh_status_cells()
        self.update_buttons()
        self.set_status(f"Launched {name} (PID {proc.pid}).")

    # --- Shutdown ----------------------------------------------------------

    @override
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Persist settings and leave launched scripts running."""
        self.save_settings()
        event.accept()


# --- Error reporting --------------------------------------------------------


def format_error(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def report_error(title: str, details: str) -> None:
    """Show a dialog -- under pythonw there is no console to print a traceback to."""
    try:
        # A message box without a QApplication aborts the process outright
        if QtWidgets.QApplication.instance() is None:
            raise RuntimeError("no QApplication")
        show_error(None, title, details)
    except Exception:
        # Qt itself is unusable; leave something behind that can be read later.
        with contextlib.suppress(OSError):
            SELF_PATH.with_name("launcher-error.log").write_text(details, encoding="utf-8")


def report_uncaught(
    _exc_type: type[BaseException], value: BaseException, _tb: TracebackType | None
) -> None:
    report_error("Python File Launcher error", format_error(value))


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    # Without this, exceptions raised inside Qt slots go to stderr, which is
    # None under pythonw -- i.e. buttons would silently do nothing.
    sys.excepthook = report_uncaught
    apply_dark_theme(app)
    window = PythonLauncherGUI()
    apply_dark_titlebar(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        status = main()
    except Exception as exc:
        report_error("Python File Launcher failed to start", format_error(exc))
        sys.exit(1)
    sys.exit(status)
