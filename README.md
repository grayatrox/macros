# Macros

Small Windows tools for gaming, mostly Minecraft:

- **`plugins/macros/Macros.py`** (code in `plugins/macros/src/macros/`): a system-tray app (it calls itself
  *send_to_window*) that
  listens for global hotkeys and runs per-game macros. It only acts when the
  matching game is focused, checking both the window title and the owning
  process. Only one copy runs at a time. In Minecraft:

  | Key | Action |
  |-----|--------|
  | F23 | `/home` (`/is home` on StrayaMC) |
  | F22 | `/spawn` |
  | F24 | `/shop` |
  | F20 | Open the current server's vote pages in Firefox |

  F23 in the app's own log window restarts the app. The macros paste the
  command through the clipboard and then put your clipboard contents back.
  Your real keypresses are held back while a macro types.

- **`plugins/macros/src/macros/mcvote.py` + `plugins/macros/strayamc_vote_autofill.user.js`**: `open_vote_pages()`
  (used by F20) opens the vote sites in your normal Firefox. The Tampermonkey
  userscript fills in your username, so you only solve the captcha and click
  vote. `python -m macros.mcvote` runs the older fully automatic Selenium
  flow for captcha-free findmcserver.com pages.

- **`launcher.pyw`**: a small PySide6 (Qt) GUI that lists the apps in
  `plugins/` and starts or stops them. Like `Macros.py`, it installs its
  components on first start (see Install). An app is a folder in `plugins/`
  with a `launcher-app.toml` (see Configure).

Every launchable app lives in its own folder under `plugins/`, code, data and
`pyproject.toml` included. The repository root keeps the launcher, the code it
shares with its apps (`src/pluginhost/`: the first-start install and the
dependency check), tooling and tests (`docs/adr/0006-plugins-are-self-contained.md`).
`plugins/recipe_calc/` is the Recipe Calc app: a `recipe_calc` package in
`plugins/recipe_calc/src/`, a thin `Recipe_Calc.py` entry point beside it, and its
own `pyproject.toml`. It is a separate project with its own tracker and is not
described further here, though the `lint`, `typecheck` and `test` verbs below
check it too.

## Install

Windows and Python 3.12 or newer. To just use the app, copy
`plugins/macros/settings.example.json` to `plugins/macros/settings.json`, fill
in your values (see [Configure](#configure)), and run it (see [Run](#run)). On
first start it (or
`launcher.pyw`) installs its own components into
`%LOCALAPPDATA%\Macros\deps-py312` (one folder per Python version), showing a
small progress window. Your Python installation is not modified.

- **Pinned.** Every download is checked against a hash in `requirements.lock`.
  A few packages (pyautogui and friends) only ship source code, so they are
  built against the setuptools pinned in `requirements-build.lock`.
- **Updated.** Editing the lock reinstalls on the next start.
- **Shared.** Every app uses the one folder. An app that starts while another
  is installing waits for that install and reuses it.
- **Failures.** If the install fails, a dialog shows pip's reason and any
  earlier install is kept.

To work on the code, make a virtualenv with the dev tools. From the repository
root:

```powershell
python -m venv .venv
.venv\Scripts\python tools/tasks.py setup
```

`setup` installs the pinned, hash-checked `requirements-dev.lock`. In that
environment the app finds everything already installed and skips the
first-start install.

Then create your settings file from the template and fill in your own values
(see [Configure](#configure)). The app will not start without it:

```powershell
Copy-Item plugins/macros/settings.example.json plugins/macros/settings.json
```

For the vote helper, install [Tampermonkey](https://www.tampermonkey.net/) in
Firefox and add `plugins/macros/strayamc_vote_autofill.user.js` to it.

The legacy Selenium flow also needs
[geckodriver](https://github.com/mozilla/geckodriver/releases): put
`geckodriver.exe` in `plugins/macros/`. It is git-ignored. Run the flow on
one or more findmcserver.com vote pages, with the username from `settings.json`:

```powershell
$env:PYTHONPATH = "plugins/macros/src"; .venv\Scripts\python -m macros.mcvote https://findmcserver.com/server/<name>?vote=true
```

## Run

```powershell
python plugins/macros/Macros.py
```

Or double-click `plugins/macros/Macros.py`. From the dev virtualenv, use
`.venv\Scripts\python tools/tasks.py run`. This starts the app. Its tray icon has Show / Hide Log, Reload and Quit. If
a copy is already running, a new one says so and exits. You can also start any
of the apps from `launcher.pyw`: double-click it, or run
`.venv\Scripts\pythonw launcher.pyw`.

## Test

```powershell
.venv\Scripts\python tools/tasks.py test
```

The other verbs are `fmt`, `lint` (ruff check plus format check) and
`typecheck` (strict mypy). CI (`.github/workflows/ci.yml`) runs `setup`, `lint`,
`typecheck` and `test` on Windows. Each app declares its own runtime
dependencies in `plugins/<app>/pyproject.toml`, and only there. The root
`pyproject.toml` declares the launcher's own (PySide6) plus the `dev` extra and
the tool config. `python tools/lock.py` compiles the root and every plugin's
`pyproject.toml` together into the hashed locks
(`docs/adr/0005-root-declares-only-the-launcher.md`); run it after changing any
of them.

## Configure

Personal values live in `plugins/macros/settings.json`. It is git-ignored, so
nothing identifying is committed; `plugins/macros/settings.example.json` is the
template. The app reads it once at startup and refuses to start, with a message
saying what is wrong, if it is missing or invalid.

- **`username`.** Your Minecraft username, filled in by the legacy Selenium
  vote flow.
- **`vote_sites`.** Maps a server address, as it appears in the client's
  `--quickPlayMultiplayer` launch argument, to the list of `https://` vote
  pages F20 opens for that server.

The userscript cannot read `settings.json`. It asks for your username on the
first vote page and keeps it in Tampermonkey's storage; change it from the
Tampermonkey menu with **Set Minecraft username**.

Everything else is in the source:

- **Hotkeys and games.** Each game is a `Profile` subclass in `plugins/macros/src/macros/app.py`
  with a `WINDOW_KEYWORD` (window title), `PROCESS_NAMES` (executables allowed
  to own that window) and a `hotkeys` mapping. A key written with a leading
  `*`, like `"*f23"`, fires whatever modifiers are held.
- **Server-specific commands.** `MinecraftProfile.STRAYA` in `plugins/macros/src/macros/app.py`.
  The current server is read from the Minecraft client's `--quickPlayMultiplayer`
  launch argument, so it only reflects the server the client was launched into.
- **Userscript sites.** Add a site to the userscript's `SELECTORS` and `@match`
  lines when you add a vote URL on a new host.
- **Launcher.** `launcher.pyw` remembers its folder and options in
  `~/.python_launcher_settings.json`. The folder starts as this repository's
  `plugins/`. It lists only scripts declared by a `launcher-app.toml` in a
  folder directly inside the chosen one, such as
  `plugins/recipe_calc/launcher-app.toml`:

  ```toml
  script = "Recipe_Calc.py"
  ```

  `script` must be a relative path to a `.py` or `.pyw` file inside that
  folder. A manifest in the chosen folder itself, or nested deeper, is ignored,
  as is every other `.py` file. A bad manifest is named in a warning, and the
  other apps are still listed. The reasoning is in
  `docs/adr/0001-launcher-app-manifests.md` and
  `docs/adr/0002-apps-live-in-plugins.md`. To add an app, give it a folder in
  `plugins/` holding its code, a manifest, and a `pyproject.toml` declaring its
  dependencies (`docs/adr/0006-plugins-are-self-contained.md`).

  After each scan the launcher checks every listed app's `[project].dependencies`
  against the versions pinned in `requirements.lock`. If any are missing, it
  installs the hash-pinned lock into `%LOCALAPPDATA%\Macros\deps-py312` in the
  background, the same install as the first start. The app shows
  *installing...* and launches once the install is done. Apps started from
  the launcher get that folder on `PYTHONPATH`. A dependency the lock does not
  pin is reported, not installed: run `python tools/lock.py`, which pins the
  dependencies of every app in `plugins/`. See `docs/adr/0004-plugin-dependency-install.md`.
