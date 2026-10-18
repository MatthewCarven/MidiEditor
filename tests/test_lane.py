"""The velocity lane is editable (PLAN.md §8), driven with coordinates and
no window.

A press in the lane is a velocity gesture and never a note gesture; every
column a drag crosses takes the pointer's velocity at that moment, so a
diagonal sweep is a ramp and a Shift sweep is a flat line; the whole sweep
is one undo; the bars preview it; a chord's notes get one value.
"""

from __future__ import annotations

from pathlib import Path

from midiseq.audio.sink_null import NullSink
from midiseq.core import io_midi
from midiseq.ui_gui.editor import LEFT, RIGHT, VELOCITY_STEP, Editor
from midiseq.ui_gui.grid import GridWidget
from midiseq.ui_gui.paint import DEFAULT_THEME, Layout, lane_bar

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
WIDTH, HEIGHT = 1280, 760
PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT
BAR9 = 8 * BAR
CHORD = 128                    # bar 9 beat 1: C4 + E4, both at 88
G4_COL = 132                   # bar 9 beat 2: the loud G4, 104
A4_COL = 136                   # bar 9 beat 3: A4, 92
DS4_COL = 140                  # bar 9 beat 4: D#4, 76
C4_COL = 144                   # bar 10 beat 1: C4, 88
EMPTY = 130                    # nothing starts here


def make():
    song = io_midi.load(BLUESY)
    w = GridWidget(song, 2)
    w.resize(WIDTH, HEIGHT)
    w.go_to_bar(9)
    w.build()
    sink = NullSink()
    return song, w, Editor(w, sink), sink


def rhodes(song):
    return song.tracks[2]


def lane_point(w, col, velocity):
    """A point in the lane over ``col`` whose height means ``velocity``."""
    _, _, _, bottom = w.layout.lane_rect(w.plan.rows, w.width)
    x = w.layout.x_of(col, w.first_cell) + w.layout.cell_w / 2
    return x, bottom - velocity * w.layout.lane_h / 127


def velocities(w, col):
    return sorted(n.velocity for n in w.notes_starting_in(col))


def sweep(ed, w, col0, v0, col1, v1, **mods):
    x0, y0 = lane_point(w, col0, v0)
    x1, y1 = lane_point(w, col1, v1)
    ed.press(x0, y0, LEFT, **mods)
    ed.move(x1, y1, **mods)
    ed.release(x1, y1, LEFT, **mods)


class TestHitTesting:
    def test_the_lane_sits_under_the_grid_and_spans_the_columns(self):
        layout = Layout(cell_w=14)
        x0, y0, x1, y1 = layout.lane_rect(48, WIDTH)
        assert (x0, y0) == (layout.gutter_w, layout.ruler_h + 48 * layout.row_h)
        assert x1 == layout.gutter_w + layout.visible_cols(WIDTH) * layout.cell_w
        assert y1 == y0 + layout.lane_h

    def test_top_is_fortissimo_and_the_floor_is_one(self):
        layout = Layout(cell_w=14)
        first_cell, rows = 128, 48
        x0, top, x1, bottom = layout.lane_rect(rows, WIDTH)
        assert layout.lane_hit(x0, top, first_cell, rows, WIDTH) == (first_cell, 127)
        _, low = layout.lane_hit(x0, bottom - 1, first_cell, rows, WIDTH)
        assert 1 <= low <= 3
        assert layout.lane_hit(x0, bottom, first_cell, rows, WIDTH) is None      # below
        assert layout.lane_hit(x0, top - 1, first_cell, rows, WIDTH) is None     # the grid
        assert layout.lane_hit(x0 - 1, top, first_cell, rows, WIDTH) is None     # the gutter
        assert layout.lane_hit(x1, top, first_cell, rows, WIDTH) is None         # past the last column
        assert layout.lane_hit(x1 - 1, top, first_cell, rows, WIDTH)[0] == first_cell + layout.visible_cols(WIDTH) - 1

    def test_a_bar_top_reads_back_as_its_own_value(self):
        layout = Layout(cell_w=14)
        first_cell, rows = 128, 48
        for v in (1, 20, 80, 104, 127):
            bar = lane_bar(layout, first_cell, rows, 130, v)
            x0, y0, x1, y1 = layout.lane_rect(rows, WIDTH)
            if bar.y0 >= y1:        # a bar too short to have a top pixel inside the lane
                continue
            _, read = layout.lane_hit(bar.x0, bar.y0, first_cell, rows, WIDTH)
            assert abs(read - v) <= 3

    def test_the_lane_is_not_a_grid_cell(self):
        layout = Layout(cell_w=14)
        x0, top, _, bottom = layout.lane_rect(48, WIDTH)
        assert layout.hit_test(x0 + 5, top, 128, 48, WIDTH) is None
        assert layout.hit_test(x0 + 5, bottom - 1, 128, 48, WIDTH) is None
        assert layout.hit_test(x0 + 5, top - 1, 128, 48, WIDTH) == (47, 128)

    def test_a_wandering_drag_pins_at_the_extremes(self):
        layout = Layout(cell_w=14)
        _, top, _, bottom = layout.lane_rect(48, WIDTH)
        assert layout.lane_raw(layout.gutter_w, top - 300, 128, 48) == (128, 127)
        assert layout.lane_raw(layout.gutter_w, bottom + 300, 128, 48) == (128, 1)
        assert layout.lane_raw(-500, top, 0, 48)[0] == 0                # never left of cell 0
        assert layout.lane_raw(-500, top, 128, 48)[0] < 128             # but off the left edge is real

    def test_the_preview_role_has_a_colour(self):
        assert "lane_preview" in DEFAULT_THEME


class TestDrag:
    def test_a_click_sets_the_whole_chord_to_one_value_and_is_one_undo(self):
        song, w, ed, sink = make()
        assert velocities(w, CHORD) == [88, 88]
        x, y = lane_point(w, CHORD, 40)
        ed.press(x, y, LEFT)
        assert ed.gesture is not None and ed.gesture.kind == "velocity"
        ed.release(x, y, LEFT)
        assert velocities(w, CHORD) == [40, 40]
        assert ed.history.depth == 1
        assert sorted(n.pitch for n in sink.last.notes) == [60, 64]     # heard, as a chord
        ed.undo()
        assert velocities(w, CHORD) == [88, 88]
        ed.redo()
        assert velocities(w, CHORD) == [40, 40]

    def test_the_last_height_in_a_column_wins(self):
        song, w, ed, _ = make()
        x, y = lane_point(w, G4_COL, 30)
        ed.press(x, y, LEFT)
        ed.move(*lane_point(w, G4_COL, 100))
        ed.release(*lane_point(w, G4_COL, 100), LEFT)
        assert velocities(w, G4_COL) == [100]

    def test_a_diagonal_sweep_draws_a_ramp(self):
        song, w, ed, _ = make()
        sweep(ed, w, CHORD, 20, C4_COL, 120)
        ramp = [velocities(w, c)[0] for c in (CHORD, G4_COL, A4_COL, DS4_COL, C4_COL)]
        assert ramp[0] == 20 and ramp[-1] == 120
        assert ramp == sorted(ramp) and len(set(ramp)) == 5           # strictly rising
        assert velocities(w, CHORD) == [20, 20]
        assert ed.history.depth == 1                                  # one undo for the sweep
        ed.undo()
        assert [velocities(w, c)[0] for c in (CHORD, G4_COL, A4_COL, DS4_COL, C4_COL)] == [88, 104, 92, 76, 88]

    def test_a_sweep_leftwards_is_the_same_ramp(self):
        song, w, ed, _ = make()
        sweep(ed, w, C4_COL, 120, CHORD, 20)
        ramp = [velocities(w, c)[0] for c in (CHORD, G4_COL, A4_COL, DS4_COL, C4_COL)]
        assert ramp[0] == 20 and ramp[-1] == 120 and ramp == sorted(ramp)

    def test_shift_drag_draws_a_flat_line_at_the_press_height(self):
        song, w, ed, _ = make()
        sweep(ed, w, CHORD, 60, C4_COL, 120, shift=True)
        assert [velocities(w, c)[0] for c in (CHORD, G4_COL, A4_COL, DS4_COL, C4_COL)] == [60] * 5
        assert velocities(w, CHORD) == [60, 60]

    def test_ctrl_changes_nothing_in_the_lane(self):
        song, w, ed, _ = make()
        sweep(ed, w, CHORD, 20, G4_COL, 120, ctrl=True)
        assert velocities(w, CHORD) == [20, 20] and velocities(w, G4_COL) == [120]

    def test_a_press_in_an_empty_column_edits_nothing_and_inserts_nothing(self):
        song, w, ed, _ = make()
        before = len(rhodes(song).notes)
        x, y = lane_point(w, EMPTY, 90)
        ed.press(x, y, LEFT)
        ed.release(x, y, LEFT)
        assert ed.history.depth == 0 and len(rhodes(song).notes) == before
        assert ed._hit(x, y) is None                                  # grid gestures never see the lane

    def test_a_click_that_changes_nothing_is_not_an_edit(self):
        song, w, ed, _ = make()
        x, y = lane_point(w, G4_COL, 104)                              # G4 is 104 already
        ed.press(x, y, LEFT)
        ed.release(x, y, LEFT)
        assert ed.history.depth == 0

    def test_only_notes_starting_in_the_column_are_touched(self):
        """The 460-tick C4 sustains through cells 129-131; a press there
        belongs to no bar and sets nothing."""
        song, w, ed, _ = make()
        x, y = lane_point(w, CHORD + 2, 20)
        ed.press(x, y, LEFT)
        ed.release(x, y, LEFT)
        assert velocities(w, CHORD) == [88, 88] and ed.history.depth == 0

    def test_a_drag_that_leaves_the_lane_pins_rather_than_breaking(self):
        song, w, ed, _ = make()
        x0, y0 = lane_point(w, CHORD, 60)
        ed.press(x0, y0, LEFT)
        x1, _ = lane_point(w, G4_COL, 60)
        ed.move(x1, -50)                                               # way above the ruler
        ed.release(x1, -50, LEFT)
        assert velocities(w, G4_COL) == [127]

    def test_right_button_in_the_lane_is_nothing(self):
        song, w, ed, _ = make()
        x, y = lane_point(w, G4_COL, 20)
        ed.press(x, y, RIGHT)
        assert ed.gesture is None
        ed.release(x, y, RIGHT)
        assert velocities(w, G4_COL) == [104] and ed.history.depth == 0

    def test_escape_cancels_a_sweep(self):
        song, w, ed, _ = make()
        x0, y0 = lane_point(w, CHORD, 20)
        ed.press(x0, y0, LEFT)
        ed.move(*lane_point(w, C4_COL, 120))
        assert ed.key("escape") and ed.gesture is None
        ed.release(*lane_point(w, C4_COL, 120), LEFT)
        assert ed.history.depth == 0 and velocities(w, CHORD) == [88, 88]

    def test_selection_is_not_needed_and_not_changed(self):
        song, w, ed, _ = make()
        ed.select_all()
        before = set(ed.selection)
        sweep(ed, w, CHORD, 20, G4_COL, 120)
        assert ed.selection == before


class TestPreview:
    def test_the_sweep_previews_bars_where_it_will_set_them(self):
        song, w, ed, _ = make()
        x0, y0 = lane_point(w, CHORD, 20)
        ed.press(x0, y0, LEFT)
        ed.move(*lane_point(w, C4_COL, 120))
        items = ed.preview_items()
        assert items and all(r.role == "lane_preview" for r in items)
        # Only the five columns that have a bar; the twelve empty ones get nothing.
        assert len(items) == 5
        first, last = items[0], items[-1]
        assert first == lane_bar(w.layout, w.first_cell, w.plan.rows, CHORD, 20, "lane_preview")
        assert last == lane_bar(w.layout, w.first_cell, w.plan.rows, C4_COL, 120, "lane_preview")
        assert all(a.y0 > b.y0 for a, b in zip(items, items[1:]))      # a ramp, rising to the right
        ed.cancel()
        assert ed.preview_items() == []

    def test_the_preview_stands_where_the_plan_draws_the_bar(self):
        song, w, ed, _ = make()
        plan = w.build()
        x_g4 = w.layout.x_of(G4_COL, w.first_cell) + 1
        [painted] = [r for r in plan.rects("lane_bar") if r.x0 == x_g4]
        x, y = lane_point(w, G4_COL, 104)
        ed.press(x, y, LEFT)
        [preview] = ed.preview_items()
        assert (preview.x0, preview.x1, preview.y1) == (painted.x0, painted.x1, painted.y1)
        assert abs(preview.y0 - painted.y0) <= 1

    def test_an_empty_column_previews_nothing(self):
        song, w, ed, _ = make()
        ed.press(*lane_point(w, EMPTY, 90), LEFT)
        assert ed.preview_items() == []


class TestKeys:
    def test_ctrl_up_and_down_nudge_the_selection_by_five(self):
        song, w, ed, _ = make()
        g4 = next(n for n in rhodes(song).notes if n.pitch == 67 and n.start == BAR9 + BEAT)
        assert ed.key("up", ctrl=True) is False                        # nothing selected: not ours
        ed.selection.add(g4.nid)
        assert ed.key("up", ctrl=True)
        assert rhodes(song).note_by_id(g4.nid).velocity == 104 + VELOCITY_STEP
        assert ed.key("down", ctrl=True) and ed.key("down", ctrl=True)
        assert rhodes(song).note_by_id(g4.nid).velocity == 104 - VELOCITY_STEP
        assert rhodes(song).note_by_id(g4.nid).pitch == 67             # velocity, not pitch

    def test_ctrl_shift_nudges_by_one(self):
        song, w, ed, _ = make()
        g4 = next(n for n in rhodes(song).notes if n.pitch == 67 and n.start == BAR9 + BEAT)
        ed.selection.add(g4.nid)
        ed.key("up", ctrl=True, shift=True)
        assert rhodes(song).note_by_id(g4.nid).velocity == 105
        ed.key("down", ctrl=True, shift=True)
        assert rhodes(song).note_by_id(g4.nid).velocity == 104

    def test_a_nudge_is_undoable_and_clamps(self):
        song, w, ed, _ = make()
        g4 = next(n for n in rhodes(song).notes if n.pitch == 67 and n.start == BAR9 + BEAT)
        ed.selection.add(g4.nid)
        for _ in range(5):                                             # 104 + 25 would be 129
            ed.key("up", ctrl=True)
        assert rhodes(song).note_by_id(g4.nid).velocity == 127
        assert ed.history.depth == 5
        ed.undo()
        assert rhodes(song).note_by_id(g4.nid).velocity == 124
        ed.key("z", ctrl=True)
        assert rhodes(song).note_by_id(g4.nid).velocity == 119


class TestStatus:
    def test_hovering_a_bar_names_its_velocity_and_where_it_is(self):
        song, w, ed, _ = make()
        w.set_hover(*lane_point(w, G4_COL, 50))
        assert ed.status_line().startswith("vel 104 at 9:2.1")
        assert w.hover is None                                         # no cell outline in the lane

    def test_an_empty_column_says_so(self):
        song, w, ed, _ = make()
        w.set_hover(*lane_point(w, EMPTY, 50))
        assert ed.status_line().startswith("vel - at 9:1.3")

    def test_leaving_the_lane_forgets_it(self):
        song, w, ed, _ = make()
        w.set_hover(*lane_point(w, G4_COL, 50))
        w.set_hover(None, None)
        assert w.lane_hover is None and not ed.status_line().startswith("vel")

    def test_after_a_sweep_the_tail_says_what_landed(self):
        song, w, ed, _ = make()
        sweep(ed, w, CHORD, 20, G4_COL, 120)
        tail = ed.status_tail()
        assert "Set velocities: 3 notes" in tail and "undo: Set velocities" in tail
