"""The paint plan, judged without a window.

Everything here is about the picture the GUI will draw: one rectangle per
note, a head where it starts, the loud note painted loud, the 3/4 bar
narrower, the off-grid flag in the gutter, and nothing painted that the
viewport can't show.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from midiseq.core import io_midi
from midiseq.core.grid import GridView, Zoom
from midiseq.core.render import render
from midiseq.ui_gui import paint
from midiseq.ui_gui.paint import Label, Layout, Rect, hover_plan, plan

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
WIDTH, HEIGHT = 1280, 760


def bluesy(first_bar: int = 9, *, cell_w: int = 14, ghosts: bool = True):
    """Track 2 of the demo file, bars from ``first_bar``, the way the widget
    renders it: whole bars, pitch rows from G#4 (70) down."""
    song = io_midi.load(BLUESY)
    view = GridView(song, Zoom(16))
    first_cell = view.bar_cell_range(view.bars.bar(first_bar))[0]
    layout = Layout(cell_w=cell_w)
    cols = layout.visible_cols(WIDTH)
    last_bar = view.bars.bar_containing(view.cell_to_tick(first_cell + cols - 1)).number
    m = render(song, 2, zoom=Zoom(16), first_bar=first_bar, last_bar=last_bar,
               low_pitch=40, high_pitch=70, ghosts=ghosts)
    p = plan(m, layout, first_cell=first_cell, width=WIDTH, height=HEIGHT)
    return song, view, layout, first_cell, m, p


def note_rects(p):
    return [r for r in p.items if isinstance(r, Rect) and r.role.startswith("note_")]


class TestBoundary:
    def test_the_plan_module_imports_no_toolkit(self):
        source = inspect.getsource(paint)
        imports = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
        assert not any("dearpygui" in ln for ln in imports), imports


class TestNotes:
    def test_one_rectangle_per_note_and_one_head_each(self):
        _, _, _, _, _, p = bluesy()
        # Bars 9-11 hold ten Rhodes notes (the dyad is two); bars 12+ are empty.
        assert len(note_rects(p)) == 10
        assert len(p.rects("head")) == 10

    def test_the_ghost_is_one_rectangle_with_no_head(self):
        _, _, _, _, _, p = bluesy()
        assert len(p.rects("ghost")) == 1
        _, _, _, _, _, quiet = bluesy(ghosts=False)
        assert quiet.rects("ghost") == []

    def test_the_loud_note_is_painted_loud_where_it_belongs(self):
        _, _, layout, first_cell, m, p = bluesy()
        loud = p.rects("note_loud")
        assert len(loud) == 1
        g4_row = next(i for i, r in enumerate(m.rows) if r.pitch == 67)
        assert loud[0].x0 == layout.x_of(first_cell + 4, first_cell)     # bar 9, beat 2
        assert loud[0].y0 == layout.y_of(g4_row) + 1

    def test_the_dyad_shares_a_column(self):
        _, _, layout, first_cell, m, p = bluesy()
        x_beat1 = layout.x_of(first_cell, first_cell)
        at_beat1 = [r for r in note_rects(p) if r.x0 == x_beat1]
        assert len(at_beat1) == 2
        assert at_beat1[0].x1 == at_beat1[1].x1

    def test_a_note_stops_where_it_stops_not_at_the_cell_edge(self):
        # The C4 in bar 9 is 460 ticks: it ends 100 ticks into its fourth cell.
        _, _, layout, first_cell, m, p = bluesy()
        c4_row = next(i for i, r in enumerate(m.rows) if r.pitch == 60)
        y = layout.y_of(c4_row) + 1
        c4 = next(r for r in note_rects(p) if r.y0 == y and r.x0 == layout.x_of(first_cell, first_cell))
        full = layout.x_of(first_cell + 3, first_cell) + layout.cell_w - 1
        assert c4.x1 < full
        assert c4.x1 > layout.x_of(first_cell + 3, first_cell)


class TestRulerAndGutter:
    def test_bar_numbers_and_the_meter_only_where_it_changes(self):
        _, _, _, _, _, p = bluesy()
        texts = [lb.text for lb in p.labels("ruler_text")]
        assert texts[:4] == ["9", "10", "11 (3/4)", "12"]

    def test_a_window_starting_in_three_four_says_so(self):
        _, _, _, _, _, p = bluesy(first_bar=12)
        assert [lb.text for lb in p.labels("ruler_text")][:2] == ["12 (3/4)", "13"]

    def test_the_three_four_bar_is_narrower(self):
        _, _, layout, first_cell, _, p = bluesy()
        xs = sorted(ln.x0 for ln in p.by_role("bar_line") if ln.y0 < layout.ruler_h)
        widths = [b - a for a, b in zip(xs, xs[1:])]
        assert widths[:3] == [16 * layout.cell_w, 16 * layout.cell_w, 12 * layout.cell_w]

    def test_the_off_grid_flag_sits_in_the_gutter_on_the_right_row(self):
        _, _, layout, _, m, p = bluesy()
        flags = p.labels("off_grid")
        assert [f.text for f in flags] == ["~"]
        f4_row = next(i for i, r in enumerate(m.rows) if r.pitch == 65)
        assert flags[0].y == layout.y_of(f4_row)
        assert flags[0].x < layout.gutter_w

    def test_accidental_rows_are_shaded(self):
        _, _, layout, _, m, p = bluesy()
        shaded = {r.y0 for r in p.rects("accidental_row")}
        for i, row in enumerate(m.rows[:p.rows]):
            assert (layout.y_of(i) in shaded) == row.accidental


class TestVirtualisation:
    def test_nothing_is_painted_past_the_right_edge(self):
        _, _, layout, _, _, p = bluesy()
        right = layout.gutter_w + p.cols * layout.cell_w
        for item in p.items:
            if isinstance(item, Rect) and item.role != "bg":
                assert item.x1 <= right
            elif isinstance(item, Label):
                assert item.x < right

    def test_only_the_rows_that_fit_are_painted(self):
        _, _, layout, _, m, p = bluesy()
        assert p.rows == min(m.height, layout.visible_rows(HEIGHT))
        assert len(p.labels("row_label")) + len(p.labels("row_label_accidental")) == p.rows

    def test_the_lane_bar_for_the_loudest_note_is_the_tallest(self):
        _, _, layout, first_cell, _, p = bluesy()
        bars = p.rects("lane_bar")
        tallest = min(bars, key=lambda r: r.y0)
        assert tallest.x0 == layout.x_of(first_cell + 4, first_cell) + 1


class TestHitTesting:
    def test_round_trip_through_cell_rect(self):
        layout = Layout(cell_w=14)
        first_cell = 128
        for row, cell in [(0, 128), (7, 140), (30, 200)]:
            x0, y0, x1, y1 = layout.cell_rect(row, cell, first_cell)
            assert layout.hit_test(x0 + 1, y0 + 1, first_cell, 40, WIDTH) == (row, cell)
            assert layout.hit_test(x1, y1, first_cell, 40, WIDTH) == (row, cell)

    def test_outside_the_grid_is_nobody(self):
        layout = Layout()
        assert layout.hit_test(3, 100, 0, 40, WIDTH) is None            # gutter
        assert layout.hit_test(200, 3, 0, 40, WIDTH) is None            # ruler
        assert layout.hit_test(200, layout.ruler_h + 40 * layout.row_h, 0, 40, WIDTH) is None

    def test_hover_is_one_outline(self):
        layout = Layout()
        items = hover_plan(layout, 10, 3, 14)
        assert len(items) == 1 and isinstance(items[0], Rect)
        assert items[0].role == "hover" and not items[0].filled
        assert (items[0].x0, items[0].y0, items[0].x1, items[0].y1) == layout.cell_rect(3, 14, 10)


class TestLayout:
    def test_cell_width_is_clamped(self):
        assert Layout().with_cell_w(1).cell_w == Layout.MIN_CELL_W
        assert Layout().with_cell_w(999).cell_w == Layout.MAX_CELL_W
        assert Layout().with_cell_w(20).cell_w == 20
