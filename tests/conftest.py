"""Shared fixtures.

launcher.pyw cannot be imported by name (the import system ignores .pyw), so
it is loaded from its path once per session.
"""

from __future__ import annotations

import importlib.util
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
