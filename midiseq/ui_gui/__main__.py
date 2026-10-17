"""``python -m midiseq.ui_gui song.mid`` - the same window as ``seq gui``."""

from __future__ import annotations

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
