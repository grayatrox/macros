"""Make each launcher app's declared dependencies available to it (OP #666).

An app in ``plugins/<app>/`` lists what it needs in its own ``pyproject.toml``
(``[project].dependencies``, ADR 0003). When launcher.pyw finds the app, it asks
:meth:`PluginDeps.status` whether those are present. The check reads files and
package metadata only, with no pip and no network, so a scan in a ready
environment costs nothing. If something is missing, :meth:`PluginDeps.ensure_installed`
runs off the GUI thread. The full reasoning is in
``docs/adr/0004-plugin-dependency-install.md``. In short:

* **Environment markers** (``; sys_platform == "win32"``) are evaluated against
  the running interpreter by :func:`applies`; a requirement whose marker does
  not match this machine is skipped entirely (OP #673).
* **Satisfied** means the exact version pinned in ``requirements.lock`` is
  visible on the path the launched app will get. Version ranges are not
  evaluated; the lock is what gets installed, so the lock is what is checked.
* **Installed from the lock only.** A missing requirement is installed by
  making the first-start install folder (:func:`pluginhost.bootstrap.deps_dir`)
  current with the whole hash-pinned runtime lock, which covers every
  plugin's requirements. A requirement the lock does not pin is reported in
  :attr:`DepsStatus.unpinned` and never installed.
* **Visible to the app** through ``PYTHONPATH`` (:meth:`PluginDeps.child_env`)
  once the launcher uses that folder.

Imports only the standard library; it runs in the launcher before any app does.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import operator
import os
import platform
import re
import sys
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from pluginhost.bootstrap import (
    LOCK_FILE,
    BootstrapError,
    Installer,
    VersionOf,
    deps_dir,
    install_lock,
    is_current,
    pip_install,
)

PYPROJECT_NAME = "pyproject.toml"


class PluginDepsError(Exception):
    """An app's dependencies could not be checked or installed; the message says why."""


class DepsStatus(NamedTuple):
    """What an app still needs, by kind."""

    #: ``name==version`` pins from the lock that are not present at that version.
    missing: list[str]
    #: Requirements, as written, that the lock does not pin. Never installed.
    unpinned: list[str]


def normalise(name: str) -> str:
    """PEP 503-normalised distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    """Normalised distribution name of a PEP 508 requirement string.

    Extras, version ranges and environment markers are ignored here: only the
    name is needed to find the requirement's pin in the lock.

    Raises:
        PluginDepsError: the string does not start with a distribution name.
    """
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not match:
        raise PluginDepsError(f"{requirement!r} is not a requirement.")
    return normalise(match.group(1))


#: PEP 508 environment: marker variable name to its value on this machine.
Environment = Mapping[str, str]

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>"[^"]*"|'[^']*')
      | (?P<op>===|==|!=|~=|<=|>=|<|>)
      | (?P<paren>[()])
      | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    )""",
    re.VERBOSE,
)
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "===": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
#: ``~=`` needs at least ``X.Y``: it pins everything but the last segment.
_COMPATIBLE_MIN_SEGMENTS = 2
_VERSION = re.compile(r"\d+(?:\.\d+)*")
_VARIABLES = frozenset(
    {
        "os_name",
        "sys_platform",
        "platform_machine",
        "platform_python_implementation",
        "platform_release",
        "platform_system",
        "platform_version",
        "python_version",
        "python_full_version",
        "implementation_name",
        "implementation_version",
        "extra",
    }
)


def current_environment() -> dict[str, str]:
    """The PEP 508 marker variables for the running interpreter (stdlib only)."""
    info = sys.implementation.version
    implementation_version = f"{info.major}.{info.minor}.{info.micro}"
    if info.releaselevel != "final":
        implementation_version += f"{info.releaselevel[0]}{info.serial}"
    return {
        "os_name": os.name,
        "sys_platform": sys.platform,
        "platform_machine": platform.machine(),
        "platform_python_implementation": platform.python_implementation(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "python_full_version": platform.python_version(),
        "implementation_name": sys.implementation.name,
        "implementation_version": implementation_version,
        "extra": "",
    }


def _release(text: str) -> tuple[int, ...] | None:
    """Numeric release segments of a plain version like ``3.12.4``, else None."""
    return tuple(int(part) for part in text.split(".")) if _VERSION.fullmatch(text) else None


def _padded(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)), b + (0,) * (size - len(b))


def _compare(left: str, op: str, right: str) -> bool:
    """One marker comparison: by version when both sides are versions, else as text."""
    if op == "in":
        return left in right
    if op == "not in":
        return left not in right
    prefix = right.endswith(".*") and op in ("==", "!=")
    a = _release(left)
    b = _release(right[:-2] if prefix else right)
    if a is None or b is None:
        # PEP 508 falls back to string comparison when a side is not a version.
        return _OPERATORS[op](left, right)
    if prefix:
        return (a[: len(b)] == b) == (op == "==")
    if op == "~=":
        if len(b) < _COMPATIBLE_MIN_SEGMENTS:
            raise PluginDepsError(f"Marker operator ~= needs a release like 3.10, not {right!r}.")
        low, high = _padded(a, b)
        return low >= high and a[: len(b) - 1] == b[:-1]
    return _OPERATORS[op](*_padded(a, b))


class _Marker:
    """Recursive-descent evaluator for a PEP 508 marker expression."""

    def __init__(self, text: str, environment: Environment) -> None:
        self.text = text
        self.environment = environment
        self.tokens: list[tuple[str, str]] = []
        position = 0
        while text[position:].strip():
            match = _TOKEN.match(text, position)
            if not match or not match.lastgroup:
                raise PluginDepsError(f"Cannot read the marker {text!r} near {text[position:]!r}.")
            self.tokens.append((match.lastgroup, match.group(match.lastgroup)))
            position = match.end()
        self.index = 0

    def evaluate(self) -> bool:
        result = self._or()
        if self.index != len(self.tokens):
            raise PluginDepsError(
                f"Unexpected {self.tokens[self.index][1]!r} in marker {self.text!r}."
            )
        return result

    def _peek(self) -> tuple[str, str]:
        return self.tokens[self.index] if self.index < len(self.tokens) else ("", "")

    def _next(self) -> tuple[str, str]:
        token = self._peek()
        if not token[0]:
            raise PluginDepsError(f"The marker {self.text!r} ends unexpectedly.")
        self.index += 1
        return token

    def _or(self) -> bool:
        result = self._and()
        while self._peek() == ("word", "or"):
            self.index += 1
            result = self._and() or result  # parse the right side even when decided
        return result

    def _and(self) -> bool:
        result = self._atom()
        while self._peek() == ("word", "and"):
            self.index += 1
            result = self._atom() and result
        return result

    def _atom(self) -> bool:
        if self._peek() == ("paren", "("):
            self.index += 1
            result = self._or()
            if self._next() != ("paren", ")"):
                raise PluginDepsError(f"Expected ')' in marker {self.text!r}.")
            return result
        left = self._operand()
        kind, value = self._next()
        if kind == "op":
            op = value
        elif (kind, value) == ("word", "in"):
            op = "in"
        elif (kind, value) == ("word", "not") and self._next() == ("word", "in"):
            op = "not in"
        else:
            raise PluginDepsError(f"Expected a comparison, got {value!r}, in marker {self.text!r}.")
        return _compare(left, op, self._operand())

    def _operand(self) -> str:
        kind, value = self._next()
        if kind == "string":
            return value[1:-1]
        if kind == "word" and value in _VARIABLES:
            return self.environment.get(value, "")
        raise PluginDepsError(f"{value!r} is not a marker variable or string in {self.text!r}.")


def applies(requirement: str, environment: Environment | None = None) -> bool:
    """Whether a PEP 508 requirement's environment marker matches ``environment``.

    A requirement with no marker always applies. ``environment`` defaults to
    :func:`current_environment`.

    Raises:
        PluginDepsError: the marker is malformed.
    """
    _, separator, marker = requirement.partition(";")
    if not separator or not marker.strip():
        return True
    environment = environment if environment is not None else current_environment()
    return _Marker(marker, environment).evaluate()


def read_pins(lock: Path) -> dict[str, str]:
    """Every ``name==version`` pin in a hashed lock file, by normalised name.

    Raises:
        PluginDepsError: the lock cannot be read.
    """
    try:
        text = lock.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginDepsError(f"The dependency lock file could not be read: {exc}") from exc
    pins = re.findall(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)", text, re.MULTILINE)
    return {normalise(name): version for name, version in pins}


def read_requirements(app_dir: Path) -> list[str]:
    """The ``[project].dependencies`` declared by ``app_dir/pyproject.toml``.

    An app folder with no ``pyproject.toml`` declares nothing, so needs nothing
    installed. Optional extras are never installed automatically.

    Raises:
        PluginDepsError: the file cannot be read, is not TOML, or its
            dependencies are not a list of strings.
    """
    pyproject = app_dir / PYPROJECT_NAME
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PluginDepsError(f"{pyproject} could not be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PluginDepsError(f"{pyproject} is not valid TOML: {exc}") from exc

    project = data.get("project", {})
    requirements = project.get("dependencies", []) if isinstance(project, dict) else None
    if not isinstance(requirements, list) or not all(isinstance(r, str) for r in requirements):
        raise PluginDepsError(f"{pyproject}: [project].dependencies must be a list of strings.")
    return requirements


def check(
    requirements: Iterable[str],
    pins: Mapping[str, str],
    version_of: VersionOf,
    environment: Environment | None = None,
) -> DepsStatus:
    """Sort ``requirements`` into those satisfied, missing, and not pinned at all.

    A requirement whose environment marker does not match ``environment``
    (default: this interpreter) is skipped: it is in none of the lists.

    Raises:
        PluginDepsError: a requirement has no distribution name or a bad marker.
    """
    environment = environment if environment is not None else current_environment()
    missing: list[str] = []
    unpinned: list[str] = []
    for requirement in requirements:
        if not applies(requirement, environment):
            continue
        name = requirement_name(requirement)
        pin = pins.get(name)
        if pin is None:
            unpinned.append(requirement.strip())
        elif version_of(name) != pin:
            missing.append(f"{name}=={pin}")
    return DepsStatus(missing, unpinned)


def version_on(path: Sequence[str]) -> VersionOf:
    """Look up installed versions on ``path``, first entry winning, as imports do."""
    search = list(path)

    def version_of(name: str) -> str | None:
        for dist in importlib.metadata.distributions(name=name, path=search):
            return dist.version
        return None

    return version_of


@dataclass(frozen=True)
class PluginDeps:
    """Where apps' dependencies are pinned and installed; tests replace ``install``.

    ``active`` in the methods below means the launcher hands ``target`` to the
    apps it starts: it was already on this process's path at startup (the
    first-start install put it there), or an install has since completed.
    """

    lock: Path = LOCK_FILE
    target: Path = field(default_factory=deps_dir)
    install: Installer = pip_install

    def active_at_start(self) -> bool:
        """Whether this process already imports from ``target``."""
        target = self.target.resolve()
        return any(entry and Path(entry).resolve() == target for entry in sys.path)

    def search_path(self, *, active: bool) -> list[str]:
        """Where an app the launcher starts looks for packages: ``target`` first, if active."""
        return [str(self.target), *sys.path] if active else list(sys.path)

    def status(self, app_dir: Path, *, active: bool) -> DepsStatus:
        """What the app in ``app_dir`` declares but would not find. No pip, no network.

        Raises:
            PluginDepsError: the app's pyproject.toml or the lock is unusable.
        """
        # Directory listings are cached; an install since the last check must be seen.
        importlib.invalidate_caches()
        return check(
            read_requirements(app_dir),
            read_pins(self.lock),
            version_on(self.search_path(active=active)),
        )

    def ensure_installed(self, *, active: bool) -> bool:
        """Make ``target`` hold the whole hash-pinned lock. Slow: keep it off the GUI thread.

        Returns whether pip had to run; it does not when ``target`` already
        holds this lock.

        Raises:
            PluginDepsError: the install failed (pip's reason included), or
                ``target`` is out of date while this process has it loaded.
                Its compiled modules cannot be replaced then.
        """
        try:
            if active and not is_current(self.lock, self.target):
                raise PluginDepsError(
                    f"{self.lock.name} has changed since the launcher started, and the "
                    f"components in {self.target} are in use. Restart the launcher to "
                    "update them."
                )
            return install_lock(self.lock, self.target, self.install)
        except BootstrapError as exc:
            raise PluginDepsError(str(exc)) from exc

    def child_env(self, *, active: bool, environ: Mapping[str, str]) -> dict[str, str] | None:
        """Environment for a launched app: ``target`` first on PYTHONPATH, or None to inherit."""
        if not active:
            return None
        existing = environ.get("PYTHONPATH")
        entries = [str(self.target), *([existing] if existing else [])]
        return {**environ, "PYTHONPATH": os.pathsep.join(entries)}
