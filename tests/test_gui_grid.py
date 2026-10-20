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

    def test_follow_turns_the_page_to_the_playheads_bar(self):
        w = widget()
        w.dirty = False
        on_screen = w.view.cell_to_tick(w.first_cell + w.visible_cols - 1)
        assert w.follow(on_screen) is False and not w.dirty
        before = w.view.cell_to_tick(w.first_cell - 1)
        assert w.follow(before) is False                 # off to the left is your business
        past = w.view.cell_to_tick(w.first_cell + w.visible_cols) + 7
        assert w.follow(past) is True and w.dirty
        bar = w.view.bars.bar_containing(past)
        assert w.first_cell == w.view.bar_cell_range(bar)[0]
        assert w.tick_visible(past)

    def test_follow_copes_with_a_bar_wider_than_the_window(self):
        w = widget()
        w.resize(60 + 10 * 14, 760)                       # ten cells: under a bar
        past = w.view.cell_to_tick(w.first_cell + 12)
        assert w.follow(past)
        assert w.tick_visible(past)


def test_beats_text():
    assert beats_text(480, 480) == "1 beat"
    assert beats_text(960, 480) == "2 beats"
    assert beats_text(460, 480) == "0.96 beats"
    assert beats_text(0, 480) == "0 beats"


def test_a_drum_track_labels_its_rows_by_name_and_the_gutter_fits():
    from midiseq.core.model import Song, Track
    song = Song(ppq=480)
    piano, drums = Track(name="piano", channel=0), Track(name="drums", channel=9)
    piano.notes.append(song.make_note(60, 0, 480, 80))
    drums.notes.append(song.make_note(38, 0, 120, 100))                  # Acoustic Snare
    song.tracks.extend([piano, drums])
    song.normalise()
    w = GridWidget(song, 1)
    w.resize(800, 600)
    w.build()
    labels = {r.pitch: r.label for r in w.matrix.rows}
    assert labels[38] == "Snr" and labels[36] == "Kick"
    assert max(len(l) for l in labels.values()) * 7 + 14 <= w.layout.gutter_w   # 12 px font, roughly
    w.set_track(0)
    w.build()
    assert {r.pitch: r.label for r in w.matrix.rows}[60] == "C4"


def test_the_widget_draws_an_overview_once_the_song_outgrows_the_window():
    from pathlib import Path
    from midiseq.core import io_midi
    song = io_midi.load(Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid")
    w = GridWidget(song, 2)
    w.resize(800, 600)
    w.go_to_bar(9)
    w.build()
    roles = [item.role for item in w.overview_items(0)]
    assert roles == ["overview", "overview_view", "overview_playhead"]
