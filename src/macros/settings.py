"""Personal settings: the Minecraft username and the vote pages per server.

They live in ``settings.json`` at the repository root, which is git-ignored so
that nothing identifying is committed; ``settings.example.json`` is the
committed template. They are read and validated once, at startup, and a bad or
missing file stops the app with a message saying what to fix.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "settings.json"
EXAMPLE_PATH = ROOT / "settings.example.json"


class SettingsError(Exception):
    """The settings file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Settings:
    """Validated personal settings.

    username: the Minecraft username the vote helpers fill in.
    vote_sites: server address -> the vote page URLs for that server.
    """

    username: str
    vote_sites: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    """Read and validate the settings file at ``path``.

    Raises:
        SettingsError: the file is missing, is not valid JSON, or a value has
            the wrong shape. The message names the file and the offending key.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SettingsError(
            f"No settings file at {path}. Copy {EXAMPLE_PATH.name} to {path.name} "
            "next to it and fill in your own values."
        ) from None
    except OSError as exc:
        raise SettingsError(f"Cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SettingsError(f"{path} is not valid JSON: {exc}") from exc
    return parse_settings(data, source=str(path))


def parse_settings(data: Any, source: str = "settings") -> Settings:
    """Validate already-decoded settings data. ``source`` names it in errors.

    Raises:
        SettingsError: a value is missing or has the wrong shape.
    """
    if not isinstance(data, dict):
        raise SettingsError(f"{source}: expected a JSON object at the top level")

    username = data.get("username")
    if not isinstance(username, str) or not username.strip():
        raise SettingsError(f"{source}: 'username' must be a non-empty string")

    raw_sites = data.get("vote_sites", {})
    if not isinstance(raw_sites, dict):
        raise SettingsError(f"{source}: 'vote_sites' must map a server address to a list of URLs")
    vote_sites: dict[str, tuple[str, ...]] = {}
    for server, urls in raw_sites.items():
        if not isinstance(urls, list) or not all(
            isinstance(url, str) and url.startswith("https://") for url in urls
        ):
            raise SettingsError(f"{source}: 'vote_sites.{server}' must be a list of https:// URLs")
        vote_sites[server] = tuple(urls)

    return Settings(username=username.strip(), vote_sites=vote_sites)
