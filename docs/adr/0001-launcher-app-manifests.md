# 1. Apps declare themselves to the launcher with `launcher-app.toml`

Date: 2026-10-04. Status: accepted; where to search superseded by
[ADR 0002](0002-apps-live-in-plugins.md). Work package: #659.

> Since ADR 0002 the launcher no longer searches recursively. It reads only
> `<folder>/*/launcher-app.toml`, and the apps live in `plugins/<app>/`. The
> manifest format and its rules below still apply.
>
> Note, 2026-10-04 (#674): the examples below use the names from when this was
> written, `Recipe Calc/Recpe_Calc.py`. That app now lives in `plugins/recipe_calc/`
> and its entry point is `Recipe_Calc.py` (see ADR 0002 and
> [ADR 0006](0006-plugins-are-self-contained.md)).

## Context

`launcher.pyw` listed every `*.py` / `*.pyw` file under the chosen folder. In
this repo that put package modules (`src/macros/*.py`), the task runner
(`tools/tasks.py`) and the tests (`tests/test_*.py`) in the list beside the real
apps. Real apps also live in subfolders (`Recipe Calc/Recpe_Calc.py`), so
discovery has to stay recursive. Skipping subfolders is not an option.

## Decision

An app is a script declared by a file named `launcher-app.toml` in its folder:

```toml
script = "Recpe_Calc.py"
```

- The launcher searches the chosen folder and all of its subfolders for
  `launcher-app.toml`, skipping `SKIP_DIRS` (`.venv`, `__pycache__`, `.git`, ...).
  It lists only the scripts those files declare. A `.py` file that no manifest
  declares is not an app.
- `script` must be a relative path to an existing `.py` or `.pyw` file inside
  the manifest's own folder (subfolders allowed). Absolute paths, a leading `/`,
  and anything that resolves outside the folder, through `..` or a symlink, are
  rejected. A manifest can only vouch for its own app.
- A bad manifest (invalid TOML, missing or wrong `script`, missing file) is
  reported in a warning dialog naming the file and the reason. The valid apps
  are still listed.
- The launcher never lists itself, even if a manifest names it.

## Options rejected

- **Add `src`, `tools`, `tests` to `SKIP_DIRS`.** This fixes this repo only. It
  still guesses from file extensions, so every other helper module and one-off
  script stays listed.
- **Read `pyproject.toml` `[project.scripts]` / `[project.gui-scripts]`.**
  These give `module:function` entry points that exist only once the package is
  installed, not a file to run. Many apps (Recipe Calc) have no
  `pyproject.toml`. A `pyproject.toml` also describes libraries that have no
  app.
- **Heuristic: files with an `if __name__ == "__main__":` guard.** No setup is
  needed, but tools and tests often have the guard too, so the list still has
  false positives. Parsing every file on each scan is also slower.
- **A fixed `plugins` folder.** Restricting apps to one folder would add
  safety, but apps live where their projects live. Opt-in manifests and the
  inside-its-own-folder rule already stop the launcher listing anything nobody
  declared. The folder box still decides where to look.

## Consequences

- Every app needs a two-line manifest. This repo ships them for `Macros.py` and
  `Recipe Calc/Recpe_Calc.py`.
- The manifest is TOML, so later keys can be added without breaking existing
  files (for example a display name or a default launch mode).
