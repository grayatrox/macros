#!/usr/bin/env python3
"""Start the Macros hotkey app ("send_to_window").

Lives in plugins/macros/ so launcher.pyw lists it, and can be double-clicked;
the code lives in the src/macros package at the repository root.
"""

import sys
from pathlib import Path

# plugins/macros/ -> repository root -> src
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from macros.__main__ import main  # needs src on sys.path, added above

raise SystemExit(main())
