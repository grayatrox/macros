"""Only one copy of the hotkey app may hook the hotkeys at a time (OP #633).

These use real Windows named mutexes, each with a unique name, so they test
the actual cross-process behaviour rather than a fake of it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from macros import app as macros_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def name() -> str:
    return f"Local\\macros-test-{uuid.uuid4()}"


@pytest.fixture
def held(name: str) -> Iterator[int]:
    handle = macros_app.acquire_single_instance(name)
    assert handle is not None
    yield handle
    macros_app.release_single_instance(handle)


def test_first_acquire_succeeds_and_can_be_released(name: str) -> None:
    handle = macros_app.acquire_single_instance(name)
    assert handle is not None
    macros_app.release_single_instance(handle)
    again = macros_app.acquire_single_instance(name)
    assert again is not None
    macros_app.release_single_instance(again)


def test_second_acquire_fails_while_held(name: str, held: int) -> None:
    assert macros_app.acquire_single_instance(name) is None


def test_another_process_cannot_acquire_while_held(name: str, held: int) -> None:
    probe = (
        "import sys; from macros import app; "
        "sys.stdout.write(str(app.acquire_single_instance(sys.argv[1]) is None))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, name],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert result.stdout == "True"


def test_wait_succeeds_once_the_holder_releases(name: str) -> None:
    handle = macros_app.acquire_single_instance(name)
    assert handle is not None
    releaser = threading.Timer(0.2, macros_app.release_single_instance, args=(handle,))
    releaser.start()
    try:
        acquired = macros_app.acquire_single_instance(name, wait_seconds=5)
        assert acquired is not None
        macros_app.release_single_instance(acquired)
    finally:
        releaser.join()


def test_wait_gives_up_while_still_held(name: str, held: int) -> None:
    assert macros_app.acquire_single_instance(name, wait_seconds=0.3) is None


# ── restart_process ──────────────────────────────────────────────────────────


class ExitedError(Exception):
    pass


@pytest.mark.parametrize(
    "argv",
    [["Macros.py"], ["Macros.py", macros_app.RESTART_FLAG]],
    ids=["first-reload", "repeat-reload"],
)
def test_restart_relaunches_with_the_flag_exactly_once(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    launched: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: launched.append(list(cmd)))

    def fake_exit(code: int) -> None:
        raise ExitedError(code)

    monkeypatch.setattr(os, "_exit", fake_exit)
    with pytest.raises(ExitedError):
        macros_app.restart_process()
    assert launched == [[sys.executable, "Macros.py", macros_app.RESTART_FLAG]]
