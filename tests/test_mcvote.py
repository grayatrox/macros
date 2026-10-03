"""Tests for mcvote.open_vote_pages: input forms and browser fallback.

Launching a browser is genuinely external, so subprocess.Popen and
webbrowser.open are replaced with recorders.
"""

from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path

import pytest

import mcvote

URLS = ["https://a.example/vote", "https://b.example/vote"]


class Opened:
    def __init__(self) -> None:
        self.popen: list[list[str]] = []
        self.browser: list[str] = []
        self.log: list[str] = []


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Opened:
    record = Opened()
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv: record.popen.append(list(argv))
    )
    monkeypatch.setattr(webbrowser, "open", record.browser.append)
    monkeypatch.setattr(mcvote, "FIREFOX_PATHS", [])
    return record


def _firefox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    exe = tmp_path / "firefox.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mcvote, "FIREFOX_PATHS", [str(exe)])
    return str(exe)


def test_opens_all_urls_in_one_firefox_call(
    opened: Opened, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    firefox = _firefox(tmp_path, monkeypatch)
    assert mcvote.open_vote_pages(URLS, callback=opened.log.append) == 2
    assert opened.popen == [[firefox, *URLS]]
    assert opened.browser == []


def test_single_url_string_is_one_page(
    opened: Opened, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    firefox = _firefox(tmp_path, monkeypatch)
    assert mcvote.open_vote_pages(URLS[0], callback=opened.log.append) == 1
    assert opened.popen == [[firefox, URLS[0]]]


@pytest.mark.parametrize("urls", [[], None, ()])
def test_no_urls_opens_nothing(opened: Opened, urls: list[str] | None) -> None:
    assert mcvote.open_vote_pages(urls, callback=opened.log.append) == 0
    assert opened.popen == []
    assert opened.browser == []
    assert opened.log == ["No vote URLs supplied"]


def test_falls_back_to_default_browser_without_firefox(opened: Opened) -> None:
    assert mcvote.open_vote_pages(URLS, callback=opened.log.append) == 2
    assert opened.popen == []
    assert opened.browser == URLS


def test_falls_back_when_firefox_fails_to_launch(
    opened: Opened, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _firefox(tmp_path, monkeypatch)

    def broken(_argv: list[str]) -> None:
        raise OSError("not a valid Win32 application")

    monkeypatch.setattr(subprocess, "Popen", broken)
    assert mcvote.open_vote_pages(URLS, callback=opened.log.append) == 2
    assert opened.browser == URLS
    assert any("Could not launch Firefox" in line for line in opened.log)
