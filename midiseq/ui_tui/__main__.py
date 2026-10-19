"""``python -m midiseq.ui_tui song.mid`` - the same terminal editor as ``seq tui``."""

from __future__ import annotations

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
