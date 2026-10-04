"""The Macros log window (macros.app.LogWindow).

Hotkeys and the tray menu call the window from their own threads; the window
must only change once the GUI thread's event loop gets to the request.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from PySide6 import QtGui, QtWidgets

from macros.app import LEVELS, LogWindow, make_window_icon


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    # No real windows during a test run. Set before the QApplication exists,
    # which is never destroyed: Qt allows only one per process.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert isinstance(app, QtWidgets.QApplication)
    return app


class _Quit:
    """Stands in for QApplication.quit, which would end the shared test app."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def quit_app() -> _Quit:
    return _Quit()


@pytest.fixture
def log_window(qapp: QtWidgets.QApplication, quit_app: _Quit) -> Iterator[LogWindow]:
    window = LogWindow(clock=lambda: "12:34:56", quit_app=quit_app)
    try:
        yield window
    finally:
        window.window.hide()
        window.window.deleteLater()


def _text(window: LogWindow) -> str:
    log = window.window.findChild(QtWidgets.QPlainTextEdit, "log")
    assert log is not None
    return log.toPlainText()


def _colour_of(window: LogWindow, fragment_text: str) -> str:
    """The foreground colour the log drew ``fragment_text`` in."""
    log = window.window.findChild(QtWidgets.QPlainTextEdit, "log")
    assert log is not None
    block = log.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.text() == fragment_text:
                return fragment.charFormat().foreground().color().name()
            it += 1
        block = block.next()
    raise AssertionError(f"{fragment_text!r} not in the log")


def _status(window: LogWindow) -> QtWidgets.QLabel:
    label = window.window.findChild(QtWidgets.QLabel, "status")
    assert label is not None
    return label


def _from_thread(call: Callable[[], None]) -> None:
    thread = threading.Thread(target=call)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()


def _process_events_until(done: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not done():
        assert time.monotonic() < deadline, "the GUI thread never handled the request"
        QtWidgets.QApplication.processEvents()


def test_lines_show_the_time_level_and_message(log_window: LogWindow) -> None:
    log_window.log("starting up")
    log_window.log("it broke", "error")

    assert _text(log_window) == "[12:34:56] INFO    starting up\n[12:34:56] ERROR   it broke\n"


@pytest.mark.parametrize("level", list(LEVELS))
def test_each_level_is_drawn_in_its_colour(log_window: LogWindow, level: str) -> None:
    log_window.log("message", level)

    assert _colour_of(log_window, f"{level:<7} ") == LEVELS[level]


def test_an_unknown_level_is_still_logged(log_window: LogWindow) -> None:
    log_window.log("odd one", "TRACE")

    assert _text(log_window) == "[12:34:56] TRACE   odd one\n"


def test_the_time_is_taken_when_the_line_is_logged(qapp: QtWidgets.QApplication) -> None:
    times = iter(["10:00:00", "10:00:01"])
    window = LogWindow(clock=lambda: next(times))
    try:
        _from_thread(lambda: window.log("first"))
        _from_thread(lambda: window.log("second"))
        _process_events_until(lambda: "second" in _text(window))

        assert _text(window) == "[10:00:00] INFO    first\n[10:00:01] INFO    second\n"
    finally:
        window.window.deleteLater()


def test_a_line_logged_from_another_thread_waits_for_the_gui_thread(
    log_window: LogWindow,
) -> None:
    _from_thread(lambda: log_window.log("from the hotkey thread", "EVENT"))

    assert _text(log_window) == ""
    _process_events_until(lambda: _text(log_window) != "")
    assert _text(log_window) == "[12:34:56] EVENT   from the hotkey thread\n"


def test_show_and_hide_from_another_thread(log_window: LogWindow) -> None:
    _from_thread(log_window.show)
    assert not log_window.window.isVisible()
    _process_events_until(log_window.window.isVisible)

    _from_thread(log_window.hide)
    assert log_window.window.isVisible()
    _process_events_until(lambda: not log_window.window.isVisible())


def test_toggle_flips_visibility(log_window: LogWindow) -> None:
    _from_thread(log_window.toggle)
    _process_events_until(log_window.window.isVisible)

    _from_thread(log_window.toggle)
    _process_events_until(lambda: not log_window.window.isVisible())


def test_closing_the_window_only_hides_it(log_window: LogWindow, quit_app: _Quit) -> None:
    log_window.show()
    assert log_window.window.isVisible()

    closed = log_window.window.close()

    assert not closed
    assert not log_window.window.isVisible()
    assert quit_app.calls == 0


def test_status_starts_as_listening(log_window: LogWindow) -> None:
    assert _status(log_window).text() == "● listening"


def test_set_status_from_another_thread(log_window: LogWindow) -> None:
    _from_thread(lambda: log_window.set_status("● paused", "warn"))

    assert _status(log_window).text() == "● listening"
    _process_events_until(lambda: _status(log_window).text() == "● paused")
    label = _status(log_window)
    label.ensurePolished()
    assert label.palette().color(QtGui.QPalette.ColorRole.WindowText).name() == LEVELS["WARN"]


def test_clear_empties_the_log(log_window: LogWindow) -> None:
    log_window.log("old news")
    clear = log_window.window.findChild(QtWidgets.QPushButton, "clear")
    assert clear is not None

    clear.click()

    assert _text(log_window) == ""


def test_quit_from_another_thread_ends_the_event_loop(
    log_window: LogWindow, quit_app: _Quit
) -> None:
    _from_thread(lambda: log_window.quit(delay_ms=0))

    assert quit_app.calls == 0
    _process_events_until(lambda: quit_app.calls == 1)


def test_the_window_icon_is_drawn(qapp: QtWidgets.QApplication) -> None:
    assert not make_window_icon().isNull()


# ── Exceptions raised inside Qt slots (OP #676) ──────────────────────────────


def test_exception_in_a_qt_slot_is_logged_with_its_traceback(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from PySide6 import QtCore

    from macros.app import install_excepthook

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    logged: list[tuple[str, str]] = []
    install_excepthook(lambda message, level="INFO": logged.append((level, message)))

    class Emitter(QtCore.QObject):
        fired = QtCore.Signal()

    def broken_slot() -> None:
        raise ValueError("slot blew up")

    emitter = Emitter()
    emitter.fired.connect(broken_slot)
    emitter.fired.emit()

    assert len(logged) == 1
    level, message = logged[0]
    assert level == "ERROR"
    assert "ValueError: slot blew up" in message
    assert "Traceback" in message
