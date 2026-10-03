#!/usr/bin/env python3
"""Start the Macros hotkey app ("send_to_window").

Kept at the repository root so it can be double-clicked or picked from
launcher.pyw; the code lives in the src/macros package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from macros.__main__ import main  # needs src on sys.path, added above

raise SystemExit(main())
