#!/usr/bin/env python3
"""
send_to_window.py
─────────────────
A persistent background app that:
  • Sits in the system tray (right-click → Show Log / Hide Log / Quit)
  • Listens globally for hotkeys defined per-profile
  • Detects the active window and dispatches to the matching profile
  • Streams events to a toggleable dark-themed GUI log window

Dependencies (pinned, hashed - see pyproject.toml):
    pip install --require-hashes -r requirements.lock
"""

import io
import os
import sys
import time
import ctypes
import base64
import threading
import tkinter as tk
from ctypes import wintypes
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Third-party imports
# ─────────────────────────────────────────────────────────────────────────────

try:
    from PIL import Image, ImageDraw
    import keyboard
    import pyautogui
    import win32gui
    import win32process
    import psutil
    import pystray
except ImportError as exc:
    # Under pythonw (the launcher's GUI mode) there is no console for the
    # traceback, so say what is missing and how to fix it in a dialog.
    ctypes.windll.user32.MessageBoxW(
        0,
        f"{exc}\n\nInstall the pinned dependencies from the Macros folder:\n\n"
        "  pip install --require-hashes -r requirements.lock",
        "send_to_window — missing dependency",
        0x10,  # MB_ICONERROR
    )
    raise

try:
    from mcvote import open_vote_pages
except ImportError:
    open_vote_pages = None


# ─────────────────────────────────────────────────────────────────────────────
# Logging / Console Window
# ─────────────────────────────────────────────────────────────────────────────

LEVELS = {
    "INFO":    "#8be9fd",   # cyan
    "SUCCESS": "#50fa7b",   # green
    "WARN":    "#ffb86c",   # orange
    "ERROR":   "#ff5555",   # red
    "DEBUG":   "#6272a4",   # muted purple
    "EVENT":   "#bd93f9",   # lavender
}

BG          = "#1e1f29"
BG_HEADER   = "#16171f"
FG          = "#f8f8f2"
FG_DIM      = "#6272a4"
ACCENT      = "#bd93f9"


def make_app_image(size: int = 64) -> "Image.Image":
    """The app's mark: a lavender ring on transparency.

    Single source of truth for both the tray icon and the window icon, so the
    two can never drift apart.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ring = size // 16                    # keep proportions at any size
    draw.ellipse([ring, ring, size - ring, size - ring], fill=ACCENT)
    hole = size // 3.5
    draw.ellipse([hole, hole, size - hole, size - hole], fill=BG)
    return img


def make_window_icon(master=None) -> tk.PhotoImage:
    """The same mark as a PhotoImage for iconphoto().

    Pass the window it belongs to: a PhotoImage is owned by one interpreter, and
    letting it bind to whichever root happens to be default fails outright if a
    second Tk is ever created.
    """
    buffer = io.BytesIO()
    make_app_image(64).save(buffer, format="PNG")
    return tk.PhotoImage(master=master, data=base64.b64encode(buffer.getvalue()))


class LogWindow:
    """Toggleable dark-theme log console (runs on the main tkinter thread)."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("send_to_window — log")
        self.root.geometry("780x480")
        self.root.configure(bg=BG)
        self.icon = make_window_icon(self.root)  # keep a reference so tk doesn't garbage-collect it
        self.root.iconphoto(True, self.icon)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._build_ui()
        self._visible = True
        self._pending: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG_HEADER, pady=6)
        header.pack(fill="x")

        tk.Label(
            header, text="⬡  send_to_window",
            bg=BG_HEADER, fg=ACCENT,
            font=("Consolas", 12, "bold"), padx=12,
        ).pack(side="left")

        self._status_label = tk.Label(
            header, text="● listening",
            bg=BG_HEADER, fg=LEVELS["SUCCESS"],
            font=("Consolas", 10), padx=12,
        )
        self._status_label.pack(side="right")

        # Scrollable text area
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill="both", expand=True)

        self._text = tk.Text(
            frame,
            bg=BG, fg=FG,
            font=("Consolas", 10),
            relief="flat", bd=0,
            wrap="word",
            state="disabled",
            selectbackground=ACCENT,
            insertbackground=FG,
            padx=10, pady=8,
        )
        scroll = tk.Scrollbar(frame, command=self._text.yview, bg=BG, troughcolor=BG_HEADER)
        self._text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        # Configure a tag per log level
        for level, color in LEVELS.items():
            self._text.tag_configure(f"lvl_{level}", foreground=color, font=("Consolas", 10, "bold"))
        self._text.tag_configure("ts",  foreground=FG_DIM, font=("Consolas", 9))
        self._text.tag_configure("msg", foreground=FG,     font=("Consolas", 10))

        # Footer / clear button
        footer = tk.Frame(self.root, bg=BG_HEADER, pady=4)
        footer.pack(fill="x")
        tk.Button(
            footer, text="Clear",
            bg=BG_HEADER, fg=FG_DIM,
            activebackground=BG, activeforeground=FG,
            relief="flat", bd=0, padx=10, pady=2,
            command=self._clear,
            font=("Consolas", 9),
            cursor="hand2",
        ).pack(side="right", padx=8)

    # ── Public API (thread-safe) ─────────────────────────────────────────────

    def log(self, message: str, level: str = "INFO"):
        with self._lock:
            self._pending.append((message, level.upper()))

    def show(self):
        self.root.after(0, self._show)

    def hide(self):
        self.root.after(0, self._hide)

    def toggle(self):
        if self._visible:
            self.hide()
        else:
            self.show()

    def set_status(self, text: str, level: str = "SUCCESS"):
        color = LEVELS.get(level.upper(), FG)
        def _update():
            self._status_label.configure(text=text, fg=color)
        self.root.after(0, _update)

    # ── Internal (must run on main thread) ───────────────────────────────────

    def _show(self):
        self.root.deiconify()
        self.root.lift()
        self._visible = True

    def _hide(self):
        self.root.withdraw()
        self._visible = False

    def _clear(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")

    def _flush_pending(self):
        with self._lock:
            items, self._pending = self._pending, []
        if items:
            self._text.configure(state="normal")
            for message, level in items:
                ts = datetime.now().strftime("%H:%M:%S")
                self._text.insert("end", f"[{ts}] ", "ts")
                self._text.insert("end", f"{level:<7} ", f"lvl_{level}")
                self._text.insert("end", f"{message}\n", "msg")
            self._text.see("end")
            self._text.configure(state="disabled")

    def run(self):
        """Blocking — call from the main thread."""
        def poll():
            self._flush_pending()
            self.root.after(100, poll)
        self.root.after(100, poll)
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# Window helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_active_window_title() -> str:
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


# ─────────────────────────────────────────────────────────────────────────────
# Minecraft server detection
# ─────────────────────────────────────────────────────────────────────────────

# Minecraft's Quick Play feature records the directly-connected server on the
# game's own command line (e.g. "--quickPlayMultiplayer strayamc.sparked.network").
# We read it back to learn which server the client is on, so macros can send
# server-appropriate commands.
_QUICKPLAY_FLAG = "--quickPlayMultiplayer"

_server_cache = {"value": None, "at": 0.0}
_SERVER_CACHE_TTL = 5.0  # seconds; scanning every process is too slow per-hotkey


def get_minecraft_server(use_cache: bool = True):
    """Return the server the running Minecraft client was launched into, or None.

    IMPORTANT: this is the server the client was *launched* with via the
    launcher's Quick Play. If you join a different server from the in-game
    multiplayer list, the command line does not change, so this won't reflect it.
    """
    now = time.monotonic()
    if use_cache and (now - _server_cache["at"]) < _SERVER_CACHE_TTL:
        return _server_cache["value"]

    server = None
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if "java" not in (proc.info["name"] or "").lower():
                continue
            cmdline = proc.info["cmdline"] or []
            for i, arg in enumerate(cmdline):
                if arg == _QUICKPLAY_FLAG and i + 1 < len(cmdline):
                    server = cmdline[i + 1]
                    break
                if arg.startswith(_QUICKPLAY_FLAG + "="):  # tolerate --flag=value form
                    server = arg.split("=", 1)[1]
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if server:
            break

    _server_cache["value"] = server
    _server_cache["at"] = now
    return server


# ─────────────────────────────────────────────────────────────────────────────
# Physical input suppression
# ─────────────────────────────────────────────────────────────────────────────

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN     = 0x0100
WM_SYSKEYDOWN  = 0x0104
WM_QUIT        = 0x0012
LLKHF_INJECTED = 0x10
HC_ACTION      = 0


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      wintypes.DWORD),
        ("scanCode",    wintypes.DWORD),
        ("flags",       wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class KeyboardSuppressor:
    """Swallow the user's real keypresses while a macro types into the game.

    BlockInput() is the obvious API for this, but since Vista it is gated by
    UIPI: called from a non-elevated process it returns 0 with
    ERROR_ACCESS_DENIED and does nothing whatsoever - which is why typing over
    a macro used to leak through. A WH_KEYBOARD_LL hook needs no elevation and
    can tell our injected keystrokes (LLKHF_INJECTED) from real ones, so only
    the real ones get dropped.

    Two deliberate escape hatches, because a wedged hook means a dead keyboard:
      * key-UP is always passed through. If you are holding W when a macro
        fires, swallowing the release would leave you walking into a ravine.
      * suppression self-expires after max_seconds even if __exit__ never runs.

    The hook must live on a thread that pumps messages, or Windows silently
    drops it after LowLevelHooksTimeout - hence the dedicated pump thread.
    """

    def __init__(self, max_seconds: float = 5.0, also_block_mouse: bool = True):
        self.max_seconds = max_seconds
        self.also_block_mouse = also_block_mouse
        self.suppressed = 0
        self.hard_blocked = False   # True if BlockInput() was actually allowed
        self._hook = None
        self._thread = None
        self._thread_id = None
        self._deadline = 0.0
        self._ready = threading.Event()
        # Keep a strong reference: if this is garbage collected while the hook
        # is installed, Windows calls into freed memory.
        self._proc = _HOOKPROC(self._callback)

    # ── hook internals ───────────────────────────────────────────────────────

    def _callback(self, n_code, w_param, l_param):
        if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            physical = not (info.flags & LLKHF_INJECTED)
            if physical and time.monotonic() < self._deadline:
                self.suppressed += 1
                return 1  # non-zero = swallow, never reaches the game
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _pump(self):
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0
        )
        self._ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        # Present only to service the hook; PostThreadMessage(WM_QUIT) ends it.
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self):
        self.suppressed = 0
        self._deadline = time.monotonic() + self.max_seconds
        self._ready.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        # Bonus when running elevated: this also freezes the mouse, which a
        # keyboard hook cannot do. Harmless no-op when UIPI denies it.
        if self.also_block_mouse:
            self.hard_blocked = bool(_user32.BlockInput(True))
        return self

    def __exit__(self, *_exc):
        self._deadline = 0.0  # stop suppressing before we even unwind
        if self.hard_blocked:
            _user32.BlockInput(False)
            self.hard_blocked = False
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2.0)
        self._thread = self._thread_id = None
        return False

    @property
    def active(self) -> bool:
        """True if the hook is installed and really intercepting keys."""
        return bool(self._hook)


# ─────────────────────────────────────────────────────────────────────────────
# Profile base
# ─────────────────────────────────────────────────────────────────────────────

class Profile:
    """
    Base class for app profiles.
    Subclasses define:
      WINDOW_KEYWORD  – matched (case-insensitive) against the active window title
      hotkeys         – dict of {hotkey_str: handler_method}
    """
    WINDOW_KEYWORD: str = ""

    @property
    def hotkeys(self) -> dict:
        return {}

    def is_active_window(self) -> bool:
        return self.WINDOW_KEYWORD.lower() in get_active_window_title().lower()

    def dispatch(self, hotkey: str, log):
        handler = self.hotkeys.get(hotkey)
        if handler is None:
            return
        if not self.is_active_window():
            log(f"[{self.WINDOW_KEYWORD}] '{hotkey}' fired but window not focused — skipped", "WARN")
            return
        log(f"[{self.WINDOW_KEYWORD}] '{hotkey}' → {handler.__name__}()", "EVENT")
        try:
            import inspect
            if "log" in inspect.signature(handler).parameters:
                handler(log)
            else:
                handler()
        except Exception as e:
            log(f"[{self.WINDOW_KEYWORD}] error in {handler.__name__}: {e}", "ERROR")


# ─────────────────────────────────────────────────────────────────────────────
# Minecraft Profile
# ─────────────────────────────────────────────────────────────────────────────

class MinecraftProfile(Profile):
    WINDOW_KEYWORD = "Minecraft"

    # Known servers, so the command methods below can read as
    # `case self.STRAYA:` instead of a bare string literal.
    STRAYA = "strayamc.sparked.network"

    # Vote pages tied to each server. vote_server() opens every URL for whichever
    # server is running in a visible browser and pre-fills the username via that
    # site's profile in mcvote (VOTE_SITE_PROFILES); you solve the captcha / log
    # in and click the final vote button.
    VOTE_SITES = [
        {"server": STRAYA, "voteurls": [
            "https://minecraftservers.org/vote/000000",
            "https://minecraft-serverlist.com/server/0000/vote",
            "https://www.planetminecraft.com/server/example/vote/",
            "https://www.minecraftiplist.com/server/example-00000/vote",
            "https://craftlist.org/example#vote",
            
        ]},
    ]

    @property
    def hotkeys(self) -> dict:
        return {
            "*f23": self.go_home,
            "*f22": self.go_spawn,
            "*f24": self.go_shop,
            "*f20": self.vote_server,
        }

    def _send_chat(self, message: str, log=None):
        """Open Minecraft chat, paste a message, and submit. Preserves clipboard.

        Real keyboard input is suppressed for the duration so a keypress mid-macro
        cannot land in the chat box or cancel it.
        """
        import win32clipboard

        def snapshot_clipboard():
            """Return a dict of {format: data} for everything on the clipboard."""
            data = {}
            try:
                win32clipboard.OpenClipboard()
                fmt = win32clipboard.EnumClipboardFormats(0)
                while fmt:
                    try:
                        data[fmt] = win32clipboard.GetClipboardData(fmt)
                    except Exception:
                        pass  # some formats can't be read directly; skip them
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
            finally:
                win32clipboard.CloseClipboard()
            return data

        def restore_clipboard(snapshot):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                for fmt, data in snapshot.items():
                    try:
                        win32clipboard.SetClipboardData(fmt, data)
                    except Exception:
                        pass  # skip formats that can't be restored (e.g. owner-drawn)
            finally:
                win32clipboard.CloseClipboard()

        def set_clipboard_text(text):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()

        previous = snapshot_clipboard()
        set_clipboard_text(message)

        with KeyboardSuppressor() as blocker:
            if log and not blocker.active:
                log("Could not install the keyboard hook — real keys will leak "
                    "into this macro", "WARN")
            pyautogui.press('t')
            time.sleep(0.3)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            pyautogui.press('enter')

        if log and blocker.suppressed:
            log(f"Suppressed {blocker.suppressed} physical keypress(es) during '{message}'",
                "DEBUG")

        restore_clipboard(previous)

    def go_home(self, log):
        # NB: `case self.STRAYA` (a dotted name) compares against the constant.
        # A bare `case STRAYA` would instead capture *any* value into a new
        # variable and always match - so keep server constants dotted here.
        match get_minecraft_server():
            case self.STRAYA:
                self._send_chat("/is home", log)   # skyblock: island home, not /home
            case _:
                self._send_chat("/home", log)

    def go_shop(self, log):
        match get_minecraft_server():
            case _:
                self._send_chat("/shop", log)

    def go_spawn(self, log):
        match get_minecraft_server():
            case _:
                self._send_chat("/spawn", log)

    def _vote_urls_for(self, server) -> list:
        """The configured vote URLs for a server, or [] if none/unknown."""
        for site in self.VOTE_SITES:
            if site["server"] == server:
                return site["voteurls"]
        return []

    def vote_server(self, log):
        if open_vote_pages is None:
            log("mcvote module not available", "WARN")
            return
        server = get_minecraft_server()
        urls = self._vote_urls_for(server)
        if not urls:
            log(f"No vote URLs configured for {server or 'this server'}", "WARN")
            return
        # Open in the user's real Firefox so Cloudflare Turnstile isn't tripped;
        # the Tampermonkey userscript fills the username on each page.
        log(f"Opening {len(urls)} vote page(s) for {server} in Firefox; "
            f"solve each captcha and click vote.", "INFO")
        open_vote_pages(urls, callback=lambda msg: log(f"[VOTE] {msg}", "INFO"))


# ─────────────────────────────────────────────────────────────────────────────
# Log Window Profile
# ─────────────────────────────────────────────────────────────────────────────

class LogWindowProfile(Profile):
    WINDOW_KEYWORD = "send_to_window"

    @property
    def hotkeys(self) -> dict:
        return {
            "f23": self.reload,
        }

    def reload(self, log):
        log("Reloading…", "WARN")
        time.sleep(0.2)
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        os._exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Rust Profile  (stub — expand later)
# ─────────────────────────────────────────────────────────────────────────────

class RustProfile(Profile):
    WINDOW_KEYWORD = "Rust"

    @property
    def hotkeys(self) -> dict:
        return {
            # "f13": self.open_team_chat,
        }


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

class App:
    PROFILES: list = [
        LogWindowProfile(),
        MinecraftProfile(),
        RustProfile(),
    ]

    def __init__(self):
        self.log_win = LogWindow()
        self._tray = None

    # ── Hotkey registration ──────────────────────────────────────────────────

    def _collect_all_hotkeys(self) -> dict:
        """Build a map of hotkey_str → [profiles that handle it]."""
        mapping = {}
        for profile in self.PROFILES:
            for hk in profile.hotkeys:
                mapping.setdefault(hk, []).append(profile)
        return mapping

    def _register_hotkeys(self):
        mapping = self._collect_all_hotkeys()
        for hk, profiles in mapping.items():
            def make_handler(h=hk, ps=profiles):
                def handler():
                    active = get_active_window_title()
                    self.log_win.log(f"Key: {h}  |  window: '{active}'", "EVENT")
                    for profile in ps:
                        if profile.is_active_window():
                            profile.dispatch(h, self.log_win.log)
                            return
                    self.log_win.log(
                        f"'{h}' fired — no matching profile for '{active}'", "DEBUG"
                    )
                return handler
            if hk.startswith('*'):
                key_name = hk[1:]
                fn = make_handler()
                keyboard.on_press_key(key_name, lambda _, f=fn: f())
                self.log_win.log(
                    f"Registered '*{key_name}' (any-modifier) → {[p.WINDOW_KEYWORD for p in profiles]}", "DEBUG"
                )
            else:
                keyboard.add_hotkey(hk, make_handler(), suppress=False)
                self.log_win.log(
                    f"Registered '{hk}' → {[p.WINDOW_KEYWORD for p in profiles]}", "DEBUG"
                )

    # ── System tray ──────────────────────────────────────────────────────────

    def _make_tray_icon(self) -> Image.Image:
        return make_app_image(64)

    def _reload(self, _icon=None, _item=None):
        self.log_win.log("Reloading…", "WARN")
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        os._exit(0)

    def _build_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show / Hide Log", lambda icon, item: self.log_win.toggle()),
            pystray.MenuItem("Reload", self._reload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        self._tray = pystray.Icon(
            "send_to_window",
            self._make_tray_icon(),
            "send_to_window",
            menu,
        )

    def _quit(self, icon=None, item=None):
        self.log_win.log("Shutting down…", "WARN")
        keyboard.unhook_all()
        if self._tray:
            self._tray.stop()
        self.log_win.root.after(300, self.log_win.root.quit)

    # ── Entry point ──────────────────────────────────────────────────────────

    def run(self):
        self.log_win.log("send_to_window starting up", "INFO")
        self._register_hotkeys()
        self.log_win.log("Global hotkey listener active", "SUCCESS")
        self.log_win.set_status("● listening", "SUCCESS")

        self._build_tray()
        tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        tray_thread.start()

        # tkinter must own the main thread on Windows
        self.log_win.run()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().run()