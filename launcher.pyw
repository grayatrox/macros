#!/usr/bin/env pythonw
"""
Python File Launcher GUI
A dark-themed tkinter application to scan folders for Python files and launch them.

Saved as .pyw so double-clicking runs it under pythonw.exe with no console
window. Run `python launcher.pyw` from a terminal if you want tracebacks on
stderr; otherwise startup and callback errors are reported in a dialog.

Single-instance mode (on by default) keeps one running process per script. The
launcher holds the Popen handle for everything it starts, so a PID is only ever
considered "alive" if that specific child is still running -- no PID reuse races.
"""

import json
import subprocess
import sys
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
from tkinter import filedialog, ttk

# Settings file location
SETTINGS_FILE = Path.home() / ".python_launcher_settings.json"

# Default settings
DEFAULT_SETTINGS = {
    "last_folder": str(Path.cwd()),
    "last_file": "",
    "launch_mode": "console",
    "single_instance": True,
}

# Directories that never contain anything worth launching
SKIP_DIRS = {"__pycache__", ".venv", "venv", "env", ".git", ".idea", "site-packages"}

# What counts as a launchable script
SCRIPT_GLOBS = ("*.py", "*.pyw")

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


def resolve_console_python():
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


def make_window_icon(master=None):
    """Draw the launcher's icon: an accent play-triangle on a dark rounded tile.

    Pixel art rather than PIL - this file deliberately has no third-party
    dependencies. Pass the window it belongs to so the PhotoImage binds to that
    interpreter rather than whichever root is default. '.' is left transparent
    to round the corners.
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
    colors = {"D": SURFACE, "A": ACCENT}
    icon = tk.PhotoImage(master=master, width=16, height=16)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char in colors:
                icon.put(colors[char], (x, y))
    return icon.zoom(4)


def pick_font_family(root):
    """Return the nicest UI font family available on this platform."""
    families = set(tkfont.families(root))
    for family in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans", "Arial"):
        if family in families:
            return family
    return tkfont.nametofont("TkDefaultFont").cget("family")


def apply_dark_titlebar(window):
    """Ask DWM for a dark title bar (Windows 10 1809+); no-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
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


class PythonLauncherGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Python File Launcher")
        self.root.geometry("760x620")
        self.root.minsize(560, 460)
        self.root.configure(bg=BG)
        self.icon = make_window_icon(self.root)  # keep a reference so tk doesn't garbage-collect it
        self.root.iconphoto(True, self.icon)

        self.settings = self.load_settings()

        # Variables
        self.selected_folder = tk.StringVar(value=self.settings["last_folder"])
        self.launch_mode = tk.StringVar(value=self.settings["launch_mode"])
        self.single_instance = tk.BooleanVar(value=self.settings["single_instance"])
        self.selected_file = None

        # path -> list of Popen handles this launcher started that are still
        # alive. Reaped by poll_processes(). With single-instance off a script
        # can legitimately have several, and all of them stay stoppable.
        self.processes = {}
        # path -> tree item id, so status cells can be updated in place
        self.items_by_path = {}

        self.font_family = pick_font_family(root)
        self.setup_theme()
        self.setup_ui()
        apply_dark_titlebar(self.root)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self.poll_processes)

        if Path(self.selected_folder.get()).is_dir():
            self.scan_folder()

    # --- Settings ----------------------------------------------------------

    def load_settings(self):
        """Load settings, filling in any keys missing from an older file."""
        settings = DEFAULT_SETTINGS.copy()
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except Exception as e:
                print(f"Error loading settings: {e}")
        return settings

    def save_settings(self):
        """Save current settings to JSON file."""
        try:
            self.settings["last_folder"] = self.selected_folder.get()
            self.settings["last_file"] = self.selected_file or ""
            self.settings["launch_mode"] = self.launch_mode.get()
            self.settings["single_instance"] = bool(self.single_instance.get())

            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")

    # --- Theme -------------------------------------------------------------

    def setup_theme(self):
        """Configure a dark ttk theme on top of 'clam'."""
        style = ttk.Style(self.root)
        style.theme_use("clam")

        base = (self.font_family, 10)
        bold = (self.font_family, 10, "bold")
        small = (self.font_family, 9)

        style.configure(
            ".",
            background=BG,
            foreground=TEXT,
            font=base,
            bordercolor=BORDER,
            darkcolor=SURFACE,
            lightcolor=SURFACE,
            troughcolor=SURFACE,
            focuscolor=BG,
        )
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Heading.TLabel", foreground=MUTED, font=small)
        style.configure(
            "Status.TLabel", background=SURFACE, foreground=MUTED, font=small, padding=(10, 6)
        )

        # Buttons -- flat, no 3D bevel
        for name, fill, hover in (
            ("TButton", ELEVATED, BORDER),
            ("Accent.TButton", GREEN, GREEN_HOVER),
            ("Launch.TButton", ACCENT, ACCENT_HOVER),
            ("Danger.TButton", RED, RED_HOVER),
        ):
            style.configure(
                name,
                background=fill,
                foreground="#ffffff" if name != "TButton" else TEXT,
                bordercolor=fill,
                darkcolor=fill,
                lightcolor=fill,
                focuscolor=fill,
                relief="flat",
                padding=(14, 7),
                font=base,
            )
            style.map(
                name,
                background=[("disabled", SURFACE), ("pressed", fill), ("active", hover)],
                bordercolor=[("disabled", SURFACE), ("active", hover)],
                lightcolor=[("disabled", SURFACE), ("active", hover)],
                darkcolor=[("disabled", SURFACE), ("active", hover)],
                foreground=[("disabled", MUTED)],
            )
        style.configure("Launch.TButton", font=bold, padding=(14, 10))

        # Read-only path entry
        style.configure(
            "Path.TEntry",
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            insertcolor=TEXT,
            padding=6,
        )
        style.map(
            "Path.TEntry",
            fieldbackground=[("readonly", SURFACE)],
            foreground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
        )

        # Check / radio indicators
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                name,
                background=BG,
                foreground=TEXT,
                font=base,
                indicatorbackground=SURFACE,
                indicatorforeground=BG,
                bordercolor=BORDER,
                focuscolor=BG,
                padding=(0, 3),
            )
            style.map(
                name,
                background=[("active", BG)],
                foreground=[("disabled", MUTED)],
                indicatorbackground=[
                    ("selected", ACCENT),
                    ("active", ELEVATED),
                    ("!selected", SURFACE),
                ],
                indicatorforeground=[("selected", "#ffffff")],
            )

        # Treeview -- drop the sunken border, dark rows, readable selection
        style.layout("Dark.Treeview", [("Dark.Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Dark.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            borderwidth=0,
            relief="flat",
            rowheight=24,
            font=base,
        )
        style.map(
            "Dark.Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")]
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=ELEVATED,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
            padding=(8, 6),
            font=small,
        )
        style.map("Dark.Treeview.Heading", background=[("active", BORDER)])

        # Scrollbar
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=ELEVATED,
            troughcolor=SURFACE,
            bordercolor=SURFACE,
            arrowcolor=MUTED,
            darkcolor=ELEVATED,
            lightcolor=ELEVATED,
            relief="flat",
            arrowsize=12,
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", BORDER)])

        # Dark-ish standard dialogs where Tk lets us
        self.root.option_add("*Dialog.msg.font", base)

    # --- UI ----------------------------------------------------------------

    def setup_ui(self):
        """Setup the user interface."""
        # Folder selection
        folder_frame = ttk.Frame(self.root, padding=(14, 14, 14, 6))
        folder_frame.pack(fill="x")

        ttk.Label(folder_frame, text="FOLDER", style="Heading.TLabel").pack(anchor="w")

        folder_row = ttk.Frame(folder_frame)
        folder_row.pack(fill="x", pady=(4, 0))
        path_entry = ttk.Entry(
            folder_row, textvariable=self.selected_folder, style="Path.TEntry", state="readonly"
        )
        path_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(folder_row, text="Browse", command=self.pick_folder).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(folder_row, text="Scan", style="Accent.TButton", command=self.scan_folder).pack(
            side="left", padx=(8, 0)
        )

        # File tree
        tree_frame = ttk.Frame(self.root, padding=(14, 8))
        tree_frame.pack(fill="both", expand=True)

        ttk.Label(tree_frame, text="PYTHON FILES", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 4)
        )

        tree_body = tk.Frame(tree_frame, bg=BORDER, bd=0, highlightthickness=0)
        tree_body.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(tree_body, style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y", padx=(0, 1), pady=1)

        self.tree = ttk.Treeview(
            tree_body,
            style="Dark.Treeview",
            columns=("path", "status"),
            displaycolumns=("status",),
            show="tree headings",
            yscrollcommand=scrollbar.set,
            height=12,
            selectmode="browse",
        )
        self.tree.heading("#0", text="  File", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", width=430, minwidth=200, stretch=True)
        self.tree.column("status", width=130, minwidth=90, anchor="w", stretch=False)
        self.tree.tag_configure("dir", foreground=MUTED)
        self.tree.tag_configure("running", foreground=GREEN_HOVER)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        scrollbar.config(command=self.tree.yview)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        # Launch options
        options_frame = ttk.Frame(self.root, padding=(14, 8))
        options_frame.pack(fill="x")

        ttk.Label(options_frame, text="LAUNCH MODE", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 2)
        )
        ttk.Radiobutton(
            options_frame, text="Console", variable=self.launch_mode, value="console"
        ).pack(anchor="w")
        ttk.Radiobutton(
            options_frame, text="GUI (no console window)", variable=self.launch_mode, value="gui"
        ).pack(anchor="w")
        ttk.Checkbutton(
            options_frame,
            text="Single instance (don't launch a script twice)",
            variable=self.single_instance,
        ).pack(anchor="w", pady=(8, 0))

        # Actions
        launch_frame = ttk.Frame(self.root, padding=(14, 8))
        launch_frame.pack(fill="x")
        self.launch_button = ttk.Button(
            launch_frame,
            text="Launch Selected File",
            style="Launch.TButton",
            command=self.launch_file,
        )
        self.launch_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(
            launch_frame,
            text="Stop",
            style="Danger.TButton",
            command=self.stop_selected,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        # Status bar
        self.status_label = ttk.Label(self.root, text="Ready", style="Status.TLabel", anchor="w")
        self.status_label.pack(fill="x", padx=14, pady=(4, 14))

    def set_status(self, text):
        self.status_label.config(text=text)

    # --- Folder scanning ---------------------------------------------------

    def pick_folder(self):
        """Open folder selection dialog."""
        folder = filedialog.askdirectory(
            initialdir=self.selected_folder.get(),
            title="Select Python Project Folder",
        )
        if folder:
            self.selected_folder.set(folder)
            self.scan_folder()

    def find_scripts(self, folder):
        """Yield launchable scripts under folder, skipping noise and this file."""
        seen = set()
        for pattern in SCRIPT_GLOBS:
            for script in folder.rglob(pattern):
                if SKIP_DIRS.intersection(script.parts):
                    continue
                resolved = script.resolve()
                if resolved == SELF_PATH or resolved in seen:
                    continue
                seen.add(resolved)
                yield script

    def scan_folder(self):
        """Scan the selected folder for Python files."""
        folder = Path(self.selected_folder.get())

        if not folder.is_dir():
            messagebox.showerror("Error", "Selected folder does not exist.")
            return

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.items_by_path.clear()

        # Organise files by directory
        file_dict = {}
        count = 0
        for script in sorted(self.find_scripts(folder)):
            rel_path = script.relative_to(folder)
            parent_dir = str(rel_path.parent) if rel_path.parent != Path(".") else "Root"
            file_dict.setdefault(parent_dir, []).append((script.name, str(script)))
            count += 1

        if not count:
            self.selected_file = None
            self.update_buttons()
            self.set_status("No Python files found in this folder.")
            return

        for dir_name in sorted(file_dict):
            dir_item = self.tree.insert(
                "", "end", text=f"  {dir_name}", open=True, values=("", ""), tags=("dir",)
            )
            for file_name, file_path in sorted(file_dict[dir_name]):
                item = self.tree.insert(
                    dir_item, "end", text=f"  {file_name}", values=(file_path, "")
                )
                self.items_by_path[file_path] = item

        self.refresh_status_cells()
        self.set_status(f"Found {count} Python file(s). Select one to launch.")

        # Re-select whatever was last launched, if it is still around
        last_file = self.settings.get("last_file", "")
        if last_file in self.items_by_path:
            item = self.items_by_path[last_file]
            self.tree.selection_set(item)
            self.tree.see(item)

    # --- Selection ---------------------------------------------------------

    def current_selection_path(self):
        selection = self.tree.selection()
        if not selection:
            return None
        values = self.tree.item(selection[0], "values")
        return values[0] if values and values[0] else None

    def on_tree_select(self, event=None):
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

    def on_tree_double_click(self, event=None):
        if self.current_selection_path():
            self.launch_file()

    def update_buttons(self):
        running = bool(self.processes.get(self.selected_file))
        self.stop_button.config(state="normal" if running else "disabled")
        self.launch_button.config(
            text="Restart Selected File"
            if running and self.single_instance.get()
            else "Launch Selected File"
        )

    # --- Process tracking --------------------------------------------------

    def poll_processes(self):
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
        self.root.after(POLL_MS, self.poll_processes)

    def refresh_status_cells(self):
        """Write the live PID(s) next to every file we currently have running."""
        for path, item in self.items_by_path.items():
            if not self.tree.exists(item):
                continue
            procs = self.processes.get(path)
            if not procs:
                text = ""
            elif len(procs) == 1:
                text = f"PID {procs[0].pid}"
            else:
                text = f"{len(procs)} running"
            self.tree.set(item, "status", text)
            self.tree.item(item, tags=("running",) if procs else ())

    def terminate(self, path, timeout=5):
        """Stop every tracked process for a path, killing any that won't exit."""
        survivors = []
        for proc in self.processes.get(path, []):
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=timeout)
                except Exception as e:
                    messagebox.showerror("Error", f"Could not stop PID {proc.pid}: {e}")
                    survivors.append(proc)
            except Exception as e:
                messagebox.showerror("Error", f"Could not stop PID {proc.pid}: {e}")
                survivors.append(proc)

        if survivors:
            self.processes[path] = survivors
        else:
            self.processes.pop(path, None)
        self.refresh_status_cells()
        self.update_buttons()
        return not survivors

    def stop_selected(self):
        """Stop the running process(es) for the selected file."""
        path = self.selected_file
        procs = self.processes.get(path)
        if not procs:
            return
        pids = ", ".join(str(p.pid) for p in procs)
        if self.terminate(path):
            self.set_status(f"Stopped {Path(path).name} (PID {pids}).")

    # --- Launching ---------------------------------------------------------

    def launch_file(self):
        """Launch the selected Python file."""
        path = self.selected_file
        if not path:
            messagebox.showwarning("Warning", "Please select a Python file first.")
            return

        if not Path(path).exists():
            messagebox.showerror("Error", "Selected file no longer exists.")
            self.scan_folder()
            return

        name = Path(path).name

        # Single-instance check: we only ever compare against PIDs we started
        # ourselves, so a recycled PID can never be mistaken for a live script.
        if self.single_instance.get():
            procs = self.processes.get(path)
            if procs:
                pids = ", ".join(str(p.pid) for p in procs)
                restart = messagebox.askyesno(
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
            elif self.launch_mode.get() == "gui":
                proc = subprocess.Popen(
                    [PYTHON_EXE, path], creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                proc = subprocess.Popen(
                    [PYTHON_EXE, path], creationflags=subprocess.CREATE_NEW_CONSOLE
                )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch file: {e}")
            return

        self.processes.setdefault(path, []).append(proc)
        self.refresh_status_cells()
        self.update_buttons()
        self.set_status(f"Launched {name} (PID {proc.pid}).")

    # --- Shutdown ----------------------------------------------------------

    def on_close(self):
        """Persist settings and leave launched scripts running."""
        self.save_settings()
        self.root.destroy()


# --- Error reporting --------------------------------------------------------


def format_error(exc):
    import traceback

    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def report_error(title, details):
    """Show a dialog -- under pythonw there is no console to print a traceback to."""
    try:
        messagebox.showerror(title, details)
    except Exception:
        # Tk itself is broken; leave something behind that can be read later.
        try:
            SELF_PATH.with_name("launcher-error.log").write_text(details, encoding="utf-8")
        except Exception:
            pass


def main():
    root = tk.Tk()
    # Without this, exceptions raised inside Tk callbacks go to stderr, which is
    # None under pythonw -- i.e. buttons would silently do nothing.
    root.report_callback_exception = lambda exc, value, tb: report_error(
        "Python File Launcher error", format_error(value)
    )
    PythonLauncherGUI(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        report_error("Python File Launcher failed to start", format_error(exc))
        sys.exit(1)
