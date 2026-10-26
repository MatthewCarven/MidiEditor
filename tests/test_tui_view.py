"""The terminal's picture, judged as text with no terminal.

The view renders through the same ``render.py`` and ``glyphs.py`` as
``seq cat``, so the grid is the README's picture; what is tested here is
what the terminal adds - a width to fit, a cursor the view follows, a
playhead column, and the roles the widget colours by.
"""

from __future__ import annotations

from pathlib import Path

from midiseq.core import io_midi
from midiseq.core.grid import Zoom
from midiseq.core.render import Profile
from midiseq.ui_tui.view import HEADER_LINES, LANE_LINES, TextView

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT
TPC = 120                      # 1/16 at 480 ppq
BAR9 = 8 * BAR
BAR11 = 10 * BAR
G4 = (67, BAR9 + BEAT)         # the loud quarter on beat 2
F4 = (65, BAR11 + 37)          # the off-grid note


def make(width: int = 100, height: int = 26, bar: int = 9) -> TextView:
    v = TextView(io_midi.load(BLUESY), 2)
    v.resize(width, height)
    v.go_to_bar(bar)
    return v


def cell(v: TextView, tick: int) -> int:
    return v.view.tick_to_cell(tick)


def row_line(frame, label: str) -> str:
    return next(line for line in frame.lines if line[1:].startswith(label))


class TestThePicture:
    def test_the_picture_is_seq_cats(self):
        v = make()
        f = v.build()
        assert f.lines[0].startswith("    ┌") and "bar 9 (4/4)" in f.lines[0]
        assert "║1 e & a │2 e & a │3 e & a │4 e & a ║" in f.lines[1]
        assert "X──────·" in row_line(f, "G4 ")
        assert "F4~" in "".join(f.lines)            # the off-grid flag, in bar 11
        assert "," in row_line(f, "D4 ")             # the ghost from track 3
        lane = f.lines[HEADER_LINES + v.visible_rows:]
        assert len(lane) == LANE_LINES and any("█" in line for line in lane)

    def test_every_line_is_the_width_and_columns_stop_at_it(self):
        v = make(width=100)
        f = v.build()
        assert all(len(line) == 100 for line in f.lines)
        last = v._xs[-1]
        assert last + 2 <= 100 < last + 4 + 1        # one more cell and its separator can't fit
        assert f.columns == len(v._xs) == len(v.matrix.columns)

    def test_the_3_4_bar_is_narrower(self):
        v = make(bar=11)
        f = v.build()
        ruler = f.lines[1]
        first, second = ruler.index("║"), ruler.index("║", ruler.index("║") + 1)
        assert ruler[first:second].count("│") == 2     # three beats, two separators
        assert "bar 11 (3/4)" in f.lines[0]
        assert "12 (3/4)" not in f.lines[0]            # same meter as the bar before: no label, as in the window

    def test_a_short_terminal_drops_the_lane_before_the_pitches(self):
        v = make(width=60, height=10)
        assert not v.lane_shown
        f = v.build()
        assert len(f.lines) == HEADER_LINES + v.visible_rows == 10


class TestTheCursor:
    def test_the_cursor_is_marked_in_the_text_and_as_a_span(self):
        v = make()
        v.put_cursor(*G4[:1], cell(v, G4[1]))
        f = v.build()
        ci = v._col_index[cell(v, G4[1])]
        x, y = v._xs[ci], HEADER_LINES + v.row_of_pitch(67)
        assert f.cursor == (x, y)
        assert f.lines[0][x] == "▼"
        assert f.lines[y].startswith(">G4 ")
        assert [s for s in f.spans if s.role == "cursor"] == [type(f.spans[0])(y, x, x + 2, "cursor")]

    def test_the_cursor_starts_on_the_tracks_first_note(self):
        v = TextView(io_midi.load(BLUESY), 2)
        assert (v.cursor_pitch, v.cursor_cell) == (60, cell(v, BAR9))   # C4, bar 9

    def test_moving_right_past_the_edge_turns_the_page_to_the_cursors_bar(self):
        v = make()
        v.put_cursor(cell=v.first_cell + v.visible_cols - 1)
        assert v.first_cell == cell(v, BAR9)
        v.move_cursor(d_cells=1)
        assert v.first_cell == v.view.bar_cell_range(v.view.bars.bar(11))[0]
        assert v.first_cell <= v.cursor_cell < v.first_cell + v.visible_cols

    def test_moving_left_past_the_edge_goes_back_a_bar(self):
        v = make()
        v.move_cursor(d_cells=-1)
        assert v.cursor_cell == cell(v, BAR9) - 1
        assert v.first_cell == v.view.bar_cell_range(v.view.bars.bar(8))[0]

    def test_a_bar_wider_than_the_screen_pages_to_the_cursor(self):
        v = make(width=60)
        v.set_zoom(Zoom(64))                        # a 4/4 bar is 64 cells: 130-odd characters
        v.put_cursor(cell=v.first_cell + v.visible_cols)
        assert v.first_cell == v.cursor_cell

    def test_pitches_scroll_by_the_row(self):
        v = make()
        top = v.top_pitch
        v.put_cursor(pitch=top + 3)
        assert v.top_pitch == top + 3
        v.put_cursor(pitch=top + 3 - v.visible_rows - 1)
        assert v.top_pitch - v.visible_rows + 1 == v.cursor_pitch

    def test_zoom_keeps_the_cursor_on_its_tick(self):
        v = make()
        v.put_cursor(67, cell(v, G4[1]))
        v.zoom_finer()
        assert v.zoom == Zoom(32) and v.cursor_tick == G4[1]
        v.zoom_coarser()
        v.zoom_coarser()
        assert v.zoom == Zoom(8) and v.cursor_tick == G4[1]

    def test_a_bar_motion_stops_at_this_bars_start_first(self):
        v = make()
        v.put_cursor(cell=cell(v, BAR9 + 2 * BEAT))
        v.move_cursor_bars(-1)
        assert v.cursor_tick == BAR9
        v.move_cursor_bars(-1)
        assert v.cursor_tick == BAR9 - BAR
        v.move_cursor_bars(2)
        assert v.cursor_tick == BAR9 + BAR

    def test_set_track_clears_the_selection_and_refits(self):
        v = make()
        v.selection.add(1)
        v.set_track(1)
        assert v.track == 1 and not v.selection
        rng = v.song.tracks[1].pitch_range()
        assert v.top_pitch == rng[1] + 2
        assert v.top_pitch - v.visible_rows < v.cursor_pitch <= v.top_pitch


class TestRoles:
    def test_notes_ghosts_and_accidentals(self):
        v = make()
        f = v.build()
        y_g4 = HEADER_LINES + v.row_of_pitch(67)
        x = v._xs[v._col_index[cell(v, G4[1])]]
        assert any(s.role == "note" and (s.y, s.x0) == (y_g4, x) for s in f.spans)
        y_d4 = HEADER_LINES + v.row_of_pitch(62)
        assert any(s.role == "ghost" and s.y == y_d4 for s in f.spans)
        y_gs4 = HEADER_LINES + v.row_of_pitch(68)
        assert any(s.role == "accidental" and (s.y, s.x0, s.x1) == (y_gs4, 0, 100) for s in f.spans)
        assert not any(s.role == "accidental" and s.y == y_g4 for s in f.spans)

    def test_a_selected_note_is_selected(self):
        v = make()
        g4 = v.note_at(67, cell(v, G4[1]))
        v.selection.add(g4.nid)
        f = v.build()
        y = HEADER_LINES + v.row_of_pitch(67)
        x = v._xs[v._col_index[cell(v, G4[1])]]
        assert any(s.role == "selected" and (s.y, s.x0) == (y, x) for s in f.spans)

    def test_the_playhead_is_one_column_from_header_to_lane(self):
        v = make()
        v.playhead = G4[1]
        f = v.build()
        x = v._xs[v._col_index[cell(v, G4[1])]]
        heads = [s for s in f.spans if s.role == "playhead"]
        assert f.playhead_x == x
        assert [s.y for s in heads] == list(range(len(f.lines)))
        assert all((s.x0, s.x1) == (x, x + 2) for s in heads)

    def test_a_playhead_off_screen_is_not_drawn(self):
        v = make()
        v.playhead = 0
        f = v.build()
        assert f.playhead_x is None and not any(s.role == "playhead" for s in f.spans)


    def test_the_beginner_profile_is_solfege_in_the_gutter_and_a_shaded_range(self):
        v = make()
        v.set_profile(Profile())
        f = v.build()
        y = lambda pitch: HEADER_LINES + v.row_of_pitch(pitch)                   # noqa: E731
        assert f.lines[y(67)][1:3] == "so" and f.lines[y(60)][1:3] == "DO"      # G4 is so; middle C is home
        assert f.lines[y(68)][1:3] == "  "                                       # G#4 is blank, and still shaded
        assert any(s.role == "accidental" and s.y == y(68) for s in f.spans)
        outside = {s.y for s in f.spans if s.role == "out_of_range"}
        assert y(70) in outside and y(61) in outside and y(69) not in outside and y(62) not in outside
        assert all((s.x0, s.x1) == (0, 100) for s in f.spans if s.role == "out_of_range")
        v.set_profile(None)
        f = v.build()
        assert f.lines[y(67)][1:3] == "G4" and not any(s.role == "out_of_range" for s in f.spans)


class TestFollowAndStatus:
    def test_follow_turns_the_page_only_past_the_right_edge(self):
        v = make()
        assert not v.follow(0)                        # behind: yours to have scrolled ahead
        assert not v.follow(BAR9 + BEAT)
        edge = v.view.cell_to_tick(v.first_cell + v.visible_cols)
        assert v.follow(edge)
        assert v.first_cell == v.view.bar_cell_range(v.view.bars.bar_containing(edge))[0]

    def test_the_status_names_the_square(self):
        v = make()
        v.put_cursor(67, cell(v, G4[1]))
        assert v.status() == ('G4 . MIDI 67 . 9:2.1 . len 0.96 beats . vel 104  |  '
                              'trk 2 "Rhodes" ch1  zoom 1/16')
        v.put_cursor(65, cell(v, F4[1]))
        assert "~ off grid" in v.status()
        v.put_cursor(70, cell(v, BAR9))
        assert v.status().startswith("A#4 . MIDI 70 . 9:1.1  |  ")

    def test_note_at_prefers_the_latest_to_start_when_two_overlap(self):
        v = make()
        song = v.song
        early = song.make_note(67, BAR9 + BEAT - 2 * TPC, 6 * TPC)
        song.tracks[2].notes.append(early)
        song.tracks[2].version += 1
        assert v.note_at(67, cell(v, G4[1])).start == G4[1]
        assert v.note_at(67, cell(v, G4[1]) - 1) is early


def test_hit_maps_a_click_to_a_square_the_ruler_or_nothing():
    from midiseq.ui_tui.view import HEADER_LINES
    v = make()
    v.build()
    assert v.hit(0, 5) == (None, None)                                       # the gutter
    x = v._xs[3]
    assert v.hit(x, HEADER_LINES + 2) == (v.matrix.rows[2].pitch, v.matrix.columns[3].cell)
    assert v.hit(x + 1, HEADER_LINES + 2) == v.hit(x, HEADER_LINES + 2)      # a cell is two characters
    assert v.hit(x, 1) == (None, v.matrix.columns[3].cell)                   # the ruler
    assert v.hit(x, HEADER_LINES + len(v.matrix.rows) + 1) == (None, None)   # the lane
