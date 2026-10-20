"""The keyboard model, driven by key names and no terminal.

    on a note                      on empty space
  Enter        select + play     Enter        insert one cell + play
  S            add to set
  Del          delete

Everything that acts on notes acts on the selection, or else the note
under the cursor; the arrows move the cursor and wear Shift to move notes.
Chords Windows Terminal can't deliver have plain spellings (S, Shift+P,
X / C / V / D, [ and ]); the chords stay as aliases.
"""

from __future__ import annotations

from pathlib import Path

from midiseq.audio.sink_null import NullSink
from midiseq.core import edit, io_midi
from midiseq.ui_tui.editor import Editor, normalise_key
from midiseq.ui_tui.view import TextView

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT
TPC = 120
BAR9 = 8 * BAR
BAR11 = 10 * BAR
BAR12 = BAR11 + 3 * BEAT               # bar 11 is 3/4
G4 = (67, BAR9 + BEAT)                 # 460 ticks, velocity 104
A4 = (69, BAR9 + 2 * BEAT)             # 460 ticks, velocity 92
C4 = (60, BAR9)                        # the chord: C4 + E4
E4 = (64, BAR9)
F4 = (65, BAR11 + 37)                  # off grid


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(bar: int = 9, **kw):
    song = io_midi.load(BLUESY)
    grid = TextView(song, 2)
    grid.resize(100, 26)
    grid.go_to_bar(bar)
    clock = FakeClock()
    sink = NullSink(clock=clock)
    return song, grid, Editor(grid, sink, **kw), sink, clock


def rhodes(song):
    return song.tracks[2]


def find(song, pitch, start):
    return next((n for n in rhodes(song).notes if n.pitch == pitch and n.start == start), None)


def at(grid, pitch, tick):
    grid.put_cursor(pitch, grid.view.tick_to_cell(tick))


def keys(ed, *names):
    for name in names:
        ed.key(name)


class TestEnter:
    def test_enter_on_empty_inserts_one_cell_and_plays_it(self):
        song, g, ed, sink, _ = make()
        at(g, 70, BAR9)
        assert ed.key("enter")
        n = find(song, 70, BAR9)
        assert n is not None and n.duration == TPC and n.velocity == 80
        assert sink.last is not None and sink.last.notes[0].pitch == 70
        assert not ed.selection                       # inserting doesn't select, as a click doesn't
        assert ed.message == "Add note: 1 note"

    def test_enter_on_a_note_selects_just_it_and_plays_it(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("enter")
        assert ed.selection == {find(song, *G4).nid}
        assert sink.last.notes[0].pitch == 67
        at(g, *A4)
        ed.key("enter")
        assert ed.selection == {find(song, *A4).nid}

    def test_shift_enter_toggles_membership(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("enter")
        at(g, *A4)
        ed.key("shift+enter")
        assert ed.selection == {find(song, *G4).nid, find(song, *A4).nid}
        ed.key("s")                                   # the spelling for terminals without Shift+Enter
        assert ed.selection == {find(song, *G4).nid}
        at(g, 70, BAR9)
        ed.key("shift+enter")                         # nothing there: nothing happens
        assert len(rhodes(song).notes) == 10

    def test_escape_deselects(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        keys(ed, "enter", "escape")
        assert not ed.selection


class TestTheTargetRule:
    def test_delete_takes_the_cursor_note_without_a_selection(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        before = len(rhodes(song).notes)
        sink.clear()
        ed.key("delete")
        assert find(song, *G4) is None and len(rhodes(song).notes) == before - 1
        assert sink.log == []                         # deleting makes no sound

    def test_delete_takes_the_selection_and_leaves_the_cursor_note(self):
        song, g, ed, sink, _ = make()
        at(g, *A4)
        ed.key("enter")
        at(g, *G4)
        ed.key("backspace")
        assert find(song, *A4) is None and find(song, *G4) is not None
        assert not ed.selection

    def test_shift_up_transposes_the_cursor_note_and_the_cursor_follows(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("shift+up")
        assert find(song, 68, G4[1]) is not None and g.cursor_pitch == 68
        ed.key("shift+down")
        assert find(song, *G4) is not None and g.cursor_pitch == 67

    def test_the_cursor_stays_when_the_selection_is_elsewhere(self):
        song, g, ed, sink, _ = make()
        at(g, *A4)
        ed.key("enter")
        at(g, *G4)
        ed.key("shift+up")
        assert find(song, 70, A4[1]) is not None and find(song, *G4) is not None
        assert g.cursor_pitch == 67

    def test_shift_right_nudges_a_cell_and_the_cursor_follows(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("shift+right")
        assert find(song, 67, G4[1] + TPC) is not None
        assert g.cursor_tick == G4[1] + TPC
        ed.key("shift+left")
        assert find(song, *G4) is not None and g.cursor_tick == G4[1]

    def test_the_brackets_move_a_scale_degree(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("right_square_bracket", "]")
        assert find(song, 69, G4[1]) is not None      # G to A in C major
        ed.key("left_square_bracket", "[")
        assert find(song, *G4) is not None
        ed.key("ctrl+shift+up")                       # the window's chord, where it arrives
        assert find(song, 69, G4[1]) is not None

    def test_ctrl_up_and_down_nudge_velocity_by_five(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("ctrl+up")
        assert find(song, *G4).velocity == 109
        keys(ed, "ctrl+down", "ctrl+down")
        assert find(song, *G4).velocity == 99

    def test_plus_and_minus_resize_by_a_cell_never_below_one(self):
        song, g, ed, sink, _ = make()
        at(g, 70, BAR9)
        ed.key("enter")
        keys(ed, "plus", "equals_sign")
        assert find(song, 70, BAR9).duration == 3 * TPC
        keys(ed, "minus", "minus")
        assert find(song, 70, BAR9).duration == TPC
        ed.key("minus")
        assert find(song, 70, BAR9).duration == TPC and ed.message == "Already one cell long"

    def test_q_quantises_the_off_grid_note_onto_the_bar_line(self):
        song, g, ed, sink, _ = make(bar=11)
        at(g, *F4)
        assert "~ off grid" in ed.status_line()
        ed.key("q")
        assert find(song, 65, BAR11) is not None and find(song, *F4) is None
        assert "~ off grid" not in ed.status_line()

    def test_nothing_under_the_cursor_is_nothing_to_do(self):
        song, g, ed, sink, _ = make()
        at(g, 70, BAR9)
        before = [n for n in rhodes(song).notes]
        keys(ed, "delete", "shift+up", "shift+right", "ctrl+up", "plus", "q")
        assert rhodes(song).notes == before and not ed.history.can_undo


class TestUndoAndSelection:
    def test_ctrl_z_and_ctrl_y(self):
        song, g, ed, sink, _ = make()
        at(g, *G4)
        ed.key("shift+up")
        ed.key("ctrl+z")
        assert find(song, *G4) is not None and ed.message == "Undid Transpose · semitones…"
        ed.key("ctrl+y")
        assert find(song, 68, G4[1]) is not None
        ed.key("ctrl+z")
        assert find(song, 68, G4[1]) is None
        ed.key("ctrl+shift+z")                        # redo, the other spelling
        assert find(song, 68, G4[1]) is not None
        keys(ed, "ctrl+z", "ctrl+z")
        assert ed.message == "Nothing to undo"

    def test_ctrl_a_selects_the_track(self):
        song, g, ed, sink, _ = make()
        ed.key("ctrl+a")
        assert ed.selection == {n.nid for n in rhodes(song).notes}
        assert "sel 10" in ed.status_line()

    def test_a_selection_outlives_a_transpose(self):
        song, g, ed, sink, _ = make()
        ed.key("ctrl+a")
        before = set(ed.selection)
        ed.key("shift+up")
        assert ed.selection == before
        assert {n.pitch for n in rhodes(song).notes} == {p + 1 for p in (60, 64, 67, 69, 63, 65)}


class TestTheTransport:
    def test_space_plays_from_the_playhead_and_stops(self):
        song, g, ed, sink, clock = make()
        ed.key("space")
        assert ed.transport.rolling and sink.started[-1].start_tick == 0
        assert ed.status_line().startswith("> 1:1.1  ")
        ed.key("space")
        assert not ed.transport.rolling

    def test_shift_space_plays_from_the_cursor(self):
        song, g, ed, sink, clock = make()
        at(g, *A4)
        ed.key("shift+space")
        assert sink.started[-1].start_tick == A4[1]
        ed.key("escape")                              # Esc stops
        assert not ed.transport.rolling and not ed.selection
        at(g, *G4)
        ed.key("P", "P")                              # Shift+P, for terminals without Shift+Space
        assert ed.transport.rolling and sink.started[-1].start_tick == G4[1]

    def test_p_puts_the_playhead_at_the_cursor(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("p")
        assert ed.transport.playhead == G4[1] and not ed.transport.rolling
        assert ed.picture().playhead_x == g._xs[g._col_index[g.view.tick_to_cell(G4[1])]]

    def test_home_rewinds_view_cursor_and_playhead(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("p")
        ed.key("home")
        assert (g.first_cell, g.cursor_cell, ed.transport.playhead) == (0, 0, 0)
        ed.key("end")
        assert g.cursor_tick == g.view.bars.bar(g.last_bar()).start_tick

    def test_the_page_turns_when_the_playhead_passes_the_right_edge(self):
        song, g, ed, sink, clock = make()
        at(g, 67, BAR9)
        ed.key("shift+space")
        ed.picture()                                  # painted once, playhead at bar 9
        assert not ed.frame()                         # nothing moved since
        edge = g.view.cell_to_tick(g.first_cell + g.visible_cols)
        clock.advance(ed.transport.seconds_at(edge) - ed.transport.seconds_at(BAR9))
        assert ed.frame()
        assert g.first_cell == g.view.bar_cell_range(g.view.bars.bar_containing(edge))[0]
        assert ed.picture().playhead_x is not None

    def test_a_view_scrolled_away_while_rolling_is_yours(self):
        song, g, ed, sink, clock = make()
        at(g, 67, BAR9)
        ed.key("shift+space")
        ed.key("home")                                # look at bar 1 while it rolls
        ed.key("end")                                 # ... and the cursor's own view elsewhere
        anchor = g.first_cell
        edge = g.view.cell_to_tick(g.first_cell + g.visible_cols)
        clock.advance(ed.transport.seconds_at(edge))
        ed.frame()
        assert g.first_cell == anchor

    def test_audition_toggle(self):
        song, g, ed, sink, clock = make()
        ed.key("a")
        assert not ed.transport.audition_enabled and "audition off" in ed.status_line()
        at(g, 70, BAR9)
        sink.clear()
        ed.key("enter")
        assert sink.log == []

    def test_the_silent_reason_stays_in_the_status_line(self):
        song, g, ed, sink, clock = make(sink_note="no MIDI output ports (silent)")
        assert "no MIDI output ports (silent)" in ed.status_line()


class TestTheClipboard:
    def test_copy_and_paste_at_the_cursor(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("enter")
        at(g, *A4)
        ed.key("s")
        ed.key("c")
        assert ed.message == "Copied 2 notes"
        at(g, 60, BAR12)
        ed.key("v")
        assert find(song, 67, BAR12) is not None and find(song, 69, BAR12 + BEAT) is not None
        assert ed.selection == {find(song, 67, BAR12).nid, find(song, 69, BAR12 + BEAT).nid}
        assert ed.message == "Pasted 2 notes at 12:1.1"

    def test_the_cursor_note_is_enough_to_copy(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("ctrl+c")
        assert ed.clipboard == ((67, 0, 460, 104),)

    def test_cut_and_duplicate(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("d")
        assert find(song, 67, G4[1] + 460) is not None
        ed.key("ctrl+z")
        ed.key("x")
        assert find(song, *G4) is None and ed.clipboard == ((67, 0, 460, 104),)
        ed.key("ctrl+v")                              # the window's chord still pastes where it arrives
        assert find(song, *G4) is not None


class TestTheView:
    def test_tab_cycles_tracks(self):
        song, g, ed, sink, clock = make()
        ed.key("ctrl+a")
        ed.key("tab")
        assert g.track == 0 and not ed.selection and ed.message == 'trk 0 "Conductor"'
        ed.key("shift+tab")
        assert g.track == 2

    def test_zoom_and_ghosts(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("full_stop")
        assert str(g.zoom) == "1/32" and g.cursor_tick == G4[1]
        ed.key("comma")
        assert str(g.zoom) == "1/16"
        ed.key("g")
        assert not g.ghosts and "ghosts off" in ed.status_line()

    def test_arrows_move_the_cursor_and_the_picture_follows(self):
        song, g, ed, sink, clock = make()
        at(g, 60, BAR9)
        keys(ed, "right", "right", "up", "up", "up", "up", "up", "up", "up")
        assert (g.cursor_pitch, g.cursor_tick) == (67, BAR9 + 2 * TPC)
        keys(ed, "ctrl+right", "pagedown")
        assert (g.cursor_pitch, g.cursor_tick) == (55, BAR9 + BAR)
        assert ed.picture().cursor is not None
        assert "G3" in ed.status_line()

    def test_question_mark_shows_the_keys_and_any_key_puts_them_away(self):
        song, g, ed, sink, clock = make()
        cell = g.cursor_cell
        assert ed.key("question_mark", "?") and ed.help_open
        assert ed.key("right") and not ed.help_open
        assert g.cursor_cell == cell                  # the key that closed the table did nothing else
        ed.run_key("app.help")
        assert ed.help_open

    def test_unknown_keys_are_not_handled(self):
        song, g, ed, sink, clock = make()
        assert not ed.key("f12")
        assert not ed.key("F", "F")                   # shift+f means nothing
        g.put_cursor(pitch=100)
        assert ed.key("f") and g.top_pitch == rhodes(song).pitch_range()[1] + 2

    def test_normalise_key(self):
        assert normalise_key("A", "A") == "shift+a"
        assert normalise_key("shift+ctrl+up") == "ctrl+shift+up"
        assert normalise_key("colon", ":") == "colon"
        assert normalise_key("plus", "+") == "plus"


class TestThePalette:
    def test_colon_opens_and_enter_runs_the_first_match(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("enter")
        at(g, *A4)
        ed.key("shift+enter")
        ed.key("colon", ":")
        assert ed.palette.is_open and ed.palette.line == ":"
        for ch in "inv":
            ed.key(ch, ch)
        assert [e.label for e, _ in ed.palette.shown(8)] == ["Invert…"]
        ed.key("enter")
        assert ed.palette.prompting and ed.palette.line.startswith("Invert - Axis note")
        ed.key("enter")                               # blank: the midpoint
        assert not ed.palette.is_open
        assert find(song, 69, G4[1]) is not None and find(song, 67, A4[1]) is not None

    def test_a_command_with_two_questions_asks_both(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("colon", ":")
        for ch in "tsd":
            ed.key(ch, ch)
        assert ed.palette.matches[0].label == "Transpose · scale degrees…"
        ed.key("enter")
        assert "Scale degrees" in ed.palette.caption and "[1]" in ed.palette.caption
        ed.key("2", "2")
        ed.key("enter")
        assert "Key" in ed.palette.caption
        for ch in "A minor":
            ed.key("space" if ch == " " else ch, ch)
        ed.key("enter")
        assert find(song, 71, G4[1]) is not None      # G up two degrees of A minor is B
        assert (ed.key_root, ed.key_scale) == (9, "minor")

    def test_a_bad_value_comes_back_as_a_sentence(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("colon", ":")
        for ch in "semi":
            ed.key(ch, ch)
        ed.key("enter")
        for ch in "abc":
            ed.key(ch, ch)
        ed.key("enter")
        assert ed.message.startswith("Transpose · semitones: invalid literal")
        assert find(song, *G4) is not None and not ed.palette.is_open

    def test_nothing_matched_keeps_it_open_and_escape_closes(self):
        song, g, ed, sink, clock = make()
        ed.key("colon", ":")
        for ch in "zzz":
            ed.key(ch, ch)
        ed.key("enter")
        assert ed.palette.is_open and ed.palette.matches == []
        ed.key("backspace")
        assert ed.palette.text == "zz"
        ed.key("escape")
        assert not ed.palette.is_open

    def test_the_arrows_pick_a_match(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("colon", ":")
        ed.key("down")
        ed.key("down")
        third = ed.palette.matches[2]
        assert [e for e, hl in ed.palette.shown(8) if hl] == [third]
        ed.key("up")
        assert ed.palette.highlight == 1

    def test_the_terminals_own_entries(self):
        song, g, ed, sink, clock = make()
        ed.key("colon", ":")
        for ch in "go to":
            ed.key("space" if ch == " " else ch, ch)
        ed.key("enter")
        for ch in "11":
            ed.key(ch, ch)
        ed.key("enter")
        assert g.first_cell == g.view.bar_cell_range(g.view.bars.bar(11))[0]
        ed.key("colon", ":")
        for ch in "track":
            ed.key(ch, ch)
        ed.key("enter")
        ed.key("1", "1")
        ed.key("enter")
        assert g.track == 1
        assert ed.run_key("view.track", {"track": "9"}).startswith("Track: no track 9")

    def test_nothing_to_act_on_says_so(self):
        song, g, ed, sink, clock = make()
        at(g, 70, BAR9)
        assert ed.run_key("xform.retrograde") == ("Retrograde: nothing selected, and no note "
                                                  "under the cursor")

    def test_the_palette_eats_every_key_while_open(self):
        song, g, ed, sink, clock = make()
        at(g, *G4)
        ed.key("colon", ":")
        assert ed.key("shift+up") and find(song, *G4) is not None
        assert ed.key("ctrl+c") and not ed.clipboard


class TestFilesAndQuitting:
    def test_ctrl_s_saves_the_edit(self, tmp_path):
        path = tmp_path / "bluesy.mid"
        song, g, ed, sink, clock = make(path=path)
        at(g, 70, BAR9)
        ed.key("enter")
        assert ed.dirty_since_save and ed.title() == "midiseq - bluesy.mid*"
        ed.key("ctrl+s")
        assert ed.message == "saved bluesy.mid" and not ed.dirty_since_save
        again = io_midi.load(path)
        assert any(n.pitch == 70 and n.start == BAR9 for n in again.tracks[2].notes)

    def test_save_as_asks_for_a_name(self, tmp_path):
        song, g, ed, sink, clock = make()
        ed.key("ctrl+s")
        assert ed.message.startswith("No file name")
        ed.key("ctrl+shift+s")
        assert ed.palette.prompting
        for ch in str(tmp_path / "out"):
            ed.key(ch, ch)
        ed.key("enter")
        assert ed.path == tmp_path / "out.mid" and ed.path.exists()

    def test_quitting_with_unsaved_changes_takes_two_presses(self):
        song, g, ed, sink, clock = make()
        ed.key("ctrl+q")
        assert ed.want_quit
        song, g, ed, sink, clock = make()
        at(g, 70, BAR9)
        ed.key("enter")
        ed.key("ctrl+q")
        assert not ed.want_quit and ed.message.startswith("Unsaved changes")
        ed.key("right")                               # anything else disarms it
        ed.key("ctrl+q")
        assert not ed.want_quit
        ed.key("ctrl+q")
        assert ed.want_quit


class TestEditsWhileRolling:
    def test_an_edit_while_rolling_reschedules_from_the_position(self):
        song, g, ed, sink, clock = make()
        ed.transport.play(BAR9)
        clock.advance(0.5)
        ed.run(edit.DeleteNotes({find(song, *G4).nid}))
        assert ed.transport.rolling and len(sink.started) == 2
        assert sink.schedule.start_tick > BAR9


class TestTheTerminalCatchesUp:
    """New, Open, the MIDI output and velocity by one, from the TODO (2026-10-20)."""

    def test_new_and_open_rebuild_the_picture_and_guard_unsaved_work(self):
        song, g, ed, sink, clock = make()
        ed.run(edit.DeleteNotes({find(song, *G4).nid}))
        assert ed.dirty_since_save
        assert "again to discard" in ed.run_key("file.new")       # the first run warns
        assert ed.song is song
        ed.run_key("file.new")                                   # the same action again discards
        assert ed.song is not song and not ed.dirty_since_save and ed.path is None
        assert ed.grid.width == g.width and ed.grid.zoom == g.zoom and ed.transport.song is ed.song
        assert ed.run_key("file.open", {"path": str(BLUESY)}) == "opened bluesy.mid"
        assert ed.path == BLUESY and len(ed.song.tracks) == len(song.tracks)
        assert "no such file" in ed.run_key("file.open", {"path": "nowhere.mid"})

    def test_a_different_action_between_disarms_the_discard(self):
        song, g, ed, sink, clock = make()
        ed.run(edit.DeleteNotes({find(song, *G4).nid}))
        ed.run_key("file.new")
        ed.run_key("edit.deselect")
        assert "again to discard" in ed.run_key("file.new")       # warned again: the arming was dropped

    def test_alt_up_and_down_move_velocity_by_one(self):
        song, g, ed, sink, clock = make()
        before = find(song, *G4).velocity
        at(g, *G4)
        assert ed.key("alt+up")
        assert find(song, *G4).velocity == before + 1
        ed.key("alt+down")
        assert find(song, *G4).velocity == before

    def test_the_output_can_be_switched_from_the_palette_while_rolling(self, monkeypatch):
        from midiseq.ui_tui import editor as module
        song, g, ed, sink, clock = make()
        ed.transport.play(BAR9)
        new = NullSink(clock=clock)
        monkeypatch.setattr(module, "open_sink", lambda name: (new, f"fake {name}"))
        assert ed.run_key("audio.output", {"port": "fake"}) == "MIDI out: fake fake"
        assert ed.sink is new and ed.transport.sink is new and ed.transport.rolling
        assert not sink.rolling                                  # the old one let go
        assert ed.run_key("audio.output", {"port": "silent"}) == "MIDI out: silent"
        assert ed.silent and ed.transport.rolling


class TestLoop:
    def test_l_loops_the_cursor_bar_and_the_palette_has_it(self):
        song, g, ed, sink, clock = make()
        at(g, 60, BAR9 + BEAT)
        assert ed.key("l") and ed.transport.loop == (BAR9, BAR9 + BAR)
        assert "loop bar 9" in ed.status_tail()
        assert ed.run_key("transport.loop") == "Loop off" and ed.transport.loop is None
