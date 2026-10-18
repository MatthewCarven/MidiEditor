"""Playback in the window, driven with coordinates, key names and a fake
clock - no window, no sound.

The window's own job is thin: translate the mouse, forward key names, call
``Editor.frame()`` once a frame and copy ``transport_state()`` into its
widgets. Everything that could be wrong lives below it and is pinned here.
"""

from __future__ import annotations

from pathlib import Path

from midiseq.audio.sink_null import NullSink
from midiseq.core import io_midi
from midiseq.ui_gui.editor import LEFT, RIGHT, Editor, TransportState, bpm_text
from midiseq.ui_gui.grid import GridWidget
from midiseq.ui_gui.paint import DEFAULT_THEME, Line

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT
TPC = 120                      # 1/16 at 480 ppq
BAR9 = 8 * BAR
G4 = (67, BAR9 + BEAT)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(bar: int = 9):
    song = io_midi.load(BLUESY)
    w = GridWidget(song, 2)
    w.resize(1280, 760)
    w.go_to_bar(bar)
    w.build()
    clock = FakeClock()
    sink = NullSink(clock=clock)
    return song, w, Editor(w, sink), sink, clock


def ruler_point(w, col):
    """A point in the ruler above column ``col``."""
    return w.layout.x_of(col, w.first_cell) + 3, w.layout.ruler_h // 2


def row_of(w, pitch):
    return next(i for i, r in enumerate(w.matrix.rows) if r.pitch == pitch)


def centre(w, row, col):
    x0, y0, x1, y1 = w.layout.cell_rect(row, col, w.first_cell)
    return (x0 + x1) / 2, (y0 + y1) / 2


def advance_to(ed, clock, tick):
    """Move the fake clock so the rolling transport reads ``tick``."""
    t = ed.transport
    clock.advance(t.seconds_at(tick) - t.seconds_at(t.position()))


class TestSeek:
    def test_a_click_in_the_ruler_moves_the_playhead_to_that_cell(self):
        _, w, ed, _, _ = make()
        col = w.first_cell + 8
        x, y = ruler_point(w, col)
        ed.press(x, y, LEFT)
        ed.release(x, y, LEFT)
        assert ed.transport.playhead == w.tick_at_col(col)
        assert ed.gesture is None and ed.history.depth == 0
        assert ed.playhead_dirty

    def test_the_gutter_and_a_right_click_are_not_seeks(self):
        _, w, ed, _, _ = make()
        ed.press(3, w.layout.ruler_h // 2, LEFT)
        ed.press(*ruler_point(w, w.first_cell + 8), RIGHT)
        assert ed.transport.playhead == 0 and ed.gesture is None

    def test_ruler_hit_stops_at_the_last_visible_column(self):
        _, w, ed, _, _ = make()
        right = w.layout.gutter_w + w.visible_cols * w.layout.cell_w
        assert ed.ruler_hit(right - 1, 5) == w.tick_at_col(w.first_cell + w.visible_cols - 1)
        assert ed.ruler_hit(right, 5) is None
        assert ed.ruler_hit(right - 1, w.layout.ruler_h) is None   # that's the grid

    def test_seeking_while_rolling_restarts_from_there(self):
        _, w, ed, sink, clock = make()
        ed.seek(BAR9)
        assert ed.transport.play()
        clock.advance(1.0)
        col = w.first_cell + 32                                  # bar 11
        x, y = ruler_point(w, col)
        ed.press(x, y, LEFT)
        assert ed.transport.rolling
        assert [s.start_tick for s in sink.started] == [BAR9, w.tick_at_col(col)]
        assert ed.transport.position() == w.tick_at_col(col)

    def test_seek_after_the_schedule_ran_out_sticks(self):
        song, _, ed, _, clock = make()
        ed.transport.play(0)
        clock.advance(60.0)                                      # well past the end
        assert not ed.transport.rolling
        ed.seek(BAR9)
        assert ed.transport.position() == BAR9                   # not the end tick


class TestKeys:
    def test_space_toggles(self):
        _, _, ed, _, _ = make()
        assert ed.key("space") and ed.transport.rolling
        assert ed.key("space") and not ed.transport.rolling

    def test_shift_space_plays_from_the_left_edge_of_the_view(self):
        _, w, ed, sink, _ = make()
        assert ed.key("space", shift=True)
        assert ed.transport.rolling and sink.schedule.start_tick == BAR9

    def test_home_rewinds_and_scrolls(self):
        _, w, ed, _, _ = make()
        ed.seek(BAR9)
        assert ed.key("home")
        assert ed.transport.playhead == 0 and w.first_cell == 0

    def test_home_while_rolling_restarts_from_bar_one(self):
        _, w, ed, sink, _ = make()
        ed.transport.play(BAR9)
        ed.key("home")
        assert ed.transport.rolling and sink.schedule.start_tick == 0

    def test_escape_stops_and_still_deselects(self):
        song, w, ed, _, clock = make()
        x, y = centre(w, row_of(w, 67), w.view.tick_to_cell(G4[1]))
        ed.press(x, y, LEFT)
        ed.release(x, y, LEFT)
        assert ed.selection
        ed.transport.play(BAR9)
        clock.advance(0.5)
        assert ed.key("escape")
        assert not ed.transport.rolling and not ed.selection
        assert ed.transport.playhead > BAR9                      # stopped where it was, not rewound

    def test_rewind_is_bar_one_rolling_or_not(self):
        _, _, ed, sink, _ = make()
        ed.seek(BAR9)
        ed.rewind()
        assert ed.transport.playhead == 0
        ed.transport.play(BAR9)
        ed.rewind()
        assert ed.transport.rolling and sink.schedule.start_tick == 0


class TestFollow:
    def test_the_page_turns_when_the_playhead_passes_the_right_edge(self):
        _, w, ed, _, clock = make(bar=1)
        ed.transport.play(0)
        right_edge = w.tick_at_col(w.first_cell + w.visible_cols)
        advance_to(ed, clock, right_edge - TPC)
        ed.frame()
        assert w.first_cell == 0                                 # still on screen: hold still
        advance_to(ed, clock, right_edge + TPC)
        tick = ed.frame()
        bar = w.view.bars.bar_containing(tick)
        assert w.first_cell == w.view.bar_cell_range(bar)[0]
        assert w.first_cell > 0 and w.dirty
        assert w.tick_visible(tick)

    def test_frame_reports_when_the_playhead_moved(self):
        _, _, ed, _, clock = make()
        ed.frame()
        ed.playhead_items()
        assert not ed.playhead_dirty
        ed.frame()
        assert not ed.playhead_dirty                             # stopped: nothing moved
        ed.transport.play(BAR9)
        clock.advance(0.1)
        ed.frame()
        assert ed.playhead_dirty

    def test_a_stopped_playhead_is_not_followed(self):
        _, w, ed, _, _ = make(bar=1)
        ed.seek(BAR9 + 2 * BAR)
        ed.frame()
        assert w.first_cell == 0


class TestPicture:
    def test_the_playhead_is_one_line_from_ruler_to_lane_at_the_tick(self):
        _, w, ed, _, _ = make()
        tick = BAR9 + BEAT + TPC // 2                            # half a cell past a boundary
        ed.seek(tick)
        [line] = ed.playhead_items()
        assert isinstance(line, Line) and line.role == "playhead"
        assert line.x0 == line.x1 == w.layout.x_of_tick(tick, w.first_cell, TPC)
        assert line.x0 == w.layout.x_of(w.first_cell + 4, w.first_cell) + w.layout.cell_w / 2
        assert line.y0 == 0
        assert line.y1 == w.layout.ruler_h + w.plan.rows * w.layout.row_h + w.layout.lane_h

    def test_off_screen_is_not_drawn(self):
        _, w, ed, _, _ = make()
        ed.seek(0)                                               # bar 1; the view is at bar 9
        assert ed.playhead_items() == []
        ed.seek(w.tick_at_col(w.first_cell + w.visible_cols))
        assert ed.playhead_items() == []

    def test_the_role_is_in_the_theme(self):
        assert "playhead" in DEFAULT_THEME


class TestTransportBar:
    def test_the_state_helpers(self):
        song, _, ed, _, _ = make()
        ed.seek(BAR9 + BEAT)
        s = ed.transport_state()
        assert isinstance(s, TransportState)
        assert s == TransportState(False, "Play", "9:2.1", "96 bpm", True)
        ed.transport.play()
        s = ed.transport_state()
        assert s.rolling and s.play_label == "Stop" and s.position == "9:2.1"
        ed.toggle_audition()
        assert ed.transport_state().audition is False

    def test_the_tempo_is_read_at_the_playhead(self):
        song, _, ed, _, _ = make()
        song.tempo_map.append(type(song.tempo_map[0])(BAR9, 500_000))
        ed.seek(0)
        assert ed.transport_state().tempo == "96 bpm"
        ed.seek(BAR9)
        assert ed.transport_state().tempo == "120 bpm"

    def test_bpm_text(self):
        assert bpm_text(120.0) == "120 bpm"
        assert bpm_text(120.00002) == "120 bpm"
        assert bpm_text(99.5) == "99.5 bpm"

    def test_the_status_line_reads_as_playing(self):
        _, w, ed, _, clock = make()
        assert not ed.status_line().startswith(">")
        ed.transport.play(BAR9)
        clock.advance(0.1)
        line = ed.status_line()
        assert line.startswith("> 9:1.") and "trk 2" in line
        ed.transport.stop()
        assert not ed.status_line().startswith(">")


class TestEditingWhilePlaying:
    def test_commands_land_and_the_transport_keeps_rolling(self):
        song, w, ed, sink, clock = make()
        ed.transport.play(BAR9)
        clock.advance(0.25)
        row, col = row_of(w, 71), w.first_cell + 8
        x, y = centre(w, row, col)
        ed.press(x, y, LEFT)
        ed.release(x, y, LEFT)
        assert ed.history.depth == 1
        assert ed.transport.rolling
        assert sink.log == []                                    # no audition over the song
        assert len(sink.started) == 1                            # the schedule is not rewritten

    def test_swapping_the_sink_stops_the_old_one(self):
        _, _, ed, old, _ = make()
        ed.transport.play(BAR9)
        new = NullSink(clock=FakeClock())
        ed.set_sink(new)
        assert not old.rolling and not ed.transport.rolling
        assert ed.sink is new and ed.transport.sink is new
        ed.transport.play(BAR9)
        assert new.rolling and len(old.started) == 1 and len(new.started) == 1
