"""PLAN.md §11's mouse model, driven with coordinates and no window.

    on a note                    on empty space
  click      select + play     click      insert + play
  drag       move it           drag       insert, drag out length
  shift+clk  add to set        shift+drag marquee select
  right      delete            right      -
  right+drag erase sweep       ctrl+drag  ignore snap
"""

from __future__ import annotations

from pathlib import Path

from midiseq.audio.sink_null import NullSink
from midiseq.core import edit, io_midi
from midiseq.ui_gui.editor import (
    EDGE_PX, LEFT, PROMPTS, RIGHT, Editor, all_entries, edit_menu, menu_sections,
    palette, parse_key, pitch_class, velocity_change,
)
from midiseq.ui_gui.grid import GridWidget
from midiseq.ui_gui.paint import Rect

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT
TPC = 120                      # 1/16 at 480 ppq
BAR9 = 8 * BAR                 # tick where bar 9 starts
G4 = (67, BAR9 + BEAT)         # the loud quarter on beat 2, 460 ticks long
A4 = (69, BAR9 + 2 * BEAT)
C4 = (60, BAR9)
E4 = (64, BAR9)


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


def row_of(w, pitch):
    return next(i for i, r in enumerate(w.matrix.rows) if r.pitch == pitch)


def col_of(w, tick):
    return w.view.tick_to_cell(tick)


def centre(w, row, col):
    x0, y0, x1, y1 = w.layout.cell_rect(row, col, w.first_cell)
    return (x0 + x1) / 2, (y0 + y1) / 2


def click(ed, w, row, col, button=LEFT, **mods):
    x, y = centre(w, row, col)
    ed.press(x, y, button, **mods)
    ed.release(x, y, button, **mods)


def drag(ed, w, row0, col0, row1, col1, button=LEFT, **mods):
    x0, y0 = centre(w, row0, col0)
    x1, y1 = centre(w, row1, col1)
    ed.press(x0, y0, button, **mods)
    ed.move(x1, y1, **mods)
    ed.release(x1, y1, button, **mods)


class TestInsert:
    def test_click_on_empty_inserts_one_cell_and_plays_it(self):
        song, w, ed, sink = make()
        row, col = row_of(w, 71), w.first_cell + 8          # B4, bar 9 beat 3: empty
        click(ed, w, row, col)
        n = find(song, 71, w.tick_at_col(col))
        assert n is not None and n.duration == TPC and n.velocity == 80
        assert ed.history.depth == 1
        assert sink.last is not None and sink.last.notes[0].pitch == 71
        assert not ed.selection                              # inserting doesn't select

    def test_drag_draws_the_length_out(self):
        song, w, ed, _ = make()
        row, col = row_of(w, 71), w.first_cell + 8
        drag(ed, w, row, col, row, col + 3)
        n = find(song, 71, w.tick_at_col(col))
        assert n is not None and n.duration == 4 * TPC

    def test_dragging_left_still_inserts_from_the_left(self):
        song, w, ed, _ = make()
        row, col = row_of(w, 71), w.first_cell + 8
        drag(ed, w, row, col + 3, row, col)
        n = find(song, 71, w.tick_at_col(col))
        assert n is not None and n.duration == 4 * TPC

    def test_ctrl_ignores_snap(self):
        song, w, ed, _ = make()
        row, col = row_of(w, 71), w.first_cell + 8
        x0, y0, _, _ = w.layout.cell_rect(row, col, w.first_cell)
        x, y = x0 + 7, y0 + 5                                # half a 14 px cell in
        ed.press(x, y, LEFT, ctrl=True)
        ed.release(x, y, LEFT, ctrl=True)
        assert find(song, 71, w.tick_at_col(col) + 60) is not None

    def test_insert_is_undoable(self):
        song, w, ed, _ = make()
        row, col = row_of(w, 71), w.first_cell + 8
        click(ed, w, row, col)
        ed.undo()
        assert find(song, 71, w.tick_at_col(col)) is None
        ed.redo()
        assert find(song, 71, w.tick_at_col(col)) is not None

    def test_audition_toggle_silences_it(self):
        song, w, ed, sink = make()
        ed.toggle_audition()
        click(ed, w, row_of(w, 71), w.first_cell + 8)
        assert sink.log == []
        assert find(song, 71, w.tick_at_col(w.first_cell + 8)) is not None   # still inserted


class TestSelectAndMove:
    def test_click_on_a_note_selects_and_plays_without_editing(self):
        song, w, ed, sink = make()
        g4 = find(song, *G4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        assert ed.selection == {g4.nid}
        assert sink.last.notes[0].pitch == 67
        assert ed.history.depth == 0

    def test_shift_click_adds_and_removes(self):
        song, w, ed, _ = make()
        g4, a4 = find(song, *G4), find(song, *A4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        click(ed, w, row_of(w, 69), col_of(w, A4[1]), shift=True)
        assert ed.selection == {g4.nid, a4.nid}
        click(ed, w, row_of(w, 69), col_of(w, A4[1]), shift=True)
        assert ed.selection == {g4.nid}

    def test_drag_moves_it_snapped_and_plays_the_landing(self):
        song, w, ed, sink = make()
        g4 = find(song, *G4)
        r, c = row_of(w, 67), col_of(w, G4[1])
        drag(ed, w, r, c, r - 2, c + 4)                     # up two pitches, right four cells
        moved = rhodes(song).note_by_id(g4.nid)
        assert (moved.pitch, moved.start) == (69, G4[1] + 4 * TPC)
        assert ed.history.depth == 1
        assert sink.last.notes[0].pitch == 69
        assert ed.selection == {g4.nid}                      # the selection follows the note

    def test_dragging_one_of_a_selection_moves_them_all(self):
        song, w, ed, _ = make()
        g4, a4 = find(song, *G4), find(song, *A4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        click(ed, w, row_of(w, 69), col_of(w, A4[1]), shift=True)
        r, c = row_of(w, 67), col_of(w, G4[1])
        drag(ed, w, r, c, r, c + 1)
        assert rhodes(song).note_by_id(g4.nid).start == G4[1] + TPC
        assert rhodes(song).note_by_id(a4.nid).start == A4[1] + TPC

    def test_ctrl_drag_moves_by_pixels_not_cells(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)
        x, y = centre(w, row_of(w, 67), col_of(w, G4[1]))
        ed.press(x, y, LEFT, ctrl=True)
        ed.move(x + 7, y, ctrl=True)
        ed.release(x + 7, y, LEFT, ctrl=True)
        assert rhodes(song).note_by_id(g4.nid).start == G4[1] + 60

    def test_a_click_that_goes_nowhere_is_not_an_edit(self):
        song, w, ed, _ = make()
        r, c = row_of(w, 67), col_of(w, G4[1])
        drag(ed, w, r, c, r, c)
        assert ed.history.depth == 0

    def test_dragging_the_right_edge_resizes(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)                                 # 460 ticks: cells 132..135
        row = row_of(w, 67)
        last = col_of(w, g4.end - 1)
        _, y0, x1, y1 = w.layout.cell_rect(row, last, w.first_cell)
        x_end = w.layout.x_of(last, w.first_cell) + w.layout.cell_w * (100 / 120) - 1
        x, y = x_end - 2, (y0 + y1) / 2                      # inside the edge zone
        ed.press(x, y, LEFT)
        assert ed.gesture is not None and ed.gesture.kind == "resize"
        tx, ty = centre(w, row, last + 2)
        ed.move(tx, ty)
        ed.release(tx, ty, LEFT)
        resized = rhodes(song).note_by_id(g4.nid)
        assert resized.start == g4.start
        assert resized.duration == (last + 3) * TPC - g4.start


class TestMarquee:
    def test_shift_drag_selects_what_the_box_touches(self):
        song, w, ed, _ = make()
        drag(ed, w, row_of(w, 71), w.first_cell, row_of(w, 60), w.first_cell + 15, shift=True)
        pitches = sorted(rhodes(song).note_by_id(n).pitch for n in ed.selection)
        assert pitches == [60, 63, 64, 67, 69]               # bar 9's five; the ghost D4 is not ours

    def test_a_marquee_adds_to_the_set(self):
        song, w, ed, _ = make()
        f4 = find(song, 65, BAR9 + 2 * BAR + 37)
        click(ed, w, row_of(w, 65), col_of(w, f4.start))
        drag(ed, w, row_of(w, 71), w.first_cell, row_of(w, 60), w.first_cell + 15, shift=True)
        assert f4.nid in ed.selection and len(ed.selection) == 6


class TestErase:
    def test_right_click_deletes_with_no_confirmation_and_undo_brings_it_back(self):
        song, w, ed, sink = make()
        g4 = find(song, *G4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]), button=RIGHT)
        assert rhodes(song).note_by_id(g4.nid) is None
        assert sink.log == []                                # deleting makes no sound
        ed.undo()
        assert rhodes(song).note_by_id(g4.nid) is not None

    def test_right_drag_sweeps_and_is_one_undo(self):
        song, w, ed, _ = make()
        g4, a4 = find(song, *G4), find(song, *A4)
        x0, y0 = centre(w, row_of(w, 67), col_of(w, G4[1]))
        x1, y1 = centre(w, row_of(w, 69), col_of(w, A4[1]))
        ed.press(x0, y0, RIGHT)
        ed.move(x1, y1)
        ed.release(x1, y1, RIGHT)
        assert rhodes(song).note_by_id(g4.nid) is None and rhodes(song).note_by_id(a4.nid) is None
        assert ed.history.depth == 1
        ed.undo()
        assert rhodes(song).note_by_id(g4.nid) is not None and rhodes(song).note_by_id(a4.nid) is not None

    def test_right_click_on_empty_does_nothing(self):
        song, w, ed, _ = make()
        before = len(rhodes(song).notes)
        click(ed, w, row_of(w, 71), w.first_cell + 8, button=RIGHT)
        assert len(rhodes(song).notes) == before and ed.history.depth == 0


class TestKeys:
    def test_escape_deselects_and_cancels(self):
        song, w, ed, _ = make()
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        assert ed.key("escape") and not ed.selection
        x, y = centre(w, row_of(w, 71), w.first_cell + 8)
        ed.press(x, y, LEFT)
        ed.key("escape")
        assert ed.gesture is None
        ed.release(x, y, LEFT)
        assert ed.history.depth == 0                         # the cancelled insert never landed

    def test_delete_key_deletes_the_selection(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        assert ed.key("delete")
        assert rhodes(song).note_by_id(g4.nid) is None

    def test_undo_and_redo_keys(self):
        song, w, ed, _ = make()
        click(ed, w, row_of(w, 71), w.first_cell + 8)
        assert ed.key("z", ctrl=True) and ed.history.depth == 0
        assert ed.key("y", ctrl=True) and ed.history.depth == 1
        assert ed.key("z", ctrl=True, shift=True) is True   # Ctrl+Shift+Z is redo too
        assert ed.history.depth == 1

    def test_arrows_transpose_a_selection_and_scroll_otherwise(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)
        assert ed.key("up") is False                         # nothing selected: not ours
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        assert ed.key("up") and rhodes(song).note_by_id(g4.nid).pitch == 68
        assert ed.key("down") and ed.key("down") and rhodes(song).note_by_id(g4.nid).pitch == 66

    def test_shift_up_is_diatonic(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        ed.key("up", shift=True)                             # G up a degree in C major is A
        assert rhodes(song).note_by_id(g4.nid).pitch == 69

    def test_left_and_right_nudge_by_a_cell(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        ed.key("right")
        assert rhodes(song).note_by_id(g4.nid).start == G4[1] + TPC
        ed.key("left")
        ed.key("left")
        assert rhodes(song).note_by_id(g4.nid).start == G4[1] - TPC

    def test_q_quantises_the_off_grid_note_to_the_grid(self):
        song, w, ed, _ = make()
        f4 = find(song, 65, BAR9 + 2 * BAR + 37)             # the ~ note, 37 ticks late
        click(ed, w, row_of(w, 65), col_of(w, f4.start))
        assert ed.selection == {f4.nid}
        ed.key("q")
        assert rhodes(song).note_by_id(f4.nid).start == BAR9 + 2 * BAR

    def test_select_all_and_audition_toggle(self):
        song, w, ed, _ = make()
        assert ed.key("a", ctrl=True) and len(ed.selection) == len(rhodes(song).notes)
        assert ed.key("a") and not ed.transport.audition_enabled


class TestRegistryDoors:
    def test_the_notes_menu_teaches_the_taxonomy(self):
        sections = menu_sections()
        assert [name for name, _ in sections] == ["keeps the shape", "moves notes independently"]
        shape = {e.key for e in sections[0][1]}
        independent = {e.key for e in sections[1][1]}
        assert {"xform.transpose.chromatic", "xform.transpose.diatonic", "xform.invert",
                "xform.retrograde", "xform.augment", "xform.diminish"} <= shape
        assert {"xform.quantise", "xform.snap_to_scale", "note.velocity"} <= independent
        assert not shape & independent

    def test_gestures_are_not_menu_items(self):
        keys = {e.key for e in all_entries()}
        assert "note.add" not in keys and "note.move" not in keys and "note.resize" not in keys
        assert "note.add_notes" not in keys and "note.velocities" not in keys
        assert "note.delete" in keys

    def test_the_edit_menu(self):
        keys = [e.key for e in edit_menu()]
        assert keys[:2] == ["edit.undo", "edit.redo"] and "note.delete" in keys

    def test_the_palette_finds_by_prefix_then_by_letters_in_order(self):
        assert palette("inv")[0].label.startswith("Invert")
        assert palette("retro")[0].key == "xform.retrograde"
        assert any(e.key == "xform.transpose.diatonic" for e in palette("tsd"))
        assert len(palette("")) == len(all_entries())

    def test_entries_know_whether_they_ask_first(self):
        asks = {e.key for e in all_entries() if e.needs_prompt}
        assert asks == set(PROMPTS)

    def test_run_key_with_prompt_values(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        message = ed.run_key("xform.transpose.chromatic", {"semitones": "+5"})
        assert rhodes(song).note_by_id(g4.nid).pitch == 72
        assert "Transpose" in message and "1 note" in message

    def test_run_key_with_nothing_selected_says_so_and_edits_nothing(self):
        song, w, ed, _ = make()
        message = ed.run_key("xform.retrograde")
        assert "nothing selected" in message and ed.history.depth == 0

    def test_a_bad_value_is_a_sentence_not_a_traceback(self):
        song, w, ed, _ = make()
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        message = ed.run_key("xform.transpose.chromatic", {"semitones": "lots"})
        assert "Transpose" in message and ed.history.depth == 0

    def test_velocity_forms(self):
        song, w, ed, _ = make()
        g4 = find(song, *G4)                                 # velocity 104
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        ed.run_key("note.velocity", {"velocity": "96"})
        assert rhodes(song).note_by_id(g4.nid).velocity == 96
        ed.run_key("note.velocity", {"velocity": "+10"})
        assert rhodes(song).note_by_id(g4.nid).velocity == 106
        ed.run_key("note.velocity", {"velocity": "x0.5"})
        assert rhodes(song).note_by_id(g4.nid).velocity == 53

    def test_snap_to_scale_remembers_the_key_for_diatonic_moves(self):
        song, w, ed, _ = make()
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        ed.run_key("xform.snap_to_scale", {"key": "A minor"})
        assert (ed.key_root, ed.key_scale) == (9, "minor")

    def test_invert_takes_an_axis_by_name(self):
        song, w, ed, _ = make()
        c4, e4 = find(song, *C4), find(song, *E4)
        click(ed, w, row_of(w, 60), col_of(w, C4[1]))
        click(ed, w, row_of(w, 64), col_of(w, E4[1]), shift=True)
        ed.run_key("xform.invert", {"axis": "D4"})
        assert rhodes(song).note_by_id(c4.nid).pitch == 64
        assert rhodes(song).note_by_id(e4.nid).pitch == 60

    def test_augment_and_diminish_are_registered_with_their_own_labels(self):
        assert edit.REGISTRY["xform.augment"].label == "Augment x2"
        assert edit.REGISTRY["xform.diminish"].label == "Diminish :2"


class TestPreviewAndPicture:
    def test_an_insert_drag_previews_where_the_note_will_land(self):
        song, w, ed, _ = make()
        row, col = row_of(w, 71), w.first_cell + 8
        x0, y0 = centre(w, row, col)
        ed.press(x0, y0, LEFT)
        ed.move(*centre(w, row, col + 3))
        items = ed.preview_items()
        assert len(items) == 1 and items[0].role == "preview"
        rx0, _, rx1, _ = w.layout.cell_rect(row, col, w.first_cell)
        assert items[0].x0 == rx0
        assert items[0].x1 == w.layout.cell_rect(row, col + 3, w.first_cell)[2]
        ed.cancel()
        assert ed.preview_items() == []

    def test_marquee_and_erase_previews_have_their_own_roles(self):
        song, w, ed, _ = make()
        x0, y0 = centre(w, row_of(w, 71), w.first_cell)
        ed.press(x0, y0, LEFT, shift=True)
        ed.move(*centre(w, row_of(w, 60), w.first_cell + 15), shift=True)
        [box] = ed.preview_items()
        assert box.role == "marquee" and not box.filled
        ed.cancel()
        ed.press(*centre(w, row_of(w, 67), col_of(w, G4[1])), RIGHT)
        [mark] = ed.preview_items()
        assert mark.role == "erase" and not mark.filled

    def test_a_selected_note_is_outlined_in_the_picture(self):
        song, w, ed, _ = make()
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        plan = w.build()
        [outline] = plan.rects("selected")
        [loud] = plan.rects("note_loud")
        assert not outline.filled
        assert outline.x0 == loud.x0 - 1 and outline.x1 == loud.x1 + 1

    def test_status_tail_reports_selection_and_undo(self):
        song, w, ed, _ = make()
        click(ed, w, row_of(w, 67), col_of(w, G4[1]))
        ed.key("up")
        tail = ed.status_tail()
        assert "sel 1" in tail and "undo: Transpose" in tail


def test_the_parsers():
    assert pitch_class("F#") == 6 and pitch_class("Bb") == 10 and pitch_class("c4") == 0
    assert parse_key("A minor") == (9, "minor")
    assert parse_key("D harmonic minor") == (2, "harmonic minor")
    assert velocity_change("x0.8") == {"scale": 0.8}
    assert velocity_change("-10") == {"delta": -10}
    assert velocity_change("96") == {"velocity": 96}
