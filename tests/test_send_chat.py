"""The chat macro must always hand the user's clipboard back (OP #636).

The Windows clipboard, the low-level keyboard hook and synthetic keypresses
are all OS-level, so each is replaced with an in-memory fake.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any, Self

import pyautogui
import pytest

import Macros
from Macros import MinecraftProfile

CF_UNICODETEXT = 13
CF_PRIVATE = 0x0200  # an app-private format that cannot be put back


class FakeClipboard:
    """Just enough of win32clipboard for _send_chat.

    Method names mirror the pywin32 API, hence the CamelCase.
    """

    CF_UNICODETEXT = CF_UNICODETEXT
    error = OSError  # stands in for pywintypes.error

    def __init__(self, contents: dict[int, Any]) -> None:
        self.contents = dict(contents)
        self.unsettable: set[int] = set()

    def OpenClipboard(self) -> None:
        pass

    def CloseClipboard(self) -> None:
        pass

    def EmptyClipboard(self) -> None:
        self.contents.clear()

    def EnumClipboardFormats(self, fmt: int) -> int:
        formats = list(self.contents)
        index = 0 if fmt == 0 else formats.index(fmt) + 1
        return formats[index] if index < len(formats) else 0

    def GetClipboardData(self, fmt: int) -> Any:
        return self.contents[fmt]

    def SetClipboardData(self, fmt: int, data: Any) -> None:
        if fmt in self.unsettable:
            raise TypeError(f"format {fmt} cannot be set")
        self.contents[fmt] = data


class NoHook:
    """Stands in for KeyboardSuppressor, which installs a real OS hook."""

    active = True
    suppressed = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __call__(self, message: str, level: str = "INFO") -> None:
        self.lines.append((message, level))


@pytest.fixture
def clipboard(monkeypatch: pytest.MonkeyPatch) -> FakeClipboard:
    fake = FakeClipboard({CF_UNICODETEXT: "the user's own text"})
    monkeypatch.setitem(sys.modules, "win32clipboard", fake)
    monkeypatch.setattr(Macros, "KeyboardSuppressor", NoHook)
    monkeypatch.setattr(Macros, "get_minecraft_server", lambda: None)
    monkeypatch.setattr(Macros.time, "sleep", lambda _s: None)
    return fake


def _keys(
    monkeypatch: pytest.MonkeyPatch, *, fail_on: str | None = None
) -> SimpleNamespace:
    seen = SimpleNamespace(pasted=None, presses=[])

    def press(key: str) -> None:
        if key == fail_on:
            raise pyautogui.FailSafeException("mouse moved to a screen corner")
        seen.presses.append(key)

    monkeypatch.setattr(pyautogui, "press", press)
    return seen


def test_clipboard_restored_when_keypress_fails(
    clipboard: FakeClipboard, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keys(monkeypatch, fail_on="t")
    with pytest.raises(pyautogui.FailSafeException):
        MinecraftProfile().go_spawn(Log())
    assert clipboard.contents == {CF_UNICODETEXT: "the user's own text"}


def test_message_is_pasted_then_clipboard_restored(
    clipboard: FakeClipboard, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _keys(monkeypatch)

    def hotkey(*keys: str) -> None:
        assert keys == ("ctrl", "v")
        seen.pasted = clipboard.contents.get(CF_UNICODETEXT)

    monkeypatch.setattr(pyautogui, "hotkey", hotkey)
    MinecraftProfile().go_spawn(Log())
    assert seen.pasted == "/spawn"
    assert seen.presses == ["t", "enter"]
    assert clipboard.contents == {CF_UNICODETEXT: "the user's own text"}


def test_format_that_cannot_be_restored_is_logged(
    clipboard: FakeClipboard, monkeypatch: pytest.MonkeyPatch
) -> None:
    clipboard.contents[CF_PRIVATE] = b"opaque"
    clipboard.unsettable.add(CF_PRIVATE)
    _keys(monkeypatch)
    monkeypatch.setattr(pyautogui, "hotkey", lambda *_k: None)
    log = Log()
    MinecraftProfile().go_spawn(log)
    assert clipboard.contents == {CF_UNICODETEXT: "the user's own text"}
    warnings = [message for message, level in log.lines if level == "WARN"]
    assert any(str(CF_PRIVATE) in message for message in warnings)
