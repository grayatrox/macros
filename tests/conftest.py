"""Shared fixtures.

launcher.pyw cannot be imported by name (the import system ignores .pyw), so
it is loaded from its path once per session.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from collections.abc import Iterator
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def launcher() -> ModuleType:
    path = ROOT / "launcher.pyw"
    loader = SourceFileLoader("launcher", str(path))
    spec = importlib.util.spec_from_loader("launcher", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def restore_excepthooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put ``sys.excepthook`` and ``threading.excepthook`` back after the test.

    For tests that install an error-reporting hook: a hook left in place
    outlives the test and makes later tests depend on run order.
    """
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)


@pytest.fixture(autouse=True)
def _no_leaked_excepthooks() -> Iterator[None]:
    """Fail any test that leaves either excepthook replaced (rule 24)."""
    sys_hook, thread_hook = sys.excepthook, threading.excepthook
    yield
    leaked = [
        name
        for name, before, after in (
            ("sys.excepthook", sys_hook, sys.excepthook),
            ("threading.excepthook", thread_hook, threading.excepthook),
        )
        if before is not after
    ]
    sys.excepthook, threading.excepthook = sys_hook, thread_hook
    assert not leaked, f"test leaked a replaced hook: {', '.join(leaked)}"
