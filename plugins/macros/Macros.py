#!/usr/bin/env python3
"""Start the Macros hotkey app ("send_to_window").

launcher.pyw lists it through launcher-app.toml, and it can be double-clicked.
The code is the macros package in this folder's src/. The first-start install
it runs is the launcher's pluginhost package, in the repository's src/.
"""

import sys
from pathlib import Path

# What the launcher provides to every plugin: plugins/macros/ -> repository root -> src
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# This plugin's own package, searched first.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from macros.__main__ import main  # needs both on sys.path, added above

raise SystemExit(main())
