# 4. The launcher installs each app's dependencies from the hashed lock

Date: 2026-10-04. Status: accepted; where the lock is compiled from is updated by
[ADR 0005](0005-root-declares-only-the-launcher.md), and the modules named here
moved from `src/macros/` to `src/pluginhost/` in
[ADR 0006](0006-plugins-are-self-contained.md). Work package: #666. Builds on
[ADR 0003](0003-per-plugin-pyproject.md).

## Context

Since ADR 0003 every app in `plugins/<app>/` declares its runtime dependencies
in its own `pyproject.toml`. Chris asked for those to be installed automatically
when the launcher finds the app's `launcher-app.toml`.

Constraints:

- Rule 7 and rule 45: installs stay pinned and hash-verified. The only hashed
  pins are in `requirements.lock`, compiled from the root `pyproject.toml`,
  which already repeats every plugin requirement (enforced by
  `tests/test_plugin_projects.py`).
- A scan must not reinstall anything, or even run pip, when the environment
  already has what the apps need.
- pip takes minutes on a first install, and the launcher's GUI must stay live.
- An app the launcher starts runs in a new process with the same interpreter.
  It sees the interpreter's own packages, not the private folder the first-start
  install (`src/macros/bootstrap.py`) adds to the launcher's own path. Under a
  bare Python, Recipe Calc therefore ran without Pillow even though the launcher
  had installed it.

## Decision

- **When: on detection.** After every scan, `launcher.pyw` checks each listed
  app (`src/macros/plugin_deps.py`). The check reads the app's
  `[project].dependencies`, the pins in `requirements.lock`, and installed
  package metadata (`importlib.metadata`). No pip, no network, so a scan of a
  ready environment does nothing more.
- **Satisfied = the locked version is present.** A requirement is satisfied
  when the exact version `requirements.lock` pins for it is visible on the path
  the app will get. The lock is what gets installed, so the lock is what is
  checked; version ranges are not evaluated. Environment markers are: a requirement whose
  marker excludes this machine is skipped (OP #673). The dev
  lock is constrained to the same runtime pins, so a dev virtualenv counts as
  satisfied.
- **What gets installed: the whole runtime lock, into the first-start folder.**
  If any listed app is missing a pinned requirement, `%LOCALAPPDATA%\Macros\deps-pyXY`
  is made current with `requirements.lock` through `bootstrap.install_lock`.
  It is the same hash-checked, staged install as the first start, keyed on
  the lock's digest, so a folder that is already current is reused without
  running pip. One install runs at a time, on a worker thread. Its result
  reaches the GUI thread through a Qt signal. An app waiting on it shows
  "installing..." and cannot be launched until it finishes.
- **How the app sees it: `PYTHONPATH`.** Once the launcher uses that folder
  (the first start put it on the launcher's path, or an install has finished),
  every app it starts gets the folder at the front of `PYTHONPATH`. Macros
  still runs its own first-start check, which finds the same folder current
  and adds it with `site.addsitedir` (pywin32 needs its `.pth` processing).
- **A requirement the lock does not pin is never installed.** The scan's
  warning names the app and the requirement and says to add it to the root
  `pyproject.toml` and run `python tools/lock.py`. The app is still listed.
- **Failures are shown, not swallowed.** A pip failure (with pip's last lines),
  an unreadable lock or `pyproject.toml`, or a requirement still missing after
  the install each produce a dialog. A folder that the launcher process
  already has loaded is never swapped for a newer lock, because its compiled
  modules cannot be replaced while loaded. The dialog says to restart the
  launcher, whose own first-start check then updates it.
- **Only `dependencies`.** Optional extras (Macros' `legacy-vote`) are not
  installed automatically.

## Options rejected

- **Install on first launch of the app instead of on detection.** The
  package asks for detection. Installing at launch would also put a
  minutes-long wait between the click and the app starting.
- **Install only the app's own requirements.** `--require-hashes` needs every
  transitive dependency pinned and hashed in the same requirements file. A
  per-app subset needs either a per-app lock (rejected in ADR 0003: separate
  resolves can disagree on a shared package) or reconstructing the dependency
  graph from the lock's `# via` comments. The whole lock is one resolve that
  already covers every app.
- **Install the missing pins with `--no-deps`.** Hash-checked, but it skips
  transitive dependencies, so the app can still fail to import.
- **Install into the launcher's own interpreter or virtualenv.** The first-start
  install deliberately never modifies the user's Python. A developer's
  virtualenv is managed by `tools/tasks.py setup`.
- **Install unpinned requirements with plain `pip install`.** Unverified
  downloads (rule 45).
- **Re-run the Tk progress window from `bootstrap.py`.** It runs its own Tk
  main loop and would block the Qt GUI. The launcher reports progress in its
  own status bar instead.

## Consequences

- A launcher started from a bare Python now gives its apps the dependencies it
  installed. Recipe Calc gets its rendered icons there for the first time.
- While the folder is in use, it comes first on a launched app's path,
  ahead of the interpreter's own packages. Apps therefore run against the
  locked versions.
- An app from outside this repository is checked against this repository's
  lock. Its requirements must be pinned here to be installed.
- Closing the launcher during an install ends the worker thread. The next
  install discards the half-finished staging folder and starts again.
