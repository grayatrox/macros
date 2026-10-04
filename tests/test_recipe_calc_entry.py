"""Recipe Calc's entry point runs the first-start install before it needs PySide6 (OP #677).

A bare Python has no PySide6. That is simulated here by making it unimportable
for the length of a test; the install itself is covered by test_bootstrap.py.
"""

from __future__ import annotations

import ctypes
import importlib
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from types import ModuleType

import pytest

from pluginhost.bootstrap import BootstrapError


class _NoPySide6:
    """A meta-path finder that makes PySide6 look absent, as on a bare Python."""

    def find_spec(
        self, name: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if name == "PySide6" or name.startswith("PySide6."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


@pytest.fixture
def entry_without_pyside6(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """``recipe_calc.__main__``, imported fresh while PySide6 cannot be imported."""
    for name in list(sys.modules):
        if name.split(".")[0] in {"PySide6", "recipe_calc"}:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_NoPySide6(), *sys.meta_path])
    return importlib.import_module("recipe_calc.__main__")


def test_the_entry_point_imports_without_pyside6(entry_without_pyside6: ModuleType) -> None:
    assert "PySide6" not in sys.modules


def test_a_bare_python_gets_the_first_start_install_under_recipe_calcs_name(
    entry_without_pyside6: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    shown: list[tuple[str, str]] = []

    def stop(*, app_name: str, required_modules: tuple[str, ...]) -> None:
        calls.append((app_name, tuple(required_modules)))
        raise BootstrapError("no network")

    def message_box(_hwnd: int, text: str, title: str, _flags: int) -> int:
        shown.append((title, text))
        return 0

    monkeypatch.setattr(entry_without_pyside6, "ensure_dependencies", stop)
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", message_box)

    assert entry_without_pyside6.main() == 1
    # Its own modules, not Macros' (#689).
    assert calls == [("Recipe Calc", ("PySide6", "PIL"))]
    assert shown == [("Recipe Calc - cannot start", "no network")]
    assert "PySide6" not in sys.modules
