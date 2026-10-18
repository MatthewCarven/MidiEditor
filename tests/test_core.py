"""Tests for the design invariants — the things that would be expensive to
discover were wrong at step 4.
"""

from __future__ import annotations

import pytest

from midiseq.audio.sink_null import NullSink
from midiseq.core import edit, io_text
from midiseq.core.glyphs import UNICODE, cell_glyphs, grid_rows, render_text
from midiseq.core.grid import GridView, Zoom
from midiseq.core.history import History
from midiseq.core.model import (
    MIDDLE_C_SCIENTIFIC,
    MIDDLE_C_YAMAHA,
    Song,
    TempoChange,
    TimeSigChange,
    Track,
    is_accidental,
    parse_pitch,
    pitch_name,
)
from midiseq.core.render import render
from midiseq.core.transport import Audition, Transport

PPQ = 480
BEAT = PPQ
BAR = 4 * PPQ


def song_with(notes, ppq=PPQ, channel=0):
    """notes: (pitch, start, duration[, velocity])"""
    song = Song(ppq=ppq)
    track = Track(name="T", channel=channel)
    for spec in notes:
        pitch, start, dur = spec[:3]
        vel = spec[3] if len(spec) > 3 else 80
        track.notes.append(song.make_note(pitch, start, dur, vel))
    song.tracks.append(track)
    song.normalise()
    return song


def row_text(song, pitch, **kw):
    """The glyph row for one pitch, without the gutter."""
    kw.setdefault("zoom", Zoom(16))
    matrix = render(song, 0, low_pitch=pitch, high_pitch=pitch, ghosts=False, **kw)
    line = grid_rows(matrix, UNICODE)[0]
    return line[4:]      # strip the gutter


# --------------------------------------------------------------------------
# The cell grammar — char 1 is the event, char 2 is the tie
# --------------------------------------------------------------------------

class TestCellGrammar:
    def test_quarter_note_fills_the_beat(self):
        song = song_with([(60, 0, BEAT)])
        assert row_text(song, 60).startswith("║x──────·")

    def test_two_eighths(self):
        song = song_with([(60, 0, BEAT // 2), (60, BEAT // 2, BEAT // 2)])
        assert row_text(song, 60).startswith("║x──·x──·")

    def test_four_staccato_sixteenths(self):
        sixteenth = BEAT // 4
        song = song_with([(60, i * sixteenth, sixteenth // 2) for i in range(4)])
        assert row_text(song, 60).startswith("║x·x·x·x·")

    def test_note_ties_over_the_beat(self):
        song = song_with([(60, 0, BEAT * 2)])
        # Carries out of beat 1, releases at the end of beat 2.
        assert row_text(song, 60).startswith("║x───────│───────·")

    def test_sustain_passing_through_a_whole_beat(self):
        song = song_with([(60, 0, BEAT * 3)])
        # index 0 is the bar line, 1-8 is beat 1, 9 is a beat line, 10-17 is beat 2
        assert row_text(song, 60)[10:18] == "────────"

    def test_note_shorter_than_a_cell_still_gets_a_cell(self):
        """Nothing vanishes for being too short to draw — it just releases within
        its square, which is exactly what ``x·`` means."""
        song = song_with([(60, 0, 5)])
        assert row_text(song, 60).startswith("║x·······")

    def test_velocity_bands(self):
        for vel, head in ((40, "o"), (80, "x"), (120, "X")):
            song = song_with([(60, 0, BEAT, vel)])
            assert row_text(song, 60)[1] == head, f"velocity {vel}"

    def test_default_velocity_is_medium_with_room_either_way(self):
        from midiseq.core.model import DEFAULT_VELOCITY
        from midiseq.core.render import velocity_band
        assert velocity_band(DEFAULT_VELOCITY) == "medium"
        assert velocity_band(DEFAULT_VELOCITY - 30) == "soft"
        assert velocity_band(DEFAULT_VELOCITY + 30) == "loud"


# --------------------------------------------------------------------------
# Off-grid — tell the truth loudly
# --------------------------------------------------------------------------

class TestOffGrid:
    def test_off_grid_start_raises_the_flag(self):
        song = song_with([(60, 37, BEAT)])
        matrix = render(song, 0, low_pitch=60, high_pitch=60, ghosts=False)
        assert matrix.rows[0].off_grid_flag

    def test_off_grid_end_does_not(self):
        """A short release is already told honestly by the tie character; flagging
        it too made the warning meaningless."""
        song = song_with([(60, 0, BEAT - 20)])
        matrix = render(song, 0, low_pitch=60, high_pitch=60, ghosts=False)
        assert not matrix.rows[0].off_grid_flag

    def test_zooming_finer_can_make_a_note_honest(self):
        song = song_with([(60, 60, BEAT)])          # a 32nd off the 16th grid
        coarse = render(song, 0, zoom=Zoom(16), low_pitch=60, high_pitch=60, ghosts=False)
        fine = render(song, 0, zoom=Zoom(32), low_pitch=60, high_pitch=60, ghosts=False)
        assert coarse.rows[0].off_grid_flag
        assert not fine.rows[0].off_grid_flag

    def test_quantise_makes_it_true(self):
        song = song_with([(60, 37, BEAT)])
        nid = song.tracks[0].notes[0].nid
        History(song).run(edit.Quantise([nid], grid_ticks=PPQ // 4))
        matrix = render(song, 0, low_pitch=60, high_pitch=60, ghosts=False)
        assert not matrix.rows[0].off_grid_flag


# --------------------------------------------------------------------------
# Musical time — the axis choice that makes tempo free
# --------------------------------------------------------------------------

class TestMusicalTime:
    def test_tempo_change_costs_the_grid_nothing(self):
        a = song_with([(60, 0, BEAT), (62, BEAT, BEAT)])
        b = song_with([(60, 0, BEAT), (62, BEAT, BEAT)])
        b.tempo_map.append(TempoChange(BEAT, 1_000_000))    # half speed mid-bar
        b.normalise()
        assert render_text(render(a, 0, ghosts=False)) == \
               render_text(render(b, 0, ghosts=False))

    def test_meter_change_narrows_the_bar_not_the_cell(self):
        song = song_with([(60, 0, BEAT)])
        song.timesig_map.append(TimeSigChange(BAR, 3, 4))
        song.normalise()
        matrix = render(song, 0, first_bar=1, last_bar=2,
                        low_pitch=60, high_pitch=60, ghosts=False)
        widths = {}
        for col in matrix.columns:
            widths[col.bar] = widths.get(col.bar, 0) + 1
        assert widths[1] == 16       # 4/4 at 1/16
        assert widths[2] == 12       # 3/4 at 1/16 — shorter bar, same cells

    def test_cells_per_beat_follows_the_meter(self):
        """In 6/8 a beat is an eighth, so 1/16 zoom gives two cells, not four.
        Deriving this from the zoom alone would mislabel the ruler."""
        assert Zoom(16).cells_per_beat(4) == 4
        assert Zoom(16).cells_per_beat(8) == 2
        assert Zoom(8).cells_per_beat(4) == 2

    def test_bar_numbering_is_one_based(self):
        song = song_with([(60, BAR * 8, BEAT)])
        view = GridView(song, Zoom(16))
        assert view.bars.bar_containing(BAR * 8).number == 9
        assert view.bars.position(BAR * 8 + BEAT) == "9:2.1"

    def test_zoom_conversions(self):
        song = song_with([(60, 0, BEAT)])
        view = GridView(song, Zoom(16))
        assert view.ticks_per_cell == 120
        assert view.tick_to_cell(360) == 3
        assert view.snap(130) == 120
        assert view.snap(190) == 240

    def test_coarser_than_a_quarter_is_refused(self):
        with pytest.raises(ValueError):
            Zoom(2)


# --------------------------------------------------------------------------
# Pitch labelling — a display offset, nothing more
# --------------------------------------------------------------------------

class TestPitchNames:
    def test_middle_c_under_each_convention(self):
        assert pitch_name(60, MIDDLE_C_SCIENTIFIC) == "C4"
        assert pitch_name(60, MIDDLE_C_YAMAHA) == "C3"

    def test_a440(self):
        assert pitch_name(69, MIDDLE_C_SCIENTIFIC) == "A4"
        assert pitch_name(69, MIDDLE_C_YAMAHA) == "A3"    # the one that surprises people

    def test_round_trip_both_ways(self):
        for octave in (MIDDLE_C_SCIENTIFIC, MIDDLE_C_YAMAHA):
            for pitch in range(0, 128):
                assert parse_pitch(pitch_name(pitch, octave), octave) == pitch

    def test_flats_parse(self):
        assert parse_pitch("Db4") == parse_pitch("C#4") == 61

    def test_labels_shift_but_notes_do_not(self):
        song = song_with([(60, 0, BEAT)])
        sci = render(song, 0, ghosts=False, middle_c_octave=MIDDLE_C_SCIENTIFIC)
        yam = render(song, 0, ghosts=False, middle_c_octave=MIDDLE_C_YAMAHA)
        assert sci.rows[0].label != yam.rows[0].label
        assert [r.pitch for r in sci.rows] == [r.pitch for r in yam.rows]

    def test_accidentals_are_the_black_keys(self):
        assert [p for p in range(60, 72) if is_accidental(p)] == [61, 63, 66, 68, 70]


# --------------------------------------------------------------------------
# Commands, undo, and the taxonomy
# --------------------------------------------------------------------------

class TestCommands:
    def test_undo_redo_round_trip(self):
        song = song_with([(60, 0, BEAT)])
        before = render_text(render(song, 0, ghosts=False))
        h = History(song)
        h.run(edit.AddNote(0, 64, BEAT, BEAT))
        assert render_text(render(song, 0, ghosts=False)) != before
        h.undo()
        assert render_text(render(song, 0, ghosts=False)) == before
        h.redo()
        assert render_text(render(song, 0, ghosts=False)) != before

    def test_new_edit_discards_the_redo_branch(self):
        song = song_with([(60, 0, BEAT)])
        h = History(song)
        h.run(edit.AddNote(0, 64, 0, BEAT))
        h.undo()
        assert h.can_redo
        h.run(edit.AddNote(0, 65, 0, BEAT))
        assert not h.can_redo

    def test_delete_is_undoable_which_is_why_right_click_is_safe(self):
        song = song_with([(60, 0, BEAT), (64, 0, BEAT)])
        nids = [n.nid for n in song.tracks[0].notes]
        h = History(song)
        h.run(edit.DeleteNotes(nids))
        assert song.tracks[0].notes == []
        h.undo()
        assert len(song.tracks[0].notes) == 2

    def test_selection_survives_a_transpose(self):
        """Selection is a set of note IDs, and IDs are stable across edits."""
        song = song_with([(60, 0, BEAT), (64, 0, BEAT)])
        nids = {n.nid for n in song.tracks[0].notes}
        History(song).run(edit.TransposeChromatic(nids, 5))
        assert {n.nid for n in song.tracks[0].notes} == nids
        assert sorted(n.pitch for n in song.tracks[0].notes) == [65, 69]

    def test_chromatic_and_diatonic_transpose_are_different_operations(self):
        """The trap: 'up a third' means two different things. Chromatic keeps
        every interval; diatonic keeps the key and changes them."""
        chrom = song_with([(60, 0, BEAT), (62, 0, BEAT)])   # C, D
        diat = song_with([(60, 0, BEAT), (62, 0, BEAT)])
        History(chrom).run(edit.TransposeChromatic(
            {n.nid for n in chrom.tracks[0].notes}, 4))
        History(diat).run(edit.TransposeDiatonic(
            {n.nid for n in diat.tracks[0].notes}, 2, root=0, scale="major"))
        assert sorted(n.pitch for n in chrom.tracks[0].notes) == [64, 66]   # E, F#
        assert sorted(n.pitch for n in diat.tracks[0].notes) == [64, 65]    # E, F
        # Chromatic preserved the whole tone; diatonic turned it into a semitone.

    def test_structure_preserving_transform_keeps_the_glyph_shape(self):
        """If a transform claims to preserve structure and the picture changes,
        it lied — and you can see that it lied."""
        song = song_with([(60, 0, BEAT // 4), (64, BEAT // 2, BEAT // 4),
                          (67, BEAT, BEAT // 2)])
        shape = _shape(song)
        History(song).run(edit.TransposeChromatic(
            {n.nid for n in song.tracks[0].notes}, 5))
        assert _shape(song) == shape

    def test_retrograde_mirrors_in_time(self):
        song = song_with([(60, 0, BEAT), (64, BEAT, BEAT * 2)])
        History(song).run(edit.Retrograde({n.nid for n in song.tracks[0].notes}))
        by_pitch = {n.pitch: n for n in song.tracks[0].notes}
        assert by_pitch[64].start == 0
        assert by_pitch[60].start == BEAT * 2

    def test_invert_mirrors_in_pitch(self):
        song = song_with([(60, 0, BEAT), (67, 0, BEAT)])
        History(song).run(edit.Invert({n.nid for n in song.tracks[0].notes}, axis=60))
        assert sorted(n.pitch for n in song.tracks[0].notes) == [53, 60]

    def test_augment_stretches_the_ties(self):
        song = song_with([(60, 0, BEAT // 2), (64, BEAT // 2, BEAT // 2)])
        History(song).run(edit.Scale({n.nid for n in song.tracks[0].notes}, 2.0))
        notes = sorted(song.tracks[0].notes, key=lambda n: n.start)
        assert [n.start for n in notes] == [0, BEAT]
        assert [n.duration for n in notes] == [BEAT, BEAT]

    def test_the_registry_is_the_function_set(self):
        assert "xform.retrograde" in edit.REGISTRY
        groups = dict(edit.notes_menu())
        shape = {s.key for s in groups["keeps the shape"]}
        independent = {s.key for s in groups["moves notes independently"]}
        assert "xform.transpose.chromatic" in shape
        assert "xform.transpose.diatonic" in shape
        assert "xform.quantise" in independent
        assert not shape & independent      # the separator means something

    def test_both_transposes_are_registered_with_distinct_labels(self):
        a = edit.REGISTRY["xform.transpose.chromatic"].label
        b = edit.REGISTRY["xform.transpose.diatonic"].label
        assert a != b
        assert "semitone" in a.lower() and "degree" in b.lower()

    def test_add_notes_is_one_diff_with_fresh_ids_and_undoes_cleanly(self):
        """A paste is one command: one undo, one audition, ids that can't
        collide with what was copied."""
        song = song_with([(60, 0, BEAT)])
        before = list(song.tracks[0].notes)
        h = History(song)
        c = h.run(edit.AddNotes(0, [(64, BEAT, BEAT, 90), (67, BEAT, BEAT // 2, 70)]))
        assert len(c._diffs) == 1 and len(song.tracks[0].notes) == 3
        added = [n for n in song.tracks[0].notes if n.nid in c.touched]
        assert [(n.pitch, n.start, n.duration, n.velocity) for n in added] == [
            (64, BEAT, BEAT, 90), (67, BEAT, BEAT // 2, 70)]
        assert len({n.nid for n in song.tracks[0].notes}) == 3
        assert {n.offset for n in Audition.of(song, c.auditions()).notes} == {0}   # heard as one chord
        h.undo()
        assert song.tracks[0].notes == before
        h.redo()
        assert {n.nid for n in song.tracks[0].notes if n.nid in c.touched} == c.touched

    def test_set_velocities_takes_a_value_per_note_and_undoes_cleanly(self):
        """A lane sweep is one command however many columns it crossed."""
        song = song_with([(60, 0, BEAT, 80), (64, BEAT, BEAT, 80), (67, 2 * BEAT, BEAT, 80)])
        a, b, c = [n.nid for n in song.tracks[0].sorted_notes()]
        h = History(song)
        cmd = h.run(edit.SetVelocities({a: 20, b: 200, c: 0}))
        by_nid = {n.nid: n.velocity for n in song.tracks[0].notes}
        assert by_nid == {a: 20, b: 127, c: 1}                       # clamped, like every edit
        assert cmd.touched == {a, b, c} and h.depth == 1
        h.undo()
        assert {n.nid: n.velocity for n in song.tracks[0].notes} == {a: 80, b: 80, c: 80}
        assert {n.nid for n in song.tracks[0].notes} == {a, b, c}    # ids survive

    def test_set_velocities_ignores_ids_it_cannot_find(self):
        song = song_with([(60, 0, BEAT, 80)])
        [n] = song.tracks[0].notes
        History(song).run(edit.SetVelocities({n.nid: 50, 999: 50}))
        assert song.tracks[0].notes[0].velocity == 50

    def test_the_gesture_commands_are_registered_in_their_categories(self):
        assert edit.REGISTRY["note.add_notes"].category is edit.Category.EDIT
        assert edit.REGISTRY["note.velocities"].category is edit.Category.INDEPENDENT
        assert edit.REGISTRY["note.velocities"].label == "Set velocities"


def _shape(song):
    """The glyph picture with pitch and time offsets removed — what a
    structure-preserving transform must leave alone."""
    matrix = render(song, 0, ghosts=False, occupied_rows_only=True)
    rows = [
        "".join(cell_glyphs(c) for c in row.cells)
        for row in matrix.rows
    ]
    return [r for r in rows if r.strip("·")]


# --------------------------------------------------------------------------
# Audition — a set of notes with relative timing, not a single note
# --------------------------------------------------------------------------

class TestAudition:
    def test_a_chord_stays_a_chord(self):
        song = song_with([(60, 0, BEAT), (64, 0, BEAT), (67, 0, BEAT)])
        sink = NullSink()
        Transport(song, sink).audition(song.tracks[0].notes)
        assert len(sink.last.notes) == 3
        assert {n.offset for n in sink.last.notes} == {0}

    def test_a_phrase_keeps_its_relative_timing(self):
        song = song_with([(60, BEAT * 4, BEAT), (64, BEAT * 5, BEAT)])
        sink = NullSink()
        Transport(song, sink).audition(song.tracks[0].notes)
        assert [n.offset for n in sink.last.notes] == [0, BEAT]

    def test_suppressed_while_the_transport_is_rolling(self):
        song = song_with([(60, 0, BEAT)])
        sink = NullSink()
        sink.set_rolling(True)
        assert not Transport(song, sink).audition(song.tracks[0].notes)
        assert not sink.log

    def test_the_toggle_works(self):
        song = song_with([(60, 0, BEAT)])
        sink = NullSink()
        t = Transport(song, sink)
        t.audition_enabled = False
        assert not t.audition(song.tracks[0].notes)

    def test_a_command_offers_its_notes_for_audition(self):
        song = song_with([])
        cmd = History(song).run(edit.AddNote(0, 60, 0, BEAT))
        assert [n.pitch for n in cmd.auditions()] == [60]

    def test_deleting_makes_no_sound(self):
        song = song_with([(60, 0, BEAT)])
        cmd = History(song).run(edit.DeleteNotes([song.tracks[0].notes[0].nid]
                                                 if song.tracks[0].notes else []))
        assert cmd.auditions() == []

    def test_seconds_walk_the_tempo_map(self):
        song = song_with([(60, 0, BEAT)])
        song.tempo_map = [TempoChange(0, 500_000)]          # 120bpm, 0.5s per beat
        t = Transport(song, NullSink())
        assert t.seconds_at(BEAT) == pytest.approx(0.5)
        song.tempo_map.append(TempoChange(BEAT, 1_000_000))  # then 60bpm
        assert t.seconds_at(BEAT * 2) == pytest.approx(1.5)


# --------------------------------------------------------------------------
# Layout — the picture has to line up or none of it reads
# --------------------------------------------------------------------------

class TestLayout:
    def test_every_line_is_the_same_width(self):
        song = song_with([(60, 0, BEAT), (64, BEAT, BEAT), (67, BEAT * 2, BEAT)])
        song.timesig_map.append(TimeSigChange(BAR, 3, 4))
        song.normalise()
        text = render_text(render(song, 0, first_bar=1, last_bar=2, ghosts=False))
        widths = {len(line) for line in text.splitlines()}
        assert len(widths) == 1, f"ragged: {sorted(widths)}"

    def test_bar_header_joins_land_on_bar_lines(self):
        song = song_with([(60, 0, BEAT)])
        text = render_text(render(song, 0, first_bar=1, last_bar=3, ghosts=False))
        header, ruler = text.splitlines()[0], text.splitlines()[1]
        joins = {i for i, c in enumerate(header) if c in "┬"}
        bars = {i for i, c in enumerate(ruler) if c == "║"}
        assert joins <= bars

    def test_drum_track_swaps_in_gm_names(self):
        song = song_with([(36, 0, 120), (42, 0, 120)], channel=9)
        matrix = render(song, 0, ghosts=False, occupied_rows_only=True)
        assert {r.label for r in matrix.rows} == {"Kick", "HH-c"}

    def test_ascii_glyphs_avoid_box_drawing(self):
        from midiseq.core.glyphs import ASCII
        song = song_with([(60, 0, BEAT)])
        text = render_text(render(song, 0, ghosts=False), ASCII)
        rows = [l for l in text.splitlines() if l.startswith((" C", "▒"))]
        assert rows and all(c.isascii() for line in rows for c in line)

    def test_ghosts_never_overwrite_the_editable_track(self):
        song = Song(ppq=PPQ)
        a, b = Track(name="A"), Track(name="B", channel=1)
        a.notes.append(song.make_note(60, 0, BEAT, 80))
        b.notes.append(song.make_note(60, 0, BEAT, 80))
        song.tracks += [a, b]
        song.normalise()
        matrix = render(song, 0, low_pitch=60, high_pitch=60, ghosts=True)
        assert matrix.rows[0].cells[0].event.name == "HEAD"


# --------------------------------------------------------------------------
# Passthrough — the rule that stops us eating people's data
# --------------------------------------------------------------------------

class TestPassthrough:
    def test_controllers_are_kept(self, tmp_path):
        import mido
        mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name="X", time=0))
        track.append(mido.Message("control_change", control=1, value=64, time=0))
        track.append(mido.Message("pitchwheel", pitch=2000, time=10))
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=BEAT))
        mid.tracks.append(track)
        path = tmp_path / "cc.mid"
        mid.save(str(path))

        from midiseq.core import io_midi
        song = io_midi.load(path)
        kinds = {e.message.type for e in song.tracks[0].passthrough}
        assert "control_change" in kinds
        assert "pitchwheel" in kinds
        assert len(song.tracks[0].notes) == 1

    def test_note_on_velocity_zero_is_a_note_off(self, tmp_path):
        import mido
        mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
        track = mido.MidiTrack()
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_on", note=60, velocity=0, time=BEAT))
        mid.tracks.append(track)
        path = tmp_path / "z.mid"
        mid.save(str(path))

        from midiseq.core import io_midi
        song = io_midi.load(path)
        assert len(song.tracks[0].notes) == 1
        assert song.tracks[0].notes[0].duration == BEAT

    def test_loaded_ids_do_not_collide_with_new_ones(self, tmp_path):
        import mido
        mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)
        track = mido.MidiTrack()
        for i in range(4):
            track.append(mido.Message("note_on", note=60 + i, velocity=80, time=0))
            track.append(mido.Message("note_off", note=60 + i, velocity=0, time=BEAT))
        mid.tracks.append(track)
        path = tmp_path / "n.mid"
        mid.save(str(path))

        from midiseq.core import io_midi
        song = io_midi.load(path)
        fresh = song.make_note(72, 0, BEAT)
        assert fresh.nid not in {n.nid for n in song.tracks[0].notes}
