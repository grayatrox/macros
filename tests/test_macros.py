"""Tests for the hotkey app's window-independent logic (Macros.py)."""

from __future__ import annotations

from typing import Any

import psutil
import pytest

import Macros
from Macros import App, MinecraftProfile, Profile, server_from_cmdline

STRAYA = MinecraftProfile.STRAYA


# ── server_from_cmdline ──────────────────────────────────────────────────────


def test_server_from_separate_flag_value() -> None:
    cmdline = [
        "javaw.exe",
        "-Xmx4G",
        "--quickPlayMultiplayer",
        STRAYA,
        "--width",
        "854",
    ]
    assert server_from_cmdline("javaw.exe", cmdline) == STRAYA


def test_server_from_equals_form() -> None:
    cmdline = ["java", f"--quickPlayMultiplayer={STRAYA}"]
    assert server_from_cmdline("java.exe", cmdline) == STRAYA


def test_non_java_process_is_ignored() -> None:
    assert server_from_cmdline("firefox.exe", ["--quickPlayMultiplayer", STRAYA]) is None


def test_flag_without_value_yields_none() -> None:
    assert server_from_cmdline("javaw.exe", ["javaw.exe", "--quickPlayMultiplayer"]) is None


def test_java_without_quickplay_yields_none() -> None:
    assert server_from_cmdline("javaw.exe", ["javaw.exe", "-jar", "server.jar"]) is None


@pytest.mark.parametrize(("name", "cmdline"), [(None, None), ("javaw.exe", None), (None, [])])
def test_missing_process_info_yields_none(name: str | None, cmdline: list[str] | None) -> None:
    assert server_from_cmdline(name, cmdline) is None


# ── get_minecraft_server ─────────────────────────────────────────────────────


class FakeProc:
    def __init__(self, name: str | None, cmdline: list[str] | None, *, deny: bool = False):
        self._info = {"name": name, "cmdline": cmdline}
        self._deny = deny

    @property
    def info(self) -> dict[str, Any]:
        if self._deny:
            raise psutil.AccessDenied(pid=1)
        return self._info


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> list[FakeProc]:
    """The process table psutil reports; tests append to it."""
    table: list[FakeProc] = []
    monkeypatch.setattr(Macros.psutil, "process_iter", lambda _attrs: iter(table))
    monkeypatch.setattr(Macros, "_server_cache", {"value": None, "at": float("-inf")})
    return table


def test_get_server_skips_inaccessible_processes(processes: list[FakeProc]) -> None:
    processes.append(FakeProc("javaw.exe", None, deny=True))
    processes.append(FakeProc("javaw.exe", ["--quickPlayMultiplayer", STRAYA]))
    assert Macros.get_minecraft_server(use_cache=False) == STRAYA


def test_get_server_none_when_minecraft_not_running(processes: list[FakeProc]) -> None:
    processes.append(FakeProc("explorer.exe", ["explorer.exe"]))
    assert Macros.get_minecraft_server(use_cache=False) is None


def test_get_server_cached_result_is_reused(processes: list[FakeProc]) -> None:
    processes.append(FakeProc("javaw.exe", ["--quickPlayMultiplayer", STRAYA]))
    assert Macros.get_minecraft_server() == STRAYA
    processes.clear()  # client quit, but within the TTL the cached answer stands
    assert Macros.get_minecraft_server() == STRAYA
    assert Macros.get_minecraft_server(use_cache=False) is None


# ── MinecraftProfile._vote_urls_for ──────────────────────────────────────────


def test_vote_urls_for_known_server() -> None:
    urls = MinecraftProfile()._vote_urls_for(STRAYA)
    assert urls == MinecraftProfile.VOTE_SITES[0]["voteurls"]
    assert all(url.startswith("https://") for url in urls)


@pytest.mark.parametrize("server", [None, "play.example.net"])
def test_vote_urls_for_unknown_server(server: str | None) -> None:
    assert MinecraftProfile()._vote_urls_for(server) == []


# ── App._collect_all_hotkeys ─────────────────────────────────────────────────


class KeysProfile(Profile):
    def __init__(self, keyword: str, keys: list[str]) -> None:
        self.WINDOW_KEYWORD = keyword
        self._keys = keys

    @property
    def hotkeys(self) -> dict[str, Any]:
        return {k: print for k in self._keys}


def test_collect_all_hotkeys_groups_profiles_by_key() -> None:
    a, b = KeysProfile("A", ["f1", "f2"]), KeysProfile("B", ["f2"])
    app = App.__new__(App)  # skip __init__: it opens the Tk log window
    app.PROFILES = [a, b]
    assert app._collect_all_hotkeys() == {"f1": [a], "f2": [a, b]}


def test_default_profiles_register_the_minecraft_keys() -> None:
    mapping = App.__new__(App)._collect_all_hotkeys()
    assert set(mapping) >= {"*f20", "*f22", "*f23", "*f24"}


# ── Profile.dispatch ─────────────────────────────────────────────────────────


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __call__(self, message: str, level: str = "INFO") -> None:
        self.lines.append((message, level))

    def levels(self) -> list[str]:
        return [level for _, level in self.lines]


class DispatchProfile(Profile):
    WINDOW_KEYWORD = "Test"

    def __init__(self, *, focused: bool) -> None:
        self.focused = focused
        self.calls: list[str] = []

    def is_active_window(self) -> bool:
        return self.focused

    @property
    def hotkeys(self) -> dict[str, Any]:
        return {"f1": self.no_log, "f2": self.with_log, "f3": self.boom}

    def no_log(self) -> None:
        self.calls.append("no_log")

    def with_log(self, log: Recorder) -> None:
        self.calls.append("with_log")
        log("inside", "DEBUG")

    def boom(self) -> None:
        raise RuntimeError("kaput")


def test_dispatch_unknown_hotkey_does_nothing() -> None:
    profile, log = DispatchProfile(focused=True), Recorder()
    profile.dispatch("f9", log)
    assert profile.calls == []
    assert log.lines == []


def test_dispatch_skips_when_window_not_focused() -> None:
    profile, log = DispatchProfile(focused=False), Recorder()
    profile.dispatch("f1", log)
    assert profile.calls == []
    assert log.levels() == ["WARN"]


def test_dispatch_calls_handler_without_log_param() -> None:
    profile, log = DispatchProfile(focused=True), Recorder()
    profile.dispatch("f1", log)
    assert profile.calls == ["no_log"]
    assert log.levels() == ["EVENT"]


def test_dispatch_passes_log_to_handler_that_wants_it() -> None:
    profile, log = DispatchProfile(focused=True), Recorder()
    profile.dispatch("f2", log)
    assert profile.calls == ["with_log"]
    assert log.lines[-1] == ("inside", "DEBUG")


def test_dispatch_logs_handler_error_instead_of_raising() -> None:
    profile, log = DispatchProfile(focused=True), Recorder()
    profile.dispatch("f3", log)
    assert log.levels() == ["EVENT", "ERROR"]
    assert "kaput" in log.lines[-1][0]


# ── vote_server when mcvote cannot be imported (OP #635) ─────────────────────


def test_vote_server_logs_why_mcvote_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Macros, "open_vote_pages", None)
    monkeypatch.setattr(Macros, "mcvote_import_error", ImportError("No module named 'selenium'"))
    log = Recorder()
    MinecraftProfile().vote_server(log)
    assert log.levels() == ["WARN"]
    assert "No module named 'selenium'" in log.lines[0][0]
