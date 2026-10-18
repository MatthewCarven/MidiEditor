"""Cut, copy, paste and duplicate, driven by key names and no window.

The clipboard holds plain tuples relative to the earliest start, so a paste
mints fresh ids; paste lands at the playhead and selects what it added, so
the next thing you do acts on the copy; duplicate lands right after the
selection's own end; each is one undo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from midiseq.audio.sink_null import NullSink
from midiseq.core import io_midi
from midiseq.ui_gui.editor import ACTIONS, Editor, all_entries, edit_menu, palette
from midiseq.ui_gui.grid import GridWidget

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT
BAR9 = 8 * BAR
G4 = (67, BAR9 + BEAT)         # 460 ticks, velocity 104
A4 = (69, BAR9 + 2 * BEAT)     # 460 ticks, velocity 92
C4 = (60, BAR9)                # the chord: C4 + E4, 460 ticks, 88
E4 = (64, BAR9)
FAR = 20 * BAR                 # well past the last note


def make():
    song = io_midi.load(BLUESY)
    w = GridWidget(song, 2)
    w.resize(1280, 760)
    w.go_to_bar(9)
    w.build()
    sink = NullSink()
    return song, w, Editor(w, sink), sink


def rhodes(song):
    return song.tracks[2]


def find(song, pitch, start):
    return next((n for n in rhodes(song).notes if n.pitch == pitch and n.start == start), None)


def select(ed, song, *specs):
    ed.selection.clear()
    for pitch, start in specs:
        ed.selection.add(find(song, pitch, start).nid)


def notes_at(song, start):
    return sorted((n for n in rhodes(song).notes if n.start >= start), key=lambda n: (n.start, n.pitch))


class TestCopyAndPaste:
    def test_copy_stores_offsets_from_the_earliest_start_as_plain_tuples(self):
        song, w, ed, _ = make()
        select(ed, song, G4, A4)
        assert ed.key("c", ctrl=True)
        assert ed.clipboard == ((67, 0, 460, 104), (69, BEAT, 460, 92))
        assert ed.history.depth == 0                                  # copying is not an edit
        assert "Copied 2 notes" in ed.message

    def test_paste_lands_at_the_playhead_with_offsets_kept_and_selects_the_copy(self):
        song, w, ed, sink = make()
        select(ed, song, G4, A4)
        originals = set(ed.selection)
        ed.key("c", ctrl=True)
        ed.seek(FAR)
        assert ed.key("v", ctrl=True)
        pasted = notes_at(song, FAR)
        assert [(n.pitch, n.start, n.duration, n.velocity) for n in pasted] == [
            (67, FAR, 460, 104), (69, FAR + BEAT, 460, 92)]
        assert ed.selection == {n.nid for n in pasted}
        assert not ed.selection & originals                            # fresh ids
        assert find(song, *G4) is not None and find(song, *A4) is not None   # originals untouched
        assert ed.history.depth == 1                                  # one undo
        assert [n.offset for n in sink.last.notes] == [0, BEAT]       # heard as the phrase it is
        assert "Pasted 2 notes at" in ed.message

    def test_paste_is_one_undo(self):
        song, w, ed, _ = make()
        select(ed, song, C4, E4)
        ed.copy()
        ed.seek(FAR)
        ed.paste()
        assert len(notes_at(song, FAR)) == 2
        ed.undo()
        assert notes_at(song, FAR) == [] and not ed.selection
        ed.redo()
        assert len(notes_at(song, FAR)) == 2

    def test_pasting_twice_makes_two_copies_with_distinct_ids(self):
        song, w, ed, _ = make()
        select(ed, song, G4)
        ed.copy()
        ed.seek(FAR)
        ed.paste()
        first = set(ed.selection)
        ed.paste()
        second = set(ed.selection)
        assert first and second and not first & second
        assert len([n for n in rhodes(song).notes if n.start == FAR]) == 2

    def test_a_chord_pastes_as_a_chord(self):
        song, w, ed, sink = make()
        select(ed, song, C4, E4)
        ed.copy()
        ed.seek(FAR)
        ed.paste()
        assert [(n.pitch, n.start) for n in notes_at(song, FAR)] == [(60, FAR), (64, FAR)]
        assert {n.offset for n in sink.last.notes} == {0}

    def test_the_clipboard_survives_the_originals_being_edited(self):
        song, w, ed, _ = make()
        select(ed, song, G4)
        ed.copy()
        ed.transpose(5)
        ed.key("delete")
        assert ed.clipboard == ((67, 0, 460, 104),)

    def test_paste_with_an_empty_clipboard_says_so_and_edits_nothing(self):
        song, w, ed, _ = make()
        before = len(rhodes(song).notes)
        assert ed.key("v", ctrl=True)
        assert ed.message == "Paste: clipboard is empty"
        assert ed.history.depth == 0 and len(rhodes(song).notes) == before
        assert "clipboard is empty" in ed.status_tail()

    def test_copy_with_nothing_selected_says_so_and_keeps_the_clipboard(self):
        song, w, ed, _ = make()
        assert ed.key("c", ctrl=True) and ed.message == "Copy: nothing selected"
        assert ed.clipboard == ()
        select(ed, song, G4)
        ed.copy()
        ed.deselect()
        ed.copy()
        assert ed.clipboard == ((67, 0, 460, 104),)

    def test_paste_goes_to_the_current_track(self):
        song, w, ed, _ = make()
        select(ed, song, G4)
        ed.copy()
        w.set_track(1)
        ed.seek(FAR)
        ed.paste()
        assert [n.pitch for n in song.tracks[1].notes if n.start == FAR] == [67]
        assert notes_at(song, FAR) == []                               # not the Rhodes


class TestCut:
    def test_cut_removes_and_keeps_the_clipboard(self):
        song, w, ed, sink = make()
        select(ed, song, G4, A4)
        assert ed.key("x", ctrl=True)
        assert find(song, *G4) is None and find(song, *A4) is None
        assert ed.clipboard == ((67, 0, 460, 104), (69, BEAT, 460, 92))
        assert ed.history.depth == 1 and not ed.selection
        assert sink.log == []                                         # cutting makes no sound
        assert "Cut 2 notes" in ed.message

    def test_cut_then_paste_moves_the_phrase(self):
        song, w, ed, _ = make()
        select(ed, song, G4, A4)
        ed.cut()
        ed.seek(FAR)
        ed.paste()
        assert [(n.pitch, n.start) for n in notes_at(song, FAR)] == [(67, FAR), (69, FAR + BEAT)]
        ed.undo()
        ed.undo()
        assert find(song, *G4) is not None and notes_at(song, FAR) == []

    def test_cut_with_nothing_selected_says_so(self):
        song, w, ed, _ = make()
        assert ed.key("x", ctrl=True) and ed.message == "Cut: nothing selected"
        assert ed.history.depth == 0


class TestDuplicate:
    def test_duplicate_lands_right_after_the_selection_and_selects_the_copy(self):
        song, w, ed, _ = make()
        select(ed, song, G4, A4)
        originals = set(ed.selection)
        assert ed.key("d", ctrl=True)
        end = A4[1] + 460
        copies = sorted((n for n in rhodes(song).notes if n.nid in ed.selection), key=lambda n: n.start)
        assert [(n.pitch, n.start, n.duration, n.velocity) for n in copies] == [
            (67, end, 460, 104), (69, end + BEAT, 460, 92)]
        assert not ed.selection & originals
        assert ed.history.depth == 1
        assert "Duplicated 2 notes at" in ed.message

    def test_duplicate_leaves_the_clipboard_alone(self):
        song, w, ed, _ = make()
        select(ed, song, G4)
        ed.copy()
        select(ed, song, C4, E4)
        ed.duplicate()
        assert ed.clipboard == ((67, 0, 460, 104),)

    def test_duplicating_again_chains_off_the_copy(self):
        song, w, ed, _ = make()
        select(ed, song, C4, E4)
        ed.duplicate()
        ed.duplicate()
        starts = sorted({n.start for n in rhodes(song).notes if n.pitch in (60, 64) and n.start >= BAR9})
        assert starts[:3] == [BAR9, BAR9 + 460, BAR9 + 920]

    def test_duplicate_with_nothing_selected_says_so(self):
        song, w, ed, _ = make()
        assert ed.key("d", ctrl=True) and ed.message == "Duplicate: nothing selected"
        assert ed.history.depth == 0

    def test_duplicate_is_one_undo(self):
        song, w, ed, _ = make()
        select(ed, song, G4, A4)
        before = len(rhodes(song).notes)
        ed.duplicate()
        assert len(rhodes(song).notes) == before + 2
        ed.undo()
        assert len(rhodes(song).notes) == before


class TestDoors:
    def test_the_edit_menu_and_the_palette_carry_the_clipboard(self):
        keys = [e.key for e in edit_menu()]
        assert keys[:6] == ["edit.undo", "edit.redo", "edit.cut", "edit.copy", "edit.paste",
                            "edit.duplicate"]
        assert {k for k, _, _ in ACTIONS} <= set(keys)                 # every action is in the menu
        assert palette("paste")[0].key == "edit.paste"
        assert palette("dup")[0].key == "edit.duplicate"
        shortcuts = {e.key: e.shortcut for e in edit_menu()}
        assert (shortcuts["edit.cut"], shortcuts["edit.copy"], shortcuts["edit.paste"],
                shortcuts["edit.duplicate"]) == ("Ctrl+X", "Ctrl+C", "Ctrl+V", "Ctrl+D")

    def test_the_back_halves_are_not_menu_items(self):
        keys = {e.key for e in all_entries()}
        assert "note.add_notes" not in keys and "note.velocities" not in keys

    def test_run_key_is_the_same_door(self):
        song, w, ed, _ = make()
        assert ed.run_key("edit.paste") == "Paste: clipboard is empty"
        select(ed, song, G4)
        assert ed.run_key("edit.copy") == "Copied 1 note"
        ed.seek(FAR)
        assert ed.run_key("edit.paste").startswith("Pasted 1 note at")
        assert ed.run_key("edit.duplicate").startswith("Duplicated 1 note at")
        assert ed.run_key("edit.cut") == "Cut 1 note"

    def test_plain_letters_are_not_the_editors(self):
        song, w, ed, _ = make()
        select(ed, song, G4)
        for name in ("x", "c", "v", "d"):
            assert ed.key(name) is False
        assert ed.history.depth == 0 and ed.clipboard == ()

    def test_the_window_maps_the_keys(self):
        dpg = pytest.importorskip("dearpygui.dearpygui")
        from midiseq.ui_gui import app
        names = set(app.KEY_NAMES.values())
        assert {"x", "c", "v", "d"} <= names
        assert {"x", "c", "v", "d"} <= app.ONE_SHOT
        for letter in "XCVD":
            assert app.KEY_NAMES[getattr(dpg, f"mvKey_{letter}")] == letter.lower()
