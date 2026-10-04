"""Where every app's log files go (OP #704).

LOCALAPPDATA and the temp folder are pointed at tmp_path, so no test writes to
the real per-user folders.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pluginhost import bootstrap
from pluginhost.log_dir import log_dir


@pytest.fixture
def local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch LOCALAPPDATA, and a scratch temp folder for the fallback."""
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    return local


def test_data_dir_is_macros_under_local_app_data(local: Path) -> None:
    assert bootstrap.data_dir() == local / "Macros"
    assert bootstrap.deps_dir().parent == bootstrap.data_dir()


def test_logs_go_in_a_logs_folder_under_local_app_data(local: Path) -> None:
    assert log_dir() == local / "Macros" / "logs"


def test_the_folder_is_created(local: Path) -> None:
    assert not (local / "Macros").exists()
    assert log_dir().is_dir()


def test_an_existing_folder_and_its_logs_are_kept(local: Path) -> None:
    existing = local / "Macros" / "logs" / "launcher.log"
    existing.parent.mkdir(parents=True)
    existing.write_text("old run\n", encoding="utf-8")

    assert log_dir() == existing.parent
    assert existing.read_text(encoding="utf-8") == "old run\n"
    assert [p.name for p in existing.parent.iterdir()] == ["launcher.log"]


def test_unwritable_local_app_data_falls_back_to_temp(local: Path, tmp_path: Path) -> None:
    (local / "Macros").write_text("a file where the folder should be", encoding="utf-8")

    assert log_dir() == tmp_path / "temp" / "Macros" / "logs"
    assert log_dir().is_dir()


def test_nothing_writable_returns_the_preferred_folder(local: Path, tmp_path: Path) -> None:
    """The caller's open then fails, and it reports the path the user would look in."""
    (local / "Macros").write_text("blocked", encoding="utf-8")
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp" / "Macros").write_text("blocked", encoding="utf-8")

    assert log_dir() == local / "Macros" / "logs"
