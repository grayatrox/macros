"""Profiles must match the foreground *application*, not any window that
merely has the keyword in its title (OP #634).

The foreground window, its owning PID and the process name all come from the
OS, so those calls are faked at the win32gui / win32process / psutil boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psutil
import pytest
import win32gui
import win32process

from macros.app import LogWindowProfile, MinecraftProfile, RustProfile

HWND = 0x1234


@dataclass
class Foreground:
    title: str
    exe: str | None
    pid: int = 4242


@pytest.fixture
def foreground(monkeypatch: pytest.MonkeyPatch) -> Foreground:
    """Whatever window is in front; tests overwrite its fields."""
    window = Foreground(title="", exe=None)

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            if window.exe is None:
                raise psutil.AccessDenied(pid=pid)
            self._pid = pid

        def name(self) -> str:
            assert window.exe is not None
            return window.exe

    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: HWND)
    monkeypatch.setattr(win32gui, "GetWindowText", lambda _h: window.title)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda _h: (1, window.pid))
    monkeypatch.setattr(psutil, "Process", FakeProcess)
    return window


@pytest.mark.parametrize(
    ("title", "exe"),
    [
        ("Vote for StrayaMC - Minecraft Server List — Mozilla Firefox", "firefox.exe"),
        ("Minecraft Wiki - Google Chrome", "chrome.exe"),
        ("C:\\Games\\Minecraft", "explorer.exe"),
        ("Recpe_Calc.py - Minecraft Recipe Calculator", "python.exe"),
    ],
)
def test_minecraft_profile_ignores_other_apps_with_minecraft_in_title(
    foreground: Foreground, title: str, exe: str
) -> None:
    foreground.title, foreground.exe = title, exe
    assert not MinecraftProfile().is_active_window()


@pytest.mark.parametrize("exe", ["javaw.exe", "java.exe", "JAVAW.EXE"])
def test_minecraft_profile_matches_the_java_client(foreground: Foreground, exe: str) -> None:
    foreground.title, foreground.exe = (
        "Minecraft 1.21.1 - Multiplayer (3rd-party Server)",
        exe,
    )
    assert MinecraftProfile().is_active_window()


def test_minecraft_profile_needs_the_title_too(foreground: Foreground) -> None:
    # e.g. the Modrinth launcher or an IDE running some other Java program
    foreground.title, foreground.exe = "IntelliJ IDEA", "javaw.exe"
    assert not MinecraftProfile().is_active_window()


def test_unknown_process_never_matches(foreground: Foreground) -> None:
    # psutil could not read the process (it exited, or access was denied)
    foreground.title, foreground.exe = "Minecraft 1.21.1", None
    assert not MinecraftProfile().is_active_window()


def test_log_profile_matches_only_this_process(foreground: Foreground) -> None:
    foreground.title, foreground.exe = "send_to_window — log", "pythonw.exe"
    foreground.pid = os.getpid()
    assert LogWindowProfile().is_active_window()
    foreground.pid = os.getpid() + 1  # a second copy, or a file called send_to_window
    assert not LogWindowProfile().is_active_window()


def test_rust_profile_matches_only_the_game(foreground: Foreground) -> None:
    foreground.title, foreground.exe = "Rust", "RustClient.exe"
    assert RustProfile().is_active_window()
    foreground.title, foreground.exe = "main.rs - Rust - Visual Studio Code", "Code.exe"
    assert not RustProfile().is_active_window()
