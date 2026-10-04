# 5. The root `pyproject.toml` declares only the launcher

Date: 2026-10-04. Status: accepted. Work package: #668. Supersedes the root
mirroring in [ADR 0003](0003-per-plugin-pyproject.md) and updates where
[ADR 0004](0004-plugin-dependency-install.md)'s lock is compiled from.

## Context

Chris asked for the root manifest to describe only the root launcher app, with
each plugin declaring its own dependencies. The package names the root
`launcher-app.toml`, but there has been no root `launcher-app.toml` since #661
moved it to `plugins/macros/`. The root file that described the plugins was
`pyproject.toml`. Its `[project]` was named and described as the Macros app. It
repeated every plugin's runtime requirements word for word (ADR 0003), and it
carried Macros' `legacy-vote` extra.

That mirroring existed only because `tools/lock.py` compiled the hashed locks
from the root alone. Constraints that still hold:

- Rule 7 and rule 45: every runtime install is pinned and hash-checked.
- ADR 0004 installs the whole `requirements.lock` into one shared folder, so
  every app's pins must come from one resolve.

## Decision

- The root `pyproject.toml` is the launcher's: `name = "launcher"`, and its
  runtime `dependencies` are what `launcher.pyw` itself imports
  (`PySide6-Essentials`). It keeps the `dev` extra and all tool config (ruff,
  mypy, pytest), which cover the whole repository.
- A plugin's requirements are declared only in `plugins/<app>/pyproject.toml`,
  extras included.
- `tools/lock.py` compiles `requirements.lock` and `requirements-dev.lock` from
  the root `pyproject.toml` plus every `plugins/<app>/pyproject.toml` next to a
  `launcher-app.toml`, in one `uv pip compile` call each. A plugin with a manifest
  but no `pyproject.toml` stops the lock with an error rather than being skipped.
- The install path from ADR 0004 is unchanged. It still installs the one
  hash-pinned `requirements.lock`, which still covers every plugin.
- `tests/test_plugin_projects.py` now checks four things. The root declares
  only the launcher. Both locks name each plugin's `pyproject.toml` as a source.
  Every plugin requirement has a hashed pin in `requirements.lock`. Every plugin
  extra has a hashed pin in `requirements-dev.lock`.

## Options rejected

- **A hash-pinned lock per plugin, installed into a folder per plugin.** This
  would also have emptied the root, but at a cost. Separate resolves can pin a
  shared package (`pillow`, `PySide6-Essentials`) differently. Avoiding that
  needs a combined resolve as a constraint, which is this decision plus one file
  per plugin. ADR 0004's install, the first-start bootstrap, and the launcher's
  child `PYTHONPATH` would all have had to become per-app as well. None of that
  is needed to stop the root describing the plugins.
- **Keep mirroring in the root, only renaming the project.** The root would
  still describe the plugins, which is what the package asks to remove.
- **A separate `.in` file listing the plugin requirements.** That is the same
  mirroring in a different file.

## Consequences

- Adding or changing an app's dependency touches only that app's
  `pyproject.toml`, followed by `python tools/lock.py`. If the lock is not
  regenerated, `test_plugin_requirements_are_hash_pinned` fails for a new
  requirement. A changed range is not caught by a test, as before: the lock
  keeps the old pin until it is regenerated.
- `requirements.lock` is still the union of every app's needs, so the first-start
  install of the launcher or of Macros still installs all of them. Narrowing
  that would mean the per-plugin option above.
- The locks' `# via` comments now name the plugin `pyproject.toml` each pin
  comes from.
