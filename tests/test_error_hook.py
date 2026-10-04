"""pluginhost.error_hook: uncaught exceptions are logged and handed to the app (OP #697).

Shared by Macros (its log window) and Recipe Calc (a dialog), so behaviour is
tested here once, through the hooks it installs.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterator

import pytest

from pluginhost.error_hook import install_excepthook

LOGGER = "test_error_hook"


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[BaseException, str]]]:
    """Install the hooks with a recording notify; both hooks are restored afterwards."""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    calls: list[tuple[BaseException, str]] = []
    install_excepthook(lambda exc, details: calls.append((exc, details)), logging.getLogger(LOGGER))
    yield calls


def raised(exc: BaseException) -> BaseException:
    """``exc`` with a real traceback attached."""
    try:
        raise exc
    except BaseException as caught:
        return caught


def test_an_uncaught_exception_is_logged_then_notified(
    notified: list[tuple[BaseException, str]], caplog: pytest.LogCaptureFixture
) -> None:
    error = raised(ValueError("boom"))

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        sys.excepthook(ValueError, error, error.__traceback__)

    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert "Traceback" in record.getMessage()
    assert "ValueError: boom" in record.getMessage()
    ((exc, details),) = notified
    assert exc is error
    assert "Traceback" in details
    assert "ValueError: boom" in details


def test_an_exception_on_a_worker_thread_is_logged_and_notified(
    notified: list[tuple[BaseException, str]], caplog: pytest.LogCaptureFixture
) -> None:
    def broken_worker() -> None:
        raise RuntimeError("worker blew up")

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        thread = threading.Thread(target=broken_worker)
        thread.start()
        thread.join()

    assert "RuntimeError: worker blew up" in caplog.text
    ((exc, details),) = notified
    assert isinstance(exc, RuntimeError)
    assert "worker blew up" in details


def test_a_thread_ending_with_system_exit_is_not_an_error(
    notified: list[tuple[BaseException, str]], caplog: pytest.LogCaptureFixture
) -> None:
    def exiting_worker() -> None:
        raise SystemExit(0)

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        thread = threading.Thread(target=exiting_worker)
        thread.start()
        thread.join()

    assert notified == []
    assert caplog.records == []


def test_a_failing_notify_does_not_hide_the_original_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)

    def broken_notify(_exc: BaseException, _details: str) -> None:
        raise OSError("no display")

    install_excepthook(broken_notify, logging.getLogger(LOGGER))
    error = raised(ValueError("original"))

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        sys.excepthook(ValueError, error, error.__traceback__)  # must not raise

    original, failure = caplog.records
    assert "ValueError: original" in original.getMessage()
    assert failure.getMessage() == "Could not report the unhandled exception"
    assert failure.exc_info is not None
    assert failure.exc_info[0] is OSError


def test_install_replaces_both_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)

    hook = install_excepthook(lambda _exc, _details: None, logging.getLogger(LOGGER))

    assert sys.excepthook == hook.report
    assert threading.excepthook == hook.report_thread
