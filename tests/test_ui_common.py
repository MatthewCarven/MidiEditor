"""The layering rule after the lift (2026-10-20): what both editors share
lives in ``midiseq/ui_common.py``; the terminal borrows nothing from the
window's package, and the shared module sees no toolkit."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_autosave_lands_beside_the_song_or_in_the_cwd(tmp_path, monkeypatch):
    from midiseq.core import io_midi
    song = io_midi.load(Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid")
    target = ui_common.autosave(song, tmp_path / "tune.mid")
    assert target == tmp_path / "tune.autosave.mid" and target.exists()
    assert len(io_midi.load(target).tracks) == len(song.tracks)
    monkeypatch.chdir(tmp_path)
    untitled = ui_common.autosave_path(None)
    assert untitled.name == "untitled.autosave.mid" and untitled.parent.resolve() == tmp_path.resolve()


def test_the_view_entries_are_in_the_palette_with_a_prompt_for_the_key():
    keys = {e.key: e for e in ui_common.all_entries()}
    assert keys["view.key"].needs_prompt and not keys["view.scale_filter"].needs_prompt
    assert keys["view.range"].needs_prompt and not keys["view.profile"].needs_prompt
    assert ui_common.entry_label("view.key") == "Key" and ui_common.entry_label("xform.invert") == "Invert"
    assert ui_common.entry_label("view.range") == "Singing range"
    assert ui_common.key_text(9, "minor") == "A minor"


def test_a_singing_range_is_two_note_names_in_either_order():
    assert ui_common.parse_range("D4-A4", 4) == (62, 69) == ui_common.parse_range("A4 - D4", 4)
    assert ui_common.parse_range("C4 to C5", 4) == (60, 72) == ui_common.parse_range("C4 C5", 4)
    assert ui_common.parse_range("D3-A3", 3) == (62, 69)         # the middle-C setting moves the names, not the notes
    assert ui_common.parse_range("C-1-C4", 4) == (0, 60)         # a dash inside a name is not the separator
    assert ui_common.range_text(62, 69, 4) == "D4-A4" and ui_common.range_text(62, 69, 3) == "D3-A3"
    with pytest.raises(ValueError):
        ui_common.parse_range("D4", 4)
    with pytest.raises(ValueError):
        ui_common.parse_range("H4-A4", 4)
