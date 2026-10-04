# 3. Each plugin declares its dependencies in its own `pyproject.toml`

Date: 2026-10-04. Status: accepted. Work package: #667. Builds on
[ADR 0002](0002-apps-live-in-plugins.md).

## Context

Every app now lives in `plugins/<app>/` with a `launcher-app.toml`, but its
dependencies were only recorded in the root `pyproject.toml`, mixed with the
launcher's and with nothing saying which app needs what. The next step (#666)
is for the launcher to install an app's dependencies when it finds the app's
manifest, so each app needs its own machine-readable list.

## Decision

- Every `plugins/<app>/` with a `launcher-app.toml` also has a `pyproject.toml`
  whose `[project]` table gives `name`, `version`, `requires-python` and
  `dependencies` (plus `optional-dependencies` where the app has extras). This
  is the list #666 reads.
- A plugin `pyproject.toml` is a **dependency declaration, not an installable
  package**. It has no `[build-system]`; the Macros code stays packaged by the
  root, as ADR 0002 decided.
- The root `pyproject.toml` stays the single source the hashed locks are
  compiled from (`tools/lock.py`), and keeps all tool config and the `dev`
  extra. Its `dependencies` are the launcher's own (PySide6) plus every
  plugin's runtime requirements, copied verbatim.
- `tests/test_plugin_projects.py` enforces this: each plugin has the file, each
  plugin requirement appears verbatim in the root, and each one has a hashed pin
  in `requirements.lock` (extras: `requirements-dev.lock`). Changing a range in
  one place without the other fails the suite.
- Recipe Calc declares `pillow` as a dependency, not an extra. It runs without
  Pillow by falling back to flat textures, but rendered icons are its normal
  experience.

## Options rejected

- **A lock file per plugin.** Rule 7 wants every dependency hash-pinned, and the
  root lock already does that. Separate locks would mean separate resolves that
  can disagree on a shared package (`pillow` is in both apps) installed into one
  environment. How #666 installs is #666's decision.
- **Move the runtime dependencies out of the root.** The first-start bootstrap,
  CI and `tools/tasks.py setup` all install from `requirements.lock`, compiled
  from the root. Emptying the root would break all three before #666 exists.
- **Make each plugin an installable package.** Recipe Calc is a single script,
  and the Macros package is rooted at `src/` for the reasons in ADR 0002.

## Consequences

- Adding or changing a dependency touches the plugin's `pyproject.toml` and the
  root's, then `python tools/lock.py`. The test catches a missed half.
- A new plugin without a `pyproject.toml` fails the suite.
