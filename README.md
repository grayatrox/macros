# Macros

Small Windows tools for gaming, mostly Minecraft:

- **`Macros.py`** (code in `src/macros/`): a system-tray app (it calls itself
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

- **`src/macros/mcvote.py` + `strayamc_vote_autofill.user.js`**: `open_vote_pages()`
  (used by F20) opens the vote sites in your normal Firefox. The Tampermonkey
  userscript fills in your username, so you only solve the captcha and click
  vote. `python -m macros.mcvote` runs the older fully automatic Selenium
  flow for captcha-free findmcserver.com pages.

- **`launcher.pyw`**: a small GUI that lists the Python scripts in a folder
  and starts or stops them. It needs no third-party packages.

`Recipe Calc/` is a separate project with its own tracker and is not covered
here.

## Install

Windows and Python 3.12 or newer. From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python tools/tasks.py setup
```

`setup` installs the pinned, hash-checked dependencies from
`requirements-dev.lock`, which includes the dev tools. To install only what the
app needs at runtime, use `.venv\Scripts\python -m pip install --require-hashes -r requirements.lock`.

For the vote helper, install [Tampermonkey](https://www.tampermonkey.net/) in
Firefox and add `strayamc_vote_autofill.user.js` to it.

The legacy Selenium flow also needs
[geckodriver](https://github.com/mozilla/geckodriver/releases): put
`geckodriver.exe` in the repository root. It is git-ignored. Run the flow with:

```powershell
$env:PYTHONPATH = "src"; .venv\Scripts\python -m macros.mcvote
```

## Run

```powershell
.venv\Scripts\python tools/tasks.py run
```

This starts `Macros.py`. Its tray icon has Show / Hide Log, Reload and Quit. If
a copy is already running, a new one says so and exits. You can also start any
of the scripts from `launcher.pyw`: double-click it, or run
`.venv\Scripts\pythonw launcher.pyw`.

## Test

```powershell
.venv\Scripts\python tools/tasks.py test
```

The other verbs are `fmt`, `lint` (ruff check plus format check) and
`typecheck` (strict mypy). CI (`.github/workflows/ci.yml`) runs `setup`, `lint`,
`typecheck` and `test` on Windows. After changing dependencies in
`pyproject.toml`, regenerate the locks with `python tools/lock.py`.

## Configure

All configuration is in the source:

- **Hotkeys and games.** Each game is a `Profile` subclass in `src/macros/app.py`
  with a `WINDOW_KEYWORD` (window title), `PROCESS_NAMES` (executables allowed
  to own that window) and a `hotkeys` mapping. A key written with a leading
  `*`, like `"*f23"`, fires whatever modifiers are held.
- **Servers and vote pages.** `MinecraftProfile.STRAYA` and
  `MinecraftProfile.VOTE_SITES` in `src/macros/app.py`. The current server is read
  from the Minecraft client's `--quickPlayMultiplayer` launch argument, so it
  only reflects the server the client was launched into.
- **Your Minecraft username.** Set it in two places: `USERNAME` in `src/macros/mcvote.py`
  and `USERNAME` in `strayamc_vote_autofill.user.js`. Add a site to the
  userscript's `SELECTORS` and `@match` lines when you add a vote URL.
- **Launcher.** `launcher.pyw` remembers its folder and options in
  `~/.python_launcher_settings.json`.
