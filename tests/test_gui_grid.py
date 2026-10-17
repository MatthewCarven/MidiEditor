"""The grid widget's state, driven with no window open."""

from __future__ import annotations

from pathlib import Path

from midiseq.core import io_midi
from midiseq.core.grid import Zoom
from midiseq.ui_gui.grid import GridWidget, beats_text, default_track

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"


def widget(**kw) -> GridWidget:
    w = GridWidget(io_midi.load(BLUESY), 2, **kw)
    w.resize(1280, 760)
    w.go_to_bar(9)
    w.build()
    return w


class TestWidget:
    def test_default_track_is_the_first_with_notes(self):
        song = io_midi.load(BLUESY)
        assert default_track(song) == 1          # 0 is the conductor

    def test_status_names_the_note_under_the_mouse(self):
        w = widget()
        g4_row = next(i for i, r in enumerate(w.matrix.rows) if r.pitch == 67)
        x0, y0, _, _ = w.layout.cell_rect(g4_row, w.first_cell + 4, w.first_cell)
        assert w.set_hover(x0 + 2, y0 + 2)
        s = w.status()
        assert s.startswith("G4 . MIDI 67 . 9:2.1")
        assert "vel 104" in s and "len 0.96 beats" in s
        assert 'trk 2 "Rhodes" ch1' in s

    def test_hover_off_the_grid_is_just_the_tail(self):
        w = widget()
        w.set_hover(3, 3)
        assert w.status().startswith("trk 2")
        assert w.hover_items() == []

    def test_a_ghost_cell_says_so(self):
        w = widget()
        d4_row = next(i for i, r in enumerate(w.matrix.rows) if r.pitch == 62)
        x0, y0, _, _ = w.layout.cell_rect(d4_row, w.first_cell + 6, w.first_cell)
        w.set_hover(x0 + 1, y0 + 1)
        assert "ghost" in w.status()

    def test_musical_zoom_keeps_the_left_edge_on_the_same_tick(self):
        w = widget()
        tick = w.view.cell_to_tick(w.first_cell)
        w.zoom_finer()
        assert w.zoom == Zoom(32)
        assert w.view.cell_to_tick(w.first_cell) == tick
        w.zoom_coarser()
        w.zoom_coarser()
        assert w.zoom == Zoom(8)
        assert w.view.cell_to_tick(w.first_cell) == tick

    def test_pixel_zoom_touches_only_the_cell_width(self):
        w = widget()
        before = (w.width, w.height, w.first_cell, w.top_pitch, w.zoom)
        w.zoom_pixels(+1)
        assert w.layout.cell_w == 18
        assert (w.width, w.height, w.first_cell, w.top_pitch, w.zoom) == before
        for _ in range(20):
            w.zoom_pixels(-1)
        assert w.layout.cell_w == w.layout.MIN_CELL_W

    def test_scrolling_by_bars_lands_on_bar_lines(self):
        w = widget()
        w.scroll_bars(2)
        assert w.view.bars.bar_containing(w.view.cell_to_tick(w.first_cell)).number == 11
        assert w.view.bar_cell_range(w.view.bars.bar(11))[0] == w.first_cell
        w.scroll_bars(-50)
        assert w.first_cell == 0

    def test_switching_track_refits_the_pitch_range(self):
        w = widget()
        w.set_track(1)                                   # the pad: one D4
        assert w.top_pitch == 62 + 2
        w.build()
        assert w.matrix.track_name == "Pad"

    def test_build_records_both_clocks(self):
        w = widget()
        assert w.render_ms >= 0 and w.plan_ms >= 0
        assert w.plan is not None and w.plan.cols == w.visible_cols


def test_beats_text():
    assert beats_text(480, 480) == "1 beat"
    assert beats_text(960, 480) == "2 beats"
    assert beats_text(460, 480) == "0.96 beats"
    assert beats_text(0, 480) == "0 beats"
