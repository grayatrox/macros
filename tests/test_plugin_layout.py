"""Each plugin's code lives in its own folder, and the launcher uses none of it (OP #670).

See docs/adr/0006-plugins-are-self-contained.md. A plugin may use the
launcher's shared package (``src/pluginhost``: the first-start install and the
dependency check), but nothing else outside ``plugins/<app>/``. The launcher
may not import from any plugin.

Imports are followed transitively by reading each module's source, so a
module only imported inside a function (after the first-start install, say)
still counts.
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
HOST_SHARED = ROOT / "src" / "pluginhost"


def _plugin_dirs() -> list[Path]:
    return sorted(manifest.parent for manifest in PLUGINS.glob("*/launcher-app.toml"))


def _entry_script(plugin: Path) -> Path:
    manifest = tomllib.loads((plugin / "launcher-app.toml").read_text(encoding="utf-8"))
    script = manifest["script"]
    assert isinstance(script, str), plugin
    return plugin / script


def _imported_names(source: Path) -> Iterator[str]:
    """Every absolute module name ``source`` imports, ``from x import y`` as ``x`` and ``x.y``."""
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _origin(name: str) -> Path | None:
    """Where ``name`` would be imported from, or None for attributes and absent modules."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):  # "x.y" where x is a module, not a package
        return None
    if spec is None or not spec.origin or spec.origin in {"built-in", "frozen"}:
        return None
    return Path(spec.origin).resolve()


def _is_first_party(path: Path) -> bool:
    return path.is_relative_to(ROOT) and not {".venv", "site-packages"} & set(path.parts)


def first_party_modules(script: Path) -> dict[str, Path]:
    """The repository's own modules ``script`` imports, directly or not: name -> file."""
    found: dict[str, Path] = {}
    pending = [script]
    while pending:
        for name in _imported_names(pending.pop()):
            origin = _origin(name)
            if name in found or origin is None or not _is_first_party(origin):
                continue
            found[name] = origin
            pending.append(origin)
    return found


@pytest.mark.parametrize("plugin", _plugin_dirs(), ids=lambda p: p.name)
def test_a_plugins_code_lives_in_its_own_folder(plugin: Path) -> None:
    modules = first_party_modules(_entry_script(plugin))

    outside = {
        name: path.relative_to(ROOT).as_posix()
        for name, path in modules.items()
        if not (path.is_relative_to(plugin) or path.is_relative_to(HOST_SHARED))
    }
    assert outside == {}


def test_the_launcher_uses_no_plugins_code() -> None:
    modules = first_party_modules(ROOT / "launcher.pyw")

    assert modules, "the walk found none of the launcher's own modules"
    inside_plugins = [name for name, path in modules.items() if path.is_relative_to(PLUGINS)]
    assert inside_plugins == []


def test_the_walk_sees_imports_made_inside_functions(tmp_path: Path) -> None:
    script = tmp_path / "entry.py"
    script.write_text("def main():\n    from tools import tasks\n", encoding="utf-8")

    # tools/ is a namespace package: no file of its own, so only the module counts.
    assert first_party_modules(script) == {"tools.tasks": (ROOT / "tools" / "tasks.py").resolve()}
