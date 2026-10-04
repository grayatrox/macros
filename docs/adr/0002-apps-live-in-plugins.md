# 2. Apps live in `plugins/<app>/`, and the launcher looks one level down

Date: 2026-10-04. Status: accepted; keeping the Macros code in `src/macros/` is
superseded by [ADR 0006](0006-plugins-are-self-contained.md). Work package: #661.
Supersedes the recursive-search part of [ADR 0001](0001-launcher-app-manifests.md).

> Note, 2026-10-04 (#674): the entry point named below as `Recpe_Calc.py` is now
> `Recipe_Calc.py`, a thin shim over the `recipe_calc` package in
> `plugins/recipe_calc/src/` (see [ADR 0006](0006-plugins-are-self-contained.md)).

## Context

ADR 0001 made apps opt in with a `launcher-app.toml`, but the launcher still
searched the chosen folder and every subfolder for one, pruning noise folders
with a `SKIP_DIRS` name list. The apps sat wherever their files happened to be:
`Macros.py` at the repository root, Recipe Calc in `Recipe Calc/`. Every scan
walked the whole tree, `.venv` included unless the name list caught it, and the
name list matched those names anywhere in the path, including above the chosen
folder.

Chris asked for a formal structure: each app in its own folder under one
top-level `plugins/` folder, and a scan that only looks there.

## Decision

- Every launchable app lives in `plugins/<app>/`, with its `launcher-app.toml`
  beside it. This repo has `plugins/macros/` (the `Macros.py` shim) and
  `plugins/recipe_calc/` (`Recpe_Calc.py`).
- `find_apps(folder)` reads only `folder/*/launcher-app.toml`. A manifest in the
  folder itself, or nested deeper than one level, is not listed. Nothing below
  an app's folder is walked, so `SKIP_DIRS` is gone.
- The launcher's default folder is the `plugins/` folder beside `launcher.pyw`.
  The folder box can still point it at any other folder laid out the same way.
- The manifest rules from ADR 0001 are unchanged. `script` may still name a file
  in a subfolder of the app's folder.
- `launcher.pyw` is the host, not a plugin. It stays at the repository root with
  the rest of the infrastructure (`pyproject.toml`, the locks, `tools/`, `tests/`,
  `docs/`, `.github/`).
- **The Macros code stays in `src/macros/`.** Only the two-line entry shim and
  its manifest moved. Packaging (`pyproject.toml`), mypy's `mypy_path`, pytest's
  `pythonpath`, the first-start install's `requirements*.lock` lookup, and
  `settings.json` all resolve from the repository root. The shim reaches `src/`
  with `parents[2]`, and the app's Reload restarts through
  `plugins/macros/Macros.py`.

## Options rejected

- **Keep the recursive search and only move the files.** The structure would
  exist by convention only, and every scan would still walk the whole tree.
- **Move `src/macros` into `plugins/macros/` too.** The package, its locks, its
  settings file and its tooling config are repository-level. Moving it would
  re-point every one of them, and the plugin folder would then hold a second
  project root. It adds churn and buys nothing the launcher needs.
- **Recursive search limited to `plugins/`.** This is still a walk, and it
  allows apps nested at any depth, which is the lack of structure this change
  removes.

## Consequences

- A new app means a new folder in `plugins/` with a manifest. A manifest
  anywhere else is ignored.
- A launcher whose saved folder (`~/.python_launcher_settings.json`) is the
  repository root now lists nothing there. Pick `plugins/` once and it is
  remembered.
