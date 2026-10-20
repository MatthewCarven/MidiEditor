"""The layering rule after the lift (2026-10-20): what both editors share
lives in ``midiseq/ui_common.py``; the terminal borrows nothing from the
window's package, and the shared module sees no toolkit."""

from __future__ import annotations

import re
from pathlib import Path

from midiseq import ui_common
from midiseq.ui_gui import editor as gui_editor, grid as gui_grid
from midiseq.ui_tui import editor as tui_editor

PKG = Path(ui_common.__file__).parent


def _imports(path: Path) -> list[str]:
    return re.findall(r"^\s*(?:from|import)\s+([\w.]+)", path.read_text(encoding="utf-8"), re.M)


def test_the_terminal_imports_nothing_from_the_window():
    for path in (PKG / "ui_tui").glob("*.py"):
        assert not any("ui_gui" in m for m in _imports(path)), path.name


def test_the_shared_module_sees_no_toolkit():
    assert not any(m.startswith(("dearpygui", "textual")) for m in _imports(PKG / "ui_common.py"))
    assert not any(m.startswith(("dearpygui", "textual")) for m in _imports(PKG / "ui_gui" / "editor.py"))


def test_the_old_names_still_resolve_where_they_were():
    assert gui_editor.PROMPTS is ui_common.PROMPTS
    assert gui_editor.parse_key is ui_common.parse_key
    assert gui_grid.default_track is ui_common.default_track
    assert tui_editor.build_command is ui_common.build_command


def test_both_editors_build_the_same_command_from_the_same_answers():
    from midiseq.core import edit
    command, keysig = ui_common.build_command("xform.transpose.diatonic", {"degrees": "2", "key": "A minor"},
                                              {1, 2}, tpc=120, middle_c_octave=4)
    assert isinstance(command, edit.TransposeDiatonic)
    assert keysig == (9, "minor")                                   # the prompt's key signature is remembered
