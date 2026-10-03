"""The project's task runner: one entry point for humans, CI and agents.

    python tools/tasks.py <verb> [args...]

Verbs: ``setup``, ``fmt``, ``lint``, ``typecheck``, ``test``, ``run``. Each is a
fixed list of commands, run in order with the current interpreter; the first
failure stops the verb and becomes its exit code.

A plain stdlib script rather than ``make`` or ``just``: this is a Windows
project, ``make`` is not on a stock machine, and ``just`` would be one more
thing to install before anything else works - including on the CI runner.

``README.md`` and ``.github/workflows/ci.yml`` both call these verbs, so the
commands live here and nowhere else.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

Command = tuple[str, ...]

VERBS = ("setup", "fmt", "lint", "typecheck", "test", "run")


def _py(*args: str) -> Command:
    return (sys.executable, "-m", *args)


def commands(verb: str, extra: Sequence[str], env: Mapping[str, str]) -> tuple[Command, ...]:
    """The commands ``verb`` runs, in order.

    ``extra`` is passed through to the app by ``run`` and ignored elsewhere.
    ``env`` decides CI-only output formatting, so the table stays a pure function.

    Raises:
        KeyError: ``verb`` is not a known verb.
    """
    lint_format = ("--output-format=github",) if env.get("GITHUB_ACTIONS") == "true" else ()
    table: dict[str, tuple[Command, ...]] = {
        # Hashed install: a download that does not match the committed digest
        # is refused. Run inside the project's virtualenv.
        "setup": (
            _py(
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "-r",
                "requirements-dev.lock",
            ),
        ),
        "fmt": (_py("ruff", "format", "."),),
        "lint": (
            _py("ruff", "check", *lint_format, "."),
            _py("ruff", "format", "--check", "."),
        ),
        "typecheck": (_py("mypy"),),
        "test": (_py("pytest"),),
        "run": ((sys.executable, "Macros.py", *extra),),
    }
    return table[verb]


def main(
    argv: Sequence[str] | None = None,
    *,
    execute: Callable[[Command], int] | None = None,
) -> int:
    """Run one verb. Returns the exit code of the first failing command, or 0.

    ``execute`` runs one command from the repo root; tests replace it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in VERBS:
        print(f"usage: python tools/tasks.py {{{','.join(VERBS)}}} [args...]", file=sys.stderr)
        return 2

    run_one = execute or _execute
    for command in commands(args[0], args[1:], os.environ):
        if (status := run_one(command)) != 0:
            return status
    return 0


def _execute(command: Command) -> int:
    print("+ " + " ".join(command), flush=True)
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
