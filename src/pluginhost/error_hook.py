"""Make otherwise-uncaught exceptions visible in an app started without a console.

The launcher starts its apps with pythonw, where stderr is None: an exception
raised in a Qt slot or a worker thread would vanish without a trace. This
module logs it with its traceback and hands it to the app to show however it
shows things (a dialog, a log window).

Standard library only, like the rest of pluginhost: showing the error is the
app's business, so this module never imports a GUI toolkit.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import Callable
from types import TracebackType

#: Called with the exception and its formatted traceback, after it is logged.
Notify = Callable[[BaseException, str], None]


class ExceptionHook:
    """The hooks :func:`install_excepthook` installs; exposed for tests and reuse."""

    def __init__(self, notify: Notify, logger: logging.Logger) -> None:
        self._notify = notify
        self._logger = logger

    def report(
        self, exc_type: type[BaseException], value: BaseException, tb: TracebackType | None
    ) -> None:
        """``sys.excepthook``: log the exception with its traceback, then notify.

        A failing ``notify`` is logged and swallowed: the original error is
        already in the log, and raising here would replace it.
        """
        details = "".join(traceback.format_exception(exc_type, value, tb))
        self._logger.error("Unhandled exception\n%s", details)
        try:
            self._notify(value, details)
        except Exception:
            self._logger.exception("Could not report the unhandled exception")

    def report_thread(self, args: threading.ExceptHookArgs) -> None:
        """``threading.excepthook``: as :meth:`report`; a thread's SystemExit is ignored."""
        if args.exc_type is SystemExit or args.exc_value is None:
            return
        self.report(args.exc_type, args.exc_value, args.exc_traceback)


def install_excepthook(notify: Notify, logger: logging.Logger) -> ExceptionHook:
    """Route uncaught exceptions, on the main thread and on threading threads, to ``logger``
    and ``notify``.

    Replaces ``sys.excepthook`` and ``threading.excepthook`` for the rest of the
    process. Under Qt, exceptions raised in slots also arrive here.

    Args:
        notify: shows the error to the user. It runs on the thread that raised,
            so a GUI app must marshal it to its GUI thread (e.g. with a queued
            signal). Exceptions it raises are logged, never propagated.
        logger: receives the traceback at ERROR level.

    Returns:
        The installed hook.
    """
    hook = ExceptionHook(notify, logger)
    sys.excepthook = hook.report
    threading.excepthook = hook.report_thread
    return hook
