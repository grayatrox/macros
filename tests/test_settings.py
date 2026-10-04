"""Tests for macros.settings: the git-ignored personal settings file (OP #654)."""

from __future__ import annotations

from pathlib import Path

import pytest

from macros.settings import EXAMPLE_PATH, Settings, SettingsError, load_settings


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_username_and_vote_sites(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '{"username": "  Steve ", "vote_sites": {"play.example.net": ["https://a.example/v"]}}',
    )
    assert load_settings(path) == Settings(
        username="Steve", vote_sites={"play.example.net": ("https://a.example/v",)}
    )


def test_vote_sites_are_optional(tmp_path: Path) -> None:
    assert load_settings(write(tmp_path, '{"username": "Steve"}')).vote_sites == {}


def test_committed_example_is_valid() -> None:
    # The template must stay loadable, or copying it gives a broken start.
    assert load_settings(EXAMPLE_PATH).username == "YourMinecraftName"


def test_missing_file_says_to_copy_the_example(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"settings\.example\.json"):
        load_settings(tmp_path / "settings.json")


def test_invalid_json_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not valid JSON"):
        load_settings(write(tmp_path, '{"username": '))


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "{}",
        '{"username": ""}',
        '{"username": "   "}',
        '{"username": 42}',
    ],
)
def test_bad_username_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(SettingsError, match=r"username|top level"):
        load_settings(write(tmp_path, text))


@pytest.mark.parametrize(
    "sites",
    [
        '["https://a.example/v"]',
        '{"s": "https://a.example/v"}',
        '{"s": [1]}',
        '{"s": ["http://a.example/v"]}',
    ],
)
def test_bad_vote_sites_are_rejected(tmp_path: Path, sites: str) -> None:
    with pytest.raises(SettingsError, match="vote_sites"):
        load_settings(write(tmp_path, f'{{"username": "Steve", "vote_sites": {sites}}}'))
