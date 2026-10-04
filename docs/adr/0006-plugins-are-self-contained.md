# 6. A plugin's code lives in its plugin folder; the launcher owns what it shares

Date: 2026-10-04. Status: accepted. Work package: #670. Supersedes the
"Macros code stays in `src/macros/`" decision of
[ADR 0002](0002-apps-live-in-plugins.md), and the "not an installable package"
bullet of [ADR 0003](0003-per-plugin-pyproject.md).

## Context

ADR 0002 moved each app's entry script into `plugins/<app>/` but left the
Macros code in the repository-root `src/macros/`. Chris reported the result:
Macros was separated from its source. `plugins/macros/` held only a shim, a
manifest and a dependency list, so a plugin could not be read, moved or
reasoned about on its own.

`src/macros/` also held two modules that are not Macros at all:
`bootstrap.py` (the first-start install of the hash-pinned lock, #663) and
`plugin_deps.py` (the launcher's check and install of an app's declared
dependencies, #666). `launcher.pyw` imported both, so the host depended on a
plugin's package.

## Decision

The layout every plugin follows:

```
launcher.pyw                  the host
src/pluginhost/               owned by the launcher; shared with its apps
    bootstrap.py              first-start install of requirements.lock
    plugin_deps.py            an app's [project].dependencies, checked and installed
plugins/<app>/
    launcher-app.toml         lists the app (ADR 0001)
    <Entry>.py                the script the manifest names
    pyproject.toml            the app's package and dependencies
    src/<package>/            the app's code (a multi-module app)
    <data files>              templates, userscripts, its git-ignored settings
```

- **A plugin is self-contained.** Everything that belongs to an app, its code,
  committed data and local git-ignored files, lives under `plugins/<app>/`. A
  multi-module app is a src-layout package, `plugins/<app>/src/<package>/`, and
  its `pyproject.toml` describes it: a `[build-system]` (setuptools, the
  backend `requirements-build.in` already pins) and
  `[tool.setuptools.packages.find] where = ["src"]`, beside the
  `[project].dependencies` from ADR 0003 and 0005.
- **Paths inside a plugin resolve from the plugin folder.** A module in
  `plugins/<app>/src/<package>/` reaches its folder with
  `Path(__file__).resolve().parents[2]`. Macros' `settings.json`,
  `settings.example.json`, `geckodriver.exe` and its restart target
  (`Macros.py`) all resolve this way.
- **What the launcher shares is the launcher's.** `bootstrap` and `plugin_deps`
  are in the root package `src/pluginhost/`, the launcher's own source. The
  launcher imports nothing from `plugins/`. A plugin may use `pluginhost` (the
  host's API), and nothing else outside its folder.
- **The entry script wires the path.** It puts its own `src/` first on
  `sys.path`, then the repository's `src/` for `pluginhost`, and imports the
  package. Running it directly, double-clicking it, or launching it from the
  launcher all behave the same.
- **Repository-wide tooling lists each plugin's root.** The root
  `pyproject.toml` adds `plugins/<app>/src` to pytest's `pythonpath`, ruff's
  `src` and mypy's `mypy_path`, and lists `plugins/<app>` in mypy's `files`.
  `tools/lock.py` already finds each plugin by its manifest (ADR 0005), so it
  needs no change.
- **`tests/test_plugin_layout.py` enforces it.** For each plugin, it follows the
  imports of the entry script transitively. Every first-party module must
  resolve inside that plugin's folder or `src/pluginhost/`. Nothing the
  launcher imports may resolve under `plugins/`.

To bring another plugin into line (Recipe Calc, #671): `git mv` its modules
into `plugins/<app>/src/<package>/`, re-point `parents[...]` paths at the
plugin folder, make the entry script add both `src/` folders, add the build
sections to its `pyproject.toml`, and add its `src` to the four tool settings
above. A single-module app with no package may stay a lone script in its
folder; the test still holds it to the same rule.

2026-10-04, #671: Recipe Calc now follows this layout. Its modules are the
`recipe_calc` package in `plugins/recipe_calc/src/recipe_calc/` (the window
is `app.py`, formerly `Recipe_Calc.py`), and `Recipe_Calc.py` is the entry
script. Its `__main__` runs the same first-start install as Macros before it
imports PySide6, so it also starts on a bare Python (#677).

## Options rejected

- **Keep the code in `src/macros/` (ADR 0002).** This is what was reported as
  wrong.
- **Move the shared modules into the Macros plugin and have the launcher import
  them from there.** The host would then depend on one of its plugins, and
  removing that plugin would break the launcher.
- **A `launcher/` package for the shared modules.** It would sit beside
  `launcher.pyw` under the same name, and on Windows `.pyw` is importable, so
  `import launcher` would be ambiguous. `pluginhost` names its role: what the
  host provides to plugins.
- **Give each plugin a copy of the bootstrap.** The bootstrap installs the one
  repository-wide `requirements.lock` into one shared folder (ADR 0004 and 0005).
  Copies would drift, and they would still depend on the root lock.
- **Install each plugin as a package (`pip install -e plugins/<app>`).** It
  would add a setup step for every app and every clone, and the first-start
  install that lets a bare Python run the app would then also have to install
  the app itself. The entry script's two `sys.path` entries do the same job.

## Consequences

- `plugins/macros/` can be read on its own. The repository root holds the
  launcher, `src/pluginhost/`, the locks, `tools/`, `tests/`, `docs/` and CI.
- A plugin still depends on the repository around it: `pluginhost` and the
  root `requirements.lock`. That is the host's contract, not an accident of
  layout. Making a plugin installable outside this repository is a separate
  change.
- `pluginhost.bootstrap.REQUIRED_MODULES` still lists the modules Macros needs,
  and the install folder is still `%LOCALAPPDATA%\Macros\deps-pyXY`. Neither
  changed here. Renaming the folder would reinstall everything.
- Existing checkouts must move their git-ignored `settings.json` and
  `geckodriver.exe` from the repository root into `plugins/macros/`. The app
  says which file it is missing.
- Building a plugin's wheel leaves `build/` and `*.egg-info/` behind, which are
  git-ignored.
