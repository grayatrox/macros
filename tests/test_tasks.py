"""Tests for the task runner's command table and exit-code handling."""

from __future__ import annotations

import sys

import pytest

from tools import tasks
from tools.tasks import Command


class Recorder:
    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.ran: list[Command] = []
        self._statuses = statuses or {}

    def __call__(self, command: Command) -> int:
        self.ran.append(command)
        return next((code for word, code in self._statuses.items() if word in command), 0)


@pytest.mark.parametrize("verb", tasks.VERBS)
def test_every_verb_runs_with_this_interpreter(verb: str) -> None:
    for command in tasks.commands(verb, [], {}):
        assert command[0] == sys.executable


def test_setup_installs_the_hashed_dev_lock() -> None:
    (command,) = tasks.commands("setup", [], {})
    assert command[1:4] == ("-m", "pip", "install")
    assert "--require-hashes" in command
    assert command[-2:] == ("-r", "requirements-dev.lock")


def test_lint_checks_both_rules_and_formatting() -> None:
    check, fmt = tasks.commands("lint", [], {})
    assert check[2:4] == ("ruff", "check")
    assert fmt[2:] == ("ruff", "format", "--check", ".")


def test_lint_uses_github_annotations_only_in_actions() -> None:
    (local, _fmt) = tasks.commands("lint", [], {})
    (ci, _fmt) = tasks.commands("lint", [], {"GITHUB_ACTIONS": "true"})
    assert "--output-format=github" not in local
    assert "--output-format=github" in ci


def test_run_passes_extra_arguments_to_the_app() -> None:
    (command,) = tasks.commands("run", ["--restarted"], {})
    assert command == (sys.executable, "Macros.py", "--restarted")


def test_unknown_verb_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        tasks.commands("deploy", [], {})


def test_main_runs_each_command_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # main() reads the real environment; on the Actions runner GITHUB_ACTIONS
    # would otherwise add --output-format=github and break the comparison.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    recorder = Recorder()
    assert tasks.main(["lint"], execute=recorder) == 0
    assert recorder.ran == list(tasks.commands("lint", [], {}))


def test_main_stops_at_first_failure_and_returns_its_code() -> None:
    recorder = Recorder({"check": 3})
    assert tasks.main(["lint"], execute=recorder) == 3
    assert len(recorder.ran) == 1  # format --check never ran


@pytest.mark.parametrize("argv", [[], ["deploy"]])
def test_main_rejects_missing_or_unknown_verb(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder()
    assert tasks.main(argv, execute=recorder) == 2
    assert recorder.ran == []
    assert "usage:" in capsys.readouterr().err
