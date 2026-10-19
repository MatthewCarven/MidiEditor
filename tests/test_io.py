"""Step 2: the writer, the ``.seq`` reader, and the round trip.

Every test here defends one sentence: **opening and saving a file you didn't edit
gives back the same music, and editing text changes only what you actually typed
over.** Where a test looks pedantic it's usually standing on a bug that already
happened once — the phantom tempo, the end-of-track marker, the re-struck note.

The files are built ugly on purpose. A round-trip test against tidy output we
generated ourselves proves almost nothing.
"""

from __future__ import annotations

import mido
import pytest

from midiseq.core import io_midi, io_seq, io_text
from midiseq.core.glyphs import ASCII
from midiseq.core.grid import Zoom
from midiseq.core.model import MIDDLE_C_FL, Song, Track


# --------------------------------------------------------------------------
# Ugly files to throw at it
# --------------------------------------------------------------------------

def write_midi(path, tracks, *, ppq=480, fmt=1):
    """Build a file from ``(delta, message)`` pairs, warts and all."""
    mid = mido.MidiFile(type=fmt, ticks_per_beat=ppq)
    for events in tracks:
        mt = mido.MidiTrack()
        for delta, msg in events:
            msg = msg.copy(time=delta)
            mt.append(msg)
        mid.tracks.append(mt)
    mid.save(str(path))
    return path


def M(kind, **kw):
    return mido.Message(kind, **kw)


def Meta(kind, **kw):
    return mido.MetaMessage(kind, **kw)


@pytest.fixture
def bluesy():
    return "examples/bluesy.mid"


def roundtrip(tmp_path, src):
    """Load, save, and return (before, after) musical content."""
    before = io_midi.musical_content(src)
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(src), out)
    return before, io_midi.musical_content(out)


# --------------------------------------------------------------------------
# The phantom seed — the bug that would have been blamed on the writer
# --------------------------------------------------------------------------

def test_loaded_song_has_only_the_tempo_the_file_declares(bluesy):
    """The Song constructor seeds a 120 bpm default. A loaded file must not keep
    it, or the writer emits 120 bpm at tick 0 *and then* the real tempo — a DAW
    reading the first one shows the wrong number."""
    song = io_midi.load(bluesy)
    assert len(song.tempo_map) == 1
    assert round(song.tempo_map[0].bpm) == 96


def test_meter_changes_are_counted_honestly(bluesy):
    song = io_midi.load(bluesy)
    assert [(c.tick, str(c)) for c in song.timesig_map] == [
        (0, "4/4"), (19200, "3/4")]


def test_a_file_with_no_tempo_still_gets_the_midi_default(tmp_path):
    src = write_midi(tmp_path / "bare.mid",
                     [[(0, M("note_on", note=60, velocity=64)),
                       (480, M("note_off", note=60, velocity=0))]])
    song = io_midi.load(src)
    assert round(song.tempo_at(0).bpm) == 120     # the MIDI default, not a phantom
    # and it lives in the *lookup*, not in the map, so it never gets written out
    assert song.tempo_map == []


def test_the_same_meter_restated_in_two_tracks_is_written_once(tmp_path):
    """Format 1 files often put 4/4 in the conductor track and again in a part."""
    src = write_midi(tmp_path / "twice.mid", [
        [(0, Meta("time_signature", numerator=4, denominator=4))],
        [(0, Meta("time_signature", numerator=4, denominator=4)),
         (0, M("note_on", note=60, velocity=64)),
         (480, M("note_off", note=60, velocity=0))],
    ])
    song = io_midi.load(src)
    assert len(song.timesig_map) == 1


# --------------------------------------------------------------------------
# The guarantee
# --------------------------------------------------------------------------

def test_the_demo_file_round_trips_exactly(tmp_path, bluesy):
    before, after = roundtrip(tmp_path, bluesy)
    assert before == after


def test_controller_automation_survives(tmp_path, bluesy):
    """The passthrough bucket earning its keep: nudge nothing, lose nothing."""
    before, after = roundtrip(tmp_path, bluesy)
    ccs = [e for e in before["other"] if e[1] == "control_change"]
    assert ccs and ccs == [e for e in after["other"] if e[1] == "control_change"]


def test_sysex_survives(tmp_path):
    src = write_midi(tmp_path / "sysex.mid", [[
        (0, mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01])),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
    ]])
    before, after = roundtrip(tmp_path, src)
    assert before == after
    assert any(e[1] == "sysex" for e in after["other"])


def test_trailing_silence_survives(tmp_path):
    """A file says 'four empty bars after the last note' by putting end_of_track
    there. Close the track on the last note instead and the song gets shorter."""
    src = write_midi(tmp_path / "tail.mid", [[
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
        (7680, Meta("end_of_track")),
    ]])
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(src), out)
    assert mido.MidiFile(str(out)).length == pytest.approx(
        mido.MidiFile(str(src)).length)


def test_end_of_track_never_lands_mid_track(tmp_path):
    """It's a delimiter. Written in the middle, everything after it vanishes."""
    src = write_midi(tmp_path / "eot.mid", [[
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
        (960, Meta("end_of_track")),
    ]])
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(src), out)
    for mtrack in mido.MidiFile(str(out)).tracks:
        assert [m.type for m in mtrack].count("end_of_track") == 1
        assert mtrack[-1].type == "end_of_track"


def test_note_on_velocity_zero_is_read_as_a_note_off(tmp_path):
    """The old running-status trick. Read as a note-on it leaves a note hanging."""
    src = write_midi(tmp_path / "vel0.mid", [[
        (0, M("note_on", note=60, velocity=90)),
        (480, M("note_on", note=60, velocity=0)),
    ]])
    song = io_midi.load(src)
    assert [(n.pitch, n.start, n.duration) for n in song.tracks[0].notes] \
        == [(60, 0, 480)]


def test_a_pitch_restruck_on_the_beat_it_ends_survives(tmp_path):
    """note_off must be written before note_on at the same tick, or the new note
    is killed by the old one's release and two notes come back as one."""
    src = write_midi(tmp_path / "restrike.mid", [[
        (0, M("note_on", note=60, velocity=90)),
        (480, M("note_off", note=60, velocity=0)),
        (0, M("note_on", note=60, velocity=90)),
        (480, M("note_off", note=60, velocity=0)),
    ]])
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(src), out)
    assert len(io_midi.load(out).tracks[0].notes) == 2


def test_a_note_left_open_at_the_end_is_closed_not_dropped(tmp_path):
    src = write_midi(tmp_path / "open.mid", [[
        (0, M("note_on", note=60, velocity=90)),
        (960, M("note_on", note=64, velocity=90)),
        (480, M("note_off", note=64, velocity=0)),
    ]])
    song = io_midi.load(src)
    assert sorted(n.pitch for n in song.tracks[0].notes) == [60, 64]


def test_odd_ppq_is_preserved_not_normalised(tmp_path):
    src = write_midi(tmp_path / "ppq.mid", [[
        (0, M("note_on", note=60, velocity=64)),
        (96, M("note_off", note=60, velocity=0)),
    ]], ppq=96)
    before, after = roundtrip(tmp_path, src)
    assert after["ppq"] == 96 and before == after


def test_track_order_and_names_are_preserved(tmp_path):
    src = write_midi(tmp_path / "named.mid", [
        [(0, Meta("track_name", name="Conductor"))],
        [(0, Meta("track_name", name="Bass")),
         (0, M("note_on", note=40, velocity=64, channel=3)),
         (480, M("note_off", note=40, velocity=0, channel=3))],
        [(0, Meta("track_name", name="Lead")),
         (0, M("note_on", note=72, velocity=64, channel=5)),
         (480, M("note_off", note=72, velocity=0, channel=5))],
    ])
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(src), out)
    reloaded = io_midi.load(out)
    assert [t.name for t in reloaded.tracks] == ["Conductor", "Bass", "Lead"]
    assert [t.channel for t in reloaded.tracks[1:]] == [3, 5]


def test_tempo_is_written_once_into_the_conductor_track(tmp_path, bluesy):
    """Not once per track — that would multiply the tempo map by the track count."""
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(bluesy), out)
    counts = [sum(1 for m in t if m.type == "set_tempo")
              for t in mido.MidiFile(str(out)).tracks]
    assert counts[0] == 1 and sum(counts[1:]) == 0


# --------------------------------------------------------------------------
# Format 0
# --------------------------------------------------------------------------

def test_format_0_splits_by_channel_on_load(tmp_path):
    src = write_midi(tmp_path / "f0.mid", [[
        (0, M("note_on", note=60, velocity=64, channel=0)),
        (0, M("note_on", note=40, velocity=64, channel=9)),
        (480, M("note_off", note=60, velocity=0, channel=0)),
        (0, M("note_off", note=40, velocity=0, channel=9)),
    ]], fmt=0)
    song = io_midi.load(src)
    assert len(song.tracks) == 2
    assert [t.channel for t in song.tracks] == [0, 9]
    assert song.tracks[1].is_drums          # channel 10 becomes a drum grid


def test_format_0_comes_back_out_as_format_0(tmp_path):
    src = write_midi(tmp_path / "f0.mid", [[
        (0, Meta("set_tempo", tempo=500000)),
        (0, M("note_on", note=60, velocity=64, channel=0)),
        (0, M("note_on", note=40, velocity=70, channel=9)),
        (480, M("note_off", note=60, velocity=0, channel=0)),
        (0, M("note_off", note=40, velocity=0, channel=9)),
    ]], fmt=0)
    before, after = roundtrip(tmp_path, src)
    assert after["format"] == 0 and after["tracks"] == 1
    assert before == after


def test_a_single_channel_format_0_file_is_not_split(tmp_path):
    src = write_midi(tmp_path / "f0one.mid", [[
        (0, M("note_on", note=60, velocity=64, channel=2)),
        (480, M("note_off", note=60, velocity=0, channel=2)),
    ]], fmt=0)
    assert len(io_midi.load(src).tracks) == 1


def test_an_empty_song_still_writes_a_valid_file(tmp_path):
    out = tmp_path / "empty.mid"
    io_midi.save(Song(), out)
    assert mido.MidiFile(str(out)).tracks


# --------------------------------------------------------------------------
# The .seq reader
# --------------------------------------------------------------------------

def dump(song, **kw):
    kw.setdefault("first_bar", 9)
    kw.setdefault("last_bar", 11)
    kw.setdefault("bars_per_block", 3)
    return io_text.dump(song, 2, **kw)


def test_an_unedited_dump_is_a_no_op(bluesy):
    """The heart of it. If reading back what we printed produces edits, every
    velocity and every off-grid tick in the file is quietly at risk."""
    song = io_midi.load(bluesy)
    commands, change = io_seq.diff(song, io_seq.parse(dump(song)))
    assert commands == [] and change.total == 0 and change.untouched == 10


def test_every_note_is_read_back_off_the_page(bluesy):
    song = io_midi.load(bluesy)
    parsed = io_seq.parse(dump(song))
    block = parsed.blocks[0]
    read = io_seq.notes_from_cells(block.rows, parsed.glyphs, block.width)
    assert len(read) == 10
    assert sorted({n.pitch for n in read}) == [60, 63, 64, 65, 67, 69]


def test_the_ascii_glyph_set_parses_too(bluesy):
    song = io_midi.load(bluesy)
    commands, change = io_seq.diff(
        song, io_seq.parse(dump(song, glyphs=ASCII)))
    assert commands == [] and change.total == 0


def test_the_header_declares_its_own_middle_c_convention(bluesy):
    """A file written under FL's C5 and read back must give the same *pitches*.
    Labels shift; notes don't. Otherwise a file can be silently misread."""
    song = io_midi.load(bluesy)
    text = dump(song, middle_c_octave=MIDDLE_C_FL)
    assert "middle-c C5" in text
    commands, change = io_seq.diff(song, io_seq.parse(text))
    assert commands == [] and change.total == 0


def test_the_header_declares_its_velocity_bands(bluesy):
    parsed = io_seq.parse(dump(io_midi.load(bluesy)))
    assert parsed.bands == {"o": 40, "x": 80, "X": 112}


def test_text_edited_at_a_different_zoom_is_refused(bluesy):
    """Silently misaligning columns would scatter notes across the bar."""
    song = io_midi.load(bluesy)
    text = dump(song, zoom=Zoom(8)).replace("zoom   1/8", "zoom   1/16")
    with pytest.raises(io_seq.SeqParseError, match="different zoom"):
        io_seq.diff(song, io_seq.parse(text))


def test_a_file_that_is_not_a_seq_is_refused():
    with pytest.raises(io_seq.SeqParseError):
        io_seq.parse("just some words\n")


# --------------------------------------------------------------------------
# The diff — only what you changed changes
# --------------------------------------------------------------------------

def apply(song, text):
    commands, change = io_seq.diff(song, io_seq.parse(text))
    for command in commands:
        command.do(song)
    return change


def blank_cells(text, label, first, count):
    """Blank ``count`` cells starting at cell ``first`` on the given row."""
    out = []
    for line in text.splitlines():
        if line.startswith(f" {label} ") or line.startswith(f"▒{label}"):
            cut = line.index("║")
            gutter, body = line[:cut], line[cut:]
            cells = [c for c in body if c not in "│║"]
            for i in range(first * 2, (first + count) * 2):
                cells[i] = "·"
            rebuilt, k = [], 0
            for ch in body:
                if ch in "│║":
                    rebuilt.append(ch)
                else:
                    rebuilt.append(cells[k])
                    k += 1
            line = gutter + "".join(rebuilt)
        out.append(line)
    return "\n".join(out) + "\n"


def test_blanking_a_cell_deletes_that_note_and_nothing_else(bluesy):
    song = io_midi.load(bluesy)
    before = {(n.pitch, n.start): (n.velocity, n.duration)
              for n in song.tracks[2].notes}
    change = apply(song, blank_cells(dump(song), "G4", 4, 4))   # bar 9 beat 2
    after = {(n.pitch, n.start): (n.velocity, n.duration)
             for n in song.tracks[2].notes}
    assert change.removed == 1 and change.added == 0
    assert set(before) - set(after) == {(67, 15840)}
    assert all(after[k] == before[k] for k in after)     # nothing else moved


def test_an_untouched_off_grid_note_keeps_its_exact_tick(bluesy):
    """The whole reason patching beats rebuilding. The F4 sits 37 ticks late; a
    text round trip must not quietly straighten it."""
    song = io_midi.load(bluesy)
    apply(song, blank_cells(dump(song), "G4", 4, 4))
    off_grid = [n for n in song.tracks[2].notes if n.pitch == 65]
    assert [(n.start, n.velocity) for n in off_grid] == [(19237, 70)]


def test_untouched_notes_keep_velocities_the_bands_cannot_express(bluesy):
    """104, 92, 88, 84, 76 and 64 all render as the same two glyphs. Rebuild from
    text and they'd collapse to 80. Patch, and they're never rewritten."""
    song = io_midi.load(bluesy)
    apply(song, blank_cells(dump(song), "G4", 4, 4))
    assert sorted(n.velocity for n in song.tracks[2].notes) == \
        [64, 70, 76, 84, 84, 88, 88, 88, 92]


def test_typing_a_head_into_an_empty_cell_adds_a_note(bluesy):
    song = io_midi.load(bluesy)
    text = dump(song)
    edited = []
    for line in text.splitlines():
        if line.startswith(" E4 "):
            cut = line.index("║")
            body = list(line[cut:])
            spots = [i for i, ch in enumerate(body) if ch not in "│║"]
            body[spots[48]] = "x"                      # cell 24: bar 10, beat 3
            body[spots[49]] = "·"
            line = line[:cut] + "".join(body)
        edited.append(line)
    change = apply(song, "\n".join(edited) + "\n")
    assert change.added == 1
    new = [n for n in song.tracks[2].notes if n.pitch == 64 and n.start > 17000]
    assert len(new) == 1
    assert new[0].velocity == 80          # the header's x band, = DEFAULT_VELOCITY


def test_changing_the_band_revoices_without_moving_anything(bluesy):
    song = io_midi.load(bluesy)
    text = dump(song).replace("│X──────·│", "│o──────·│")   # the loud G4
    change = apply(song, text)
    assert (change.revoiced, change.added, change.removed) == (1, 0, 0)
    g4 = next(n for n in song.tracks[2].notes if n.start == 15840)
    assert g4.velocity == 40 and g4.duration == 460   # length untouched


def test_extending_the_tie_run_resizes_the_note(bluesy):
    song = io_midi.load(bluesy)
    # The A4 quarter in bar 9 beat 3: carry it through beat 4 as well.
    text = dump(song).replace(
        " A4 ║········│········│x──────·│········║",
        " A4 ║········│········│x───────│───────·║")
    change = apply(song, text)
    assert change.resized == 1 and change.added == 0
    a4 = next(n for n in song.tracks[2].notes if n.start == 16320)
    assert a4.duration == 960 and a4.velocity == 92    # velocity untouched


def test_a_row_left_out_of_the_text_is_left_alone(bluesy):
    """Delete a *line* and you've said nothing about that pitch. Only blanking a
    cell is a claim. Otherwise printing a window would wipe what it didn't show."""
    song = io_midi.load(bluesy)
    text = "\n".join(L for L in dump(song).splitlines()
                     if not L.startswith(" G4 ")) + "\n"
    change = apply(song, text)
    assert change.removed == 0 and change.skipped_rows == 1
    assert len([n for n in song.tracks[2].notes if n.pitch == 67]) == 3


def test_bars_outside_the_printed_window_are_untouched(bluesy):
    song = io_midi.load(bluesy)
    elsewhere = [(n.pitch, n.start) for n in song.tracks[2].notes
                 if not 15360 <= n.start < 19680]
    apply(song, blank_cells(dump(song), "G4", 4, 4))
    assert [(n.pitch, n.start) for n in song.tracks[2].notes
            if not 15360 <= n.start < 19680] == elsewhere


def test_other_tracks_are_untouched(bluesy):
    song = io_midi.load(bluesy)
    pad = list(song.tracks[1].notes)
    passthrough = len(song.tracks[1].passthrough)
    apply(song, blank_cells(dump(song), "G4", 4, 4))
    assert song.tracks[1].notes == pad
    assert len(song.tracks[1].passthrough) == passthrough


def test_a_patched_song_still_round_trips(tmp_path, bluesy):
    song = io_midi.load(bluesy)
    apply(song, blank_cells(dump(song), "G4", 4, 4))
    out = tmp_path / "patched.mid"
    io_midi.save(song, out)
    reloaded = io_midi.load(out)
    assert len(reloaded.tracks[2].notes) == 9
    assert [e.message.type for e in reloaded.tracks[1].passthrough] \
        == [e.message.type for e in song.tracks[1].passthrough]


def test_the_diff_refuses_a_track_the_file_does_not_have(bluesy):
    song = io_midi.load(bluesy)
    text = dump(song).replace("track  2", "track  9")
    with pytest.raises(io_seq.SeqParseError, match="track 9"):
        io_seq.diff(song, io_seq.parse(text))


def test_edits_are_undoable(bluesy):
    """The diff emits the same command objects the GUI uses, so a build gets undo
    for free rather than being a second, separate way to change a song."""
    from midiseq.core.history import History
    song = io_midi.load(bluesy)
    history = History(song)
    commands, _ = io_seq.diff(song, io_seq.parse(
        blank_cells(dump(song), "G4", 4, 4)))
    for command in commands:
        history.run(command)
    assert len(song.tracks[2].notes) == 9
    while history.can_undo:
        history.undo()
    assert len(song.tracks[2].notes) == 10
    assert sorted(n.velocity for n in song.tracks[2].notes) == \
        [64, 70, 76, 84, 84, 88, 88, 88, 92, 104]


# --------------------------------------------------------------------------
# Building from text alone
# --------------------------------------------------------------------------

def test_text_alone_builds_a_song(bluesy):
    song = io_midi.load(bluesy)
    built = io_seq.to_song(io_seq.parse(dump(song)))
    assert len(built.tracks) == 1
    assert len(built.tracks[0].notes) == 10
    assert built.ppq == 480 and round(built.tempo_at(0).bpm) == 96


def test_text_alone_is_honestly_lossy(bluesy):
    """Named so nobody later 'fixes' it. Without a source there is nothing to
    diff against, so velocities land on the bands and the 37 ticks are gone. That
    limit is the argument for -i, not a defect to paper over."""
    song = io_midi.load(bluesy)
    built = io_seq.to_song(io_seq.parse(dump(song)))
    assert {n.velocity for n in built.tracks[0].notes} <= {40, 80, 112}
    assert all(n.start % 120 == 0 for n in built.tracks[0].notes)


def test_a_text_built_song_saves_and_reloads(tmp_path, bluesy):
    song = io_midi.load(bluesy)
    built = io_seq.to_song(io_seq.parse(dump(song)))
    out = tmp_path / "built.mid"
    io_midi.save(built, out)
    assert len(io_midi.load(out).tracks[0].notes) == 10


def test_a_text_built_song_keeps_the_meter_change(bluesy):
    """The bar header prints the signature where it changes, so a meter change is
    in the text. Ignoring it flattened bar 11 back to 4/4 and every bar after it
    landed in the wrong place."""
    song = io_midi.load(bluesy)
    built = io_seq.to_song(io_seq.parse(dump(song)))
    assert [(c.tick, str(c)) for c in built.timesig_map] == [(0, "4/4"), (19200, "3/4")]


def test_a_text_built_song_renders_back_to_the_same_cells(bluesy):
    """The strongest statement the text-only path can make: what the grammar can
    express, it expresses exactly. Round-tripping the *picture* is lossless even
    though round-tripping the music isn't."""
    song = io_midi.load(bluesy)
    # Without the lane: the velocity lane legitimately differs, because rebuilt
    # velocities are the three band values. The note grid is the claim.
    text = dump(song, lane=False)
    built = io_seq.to_song(io_seq.parse(text))
    again = io_text.dump(built, 0, first_bar=9, last_bar=11, bars_per_block=3,
                         lane=False)
    cells = lambda s: [L[L.index("║"):] for L in s.splitlines()
                       if "║" in L and "bar" not in L]
    assert cells(again) == cells(text)


def test_the_velocity_lane_is_where_a_text_only_rebuild_shows_its_loss(bluesy):
    """Same notes, same lengths, different dynamics — and the lane is the one
    place in the picture that says so. Worth a test of its own: it's the visible
    argument for patching over rebuilding."""
    song = io_midi.load(bluesy)
    text = dump(song)
    built = io_seq.to_song(io_seq.parse(text))
    again = io_text.dump(built, 0, first_bar=9, last_bar=11, bars_per_block=3)
    lane = lambda s: [L for L in s.splitlines() if L.startswith((" f ", " mf", " p ", " pp"))]
    assert lane(again) != lane(text)


def test_the_off_grid_flag_correctly_disappears_from_a_text_built_song(bluesy):
    """The one thing that legitimately changes. The F4 was 37 ticks late and the
    `~` said the row was drawn approximately; rebuilt from text it really is on
    the grid, so the warning would be a lie. Losing the flag here is the loss of
    the offset being reported honestly — not a rendering bug."""
    song = io_midi.load(bluesy)
    text = dump(song)
    built = io_seq.to_song(io_seq.parse(text))
    again = io_text.dump(built, 0, first_bar=9, last_bar=11, bars_per_block=3)
    assert " F4~" in text and " F4~" not in again
    assert all(n.start % 120 == 0 for n in built.tracks[0].notes)


# --------------------------------------------------------------------------
# The battery — awkward files, checked the way `seq check` checks them
# --------------------------------------------------------------------------

UGLY = {
    "format 0 with three channels including drums": (0, 480, [[
        (0, Meta("set_tempo", tempo=400000)),
        (0, Meta("time_signature", numerator=7, denominator=8)),
        (0, M("program_change", channel=0, program=48)),
        (0, M("note_on", channel=0, note=60, velocity=99)),
        (0, M("note_on", channel=9, note=36, velocity=120)),
        (240, M("note_on", channel=0, note=60, velocity=0)),
        (0, M("note_off", channel=9, note=36, velocity=0)),
        (0, M("note_on", channel=4, note=72, velocity=55)),
        (960, M("note_off", channel=4, note=72, velocity=0)),
    ]]),
    "pitch bend, channel and poly aftertouch": (1, 480, [[
        (0, M("pitchwheel", pitch=-2000)),
        (0, M("aftertouch", value=64)),
        (0, M("polytouch", note=60, value=90)),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
    ]]),
    "meter and tempo changing off the bar line": (1, 480, [
        [(0, Meta("time_signature", numerator=5, denominator=8)),
         (1200, Meta("time_signature", numerator=3, denominator=4)),
         (1440, Meta("time_signature", numerator=13, denominator=16)),
         (0, Meta("set_tempo", tempo=300000)),
         (600, Meta("set_tempo", tempo=1000000))],
        [(0, M("note_on", note=61, velocity=1)),
         (37, M("note_off", note=61, velocity=0)),
         (1, M("note_on", note=61, velocity=127)),
         (5000, M("note_off", note=61, velocity=0))],
    ]),
    "tiny ppq with the same pitch overlapping itself": (1, 24, [[
        (0, M("note_on", note=60, velocity=64)),
        (0, M("note_on", note=60, velocity=90)),
        (24, M("note_off", note=60, velocity=0)),
        (24, M("note_off", note=60, velocity=0)),
    ]]),
    "no notes at all, only markers": (1, 480, [[
        (0, Meta("track_name", name="just markers")),
        (0, Meta("marker", text="verse")),
        (1920, Meta("marker", text="chorus")),
    ]]),
    "meta we don't model": (1, 480, [[
        (0, Meta("key_signature", key="F#m")),
        (0, Meta("lyrics", text="la")),
        (0, Meta("smpte_offset")),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
    ]]),
}


@pytest.mark.parametrize("description", sorted(UGLY))
def test_awkward_files_round_trip(tmp_path, description):
    fmt, ppq, tracks = UGLY[description]
    src = write_midi(tmp_path / "ugly.mid", tracks, ppq=ppq, fmt=fmt)
    before, after = roundtrip(tmp_path, src)
    assert before == after, description


# --------------------------------------------------------------------------
# Regressions from the step-2 adversarial pass
#
# Ten ways the two promises could be broken, all found by attacking the code
# rather than by reading it. Each of these lost or invented something silently.
# --------------------------------------------------------------------------

def test_a_program_change_for_another_channel_does_not_move_the_part(tmp_path):
    """A GM file sets up the drum kit on channel 10 before playing a piano on
    channel 1. Letting the first channel-bearing message name the track meant it
    opened as a piano solo and saved as a drum solo."""
    src = write_midi(tmp_path / "gm.mid", [[
        (0, M("program_change", channel=9, program=0)),   # kit setup, no notes
        (0, M("program_change", channel=0, program=0)),
        (0, M("note_on", channel=0, note=60, velocity=80)),
        (480, M("note_off", channel=0, note=60, velocity=0)),
    ]], fmt=0)
    out = tmp_path / "out.mid"
    io_midi.save(io_midi.load(src), out)
    played = [m.channel for t in mido.MidiFile(str(out)).tracks
              for m in t if m.type == "note_on"]
    assert played == [0]


def test_one_track_may_hold_notes_on_several_channels(tmp_path):
    """Format 1 tracks from older sequencers routinely do. Squashing them onto
    the track's channel moved fifteen of sixteen parts to the wrong instrument."""
    events = [(0, M("note_on", channel=c, note=60 + c, velocity=64))
              for c in range(16)]
    events += [(480 if c == 0 else 0,
                M("note_off", channel=c, note=60 + c, velocity=0))
               for c in range(16)]
    src = write_midi(tmp_path / "multi.mid", [events])
    before, after = roundtrip(tmp_path, src)
    assert before == after


def test_a_controller_on_a_silent_channel_survives_a_format_0_split(tmp_path):
    """The split bucketed by channels that *sound notes*, so a volume CC on an
    otherwise silent channel belonged to no track and was dropped — exactly the
    loss the passthrough bucket exists to prevent."""
    src = write_midi(tmp_path / "silent.mid", [[
        (0, M("control_change", channel=5, control=7, value=100)),
        (0, M("note_on", channel=0, note=60, velocity=64)),
        (0, M("note_on", channel=9, note=40, velocity=64)),
        (480, M("note_off", channel=0, note=60, velocity=0)),
        (0, M("note_off", channel=9, note=40, velocity=0)),
    ]], fmt=0)
    before, after = roundtrip(tmp_path, src)
    assert before == after


DRUM_KIT = [
    (0, Meta("track_name", name="Kit")),
    (0, M("note_on", channel=9, note=42, velocity=90)),    # HH-c
    (120, M("note_off", channel=9, note=42, velocity=0)),
    (0, M("note_on", channel=9, note=36, velocity=100)),   # Kick
    (120, M("note_off", channel=9, note=36, velocity=0)),
    (0, M("note_on", channel=9, note=46, velocity=80)),    # HH-o
    (240, M("note_off", channel=9, note=46, velocity=0)),
]


@pytest.mark.parametrize("glyphs", [None, ASCII], ids=["unicode", "ascii"])
def test_a_drum_track_can_be_read_back(tmp_path, glyphs):
    """Two separate bugs made this impossible. The header wrote ``drums`` as a
    bare word the keyword/value parser couldn't see, so every GM name was read as
    a pitch name and dropped. And the ascii bar separator is ``H`` — which is
    inside twelve GM names including all three hi-hats — so scanning for a
    separator cut those rows inside their own label."""
    src = write_midi(tmp_path / "drums.mid", [DRUM_KIT])
    song = io_midi.load(src)
    kw = {"glyphs": glyphs} if glyphs else {}
    text = io_text.dump(song, 0, first_bar=1, last_bar=1, **kw)
    parsed = io_seq.parse(text)
    assert sorted(parsed.blocks[0].labels.values()) == ["HH-c", "HH-o", "Kick"]
    commands, change = io_seq.diff(song, parsed)
    assert commands == [] and change.untouched == 3


@pytest.mark.parametrize("numerator,denominator", [(2, 4), (3, 8), (5, 8), (3, 4)])
@pytest.mark.parametrize("zoom", [4, 8, 16])
def test_narrow_bars_still_name_themselves(tmp_path, numerator, denominator, zoom):
    """A 2/4 bar at 1/4 zoom is five characters wide and ' bar 9 ' is seven, so
    the header printed '┌ bar ┬ bar ┐' and nothing could read it back. Worse, a
    four-digit number truncated to three still *parsed* — and pointed an edit at
    bar 123 instead of bar 1234."""
    src = write_midi(tmp_path / "narrow.mid", [[
        (0, Meta("time_signature",
                 numerator=numerator, denominator=denominator)),
        (0, M("note_on", note=60, velocity=64)),
        (120, M("note_off", note=60, velocity=0)),
    ]])
    song = io_midi.load(src)
    text = io_text.dump(song, 0, zoom=Zoom(zoom), first_bar=1, last_bar=3,
                        bars_per_block=3, lane=False)
    parsed = io_seq.parse(text)
    assert parsed.blocks[0].bar_numbers == [1, 2, 3]
    assert io_seq.diff(song, parsed)[0] == []


def test_a_four_digit_bar_number_is_never_truncated(tmp_path):
    src = write_midi(tmp_path / "far.mid", [[
        (0, Meta("time_signature", numerator=3, denominator=4)),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
    ]])
    song = io_midi.load(src)
    text = io_text.dump(song, 0, zoom=Zoom(4), first_bar=1234, last_bar=1234,
                        bars_per_block=1, lane=False)
    assert io_seq.parse(text).blocks[0].bar_numbers == [1234]


def test_a_header_naming_bars_the_range_does_not_render_is_refused(bluesy):
    """Two bars of the same width slip past the cell-count check, so the bar
    numbers get compared too rather than editing the wrong bar in silence."""
    song = io_midi.load(bluesy)
    text = dump(song).replace("bar 10 ", "bar 12 ")   # same width, wrong bar
    with pytest.raises(io_seq.SeqParseError, match="renders as"):
        io_seq.diff(song, io_seq.parse(text))


def test_a_zero_length_note_stays_zero_length(tmp_path):
    """Several exporters write them. Padding to one tick is inventing data — and
    the note_off has to be written *after* its own note_on, the one case where
    the general note-off-first rule would release a note before it sounded."""
    src = write_midi(tmp_path / "zero.mid", [[
        (0, M("note_on", note=60, velocity=64)),
        (0, M("note_off", note=60, velocity=0)),
        (0, M("note_on", note=64, velocity=70)),
        (480, M("note_off", note=64, velocity=0)),
    ]])
    before, after = roundtrip(tmp_path, src)
    assert before == after
    assert sorted(n.duration for n in io_midi.load(src).tracks[0].notes) == [0, 480]


def test_format_2_is_refused_rather_than_written_wrong(tmp_path):
    """Independent patterns, each with its own tempo at its own tick 0. The model
    pools tempo song-wide, so writing one back collapses every pattern onto the
    last tempo declared and silently doubles the speed of the rest."""
    src = write_midi(tmp_path / "f2.mid", [
        [(0, Meta("set_tempo", tempo=500000)),
         (0, M("note_on", note=60, velocity=64)),
         (480, M("note_off", note=60, velocity=0))],
        [(0, Meta("set_tempo", tempo=250000)),
         (0, M("note_on", note=64, velocity=64)),
         (480, M("note_off", note=64, velocity=0))],
    ], fmt=2)
    with pytest.raises(ValueError, match="format 2"):
        io_midi.save(io_midi.load(src), tmp_path / "out.mid")


def test_a_tempo_restated_at_a_section_boundary_survives(tmp_path):
    """De-duplicating by *value* rather than by tick deleted it. Plenty of DAWs
    restate the tempo at each section, so `seq check` reported a loss on its own
    correct output."""
    src = write_midi(tmp_path / "restated.mid", [[
        (0, Meta("set_tempo", tempo=500000)),
        (1920, Meta("set_tempo", tempo=500000)),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0)),
    ]])
    before, after = roundtrip(tmp_path, src)
    assert before == after
    assert len(io_midi.load(src).tempo_map) == 2


def test_release_velocity_and_metronome_click_survive(tmp_path):
    """Neither is used often. 'Nothing lost' doesn't have an 'often' clause — and
    rebuilding a 6/8 file's time signature at the defaults changes its click from
    a dotted quarter to a quarter."""
    src = write_midi(tmp_path / "detail.mid", [[
        (0, Meta("time_signature", numerator=6, denominator=8,
                 clocks_per_click=36, notated_32nd_notes_per_beat=12)),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=77)),
    ]])
    before, after = roundtrip(tmp_path, src)
    assert before == after


def test_a_pitch_row_written_twice_is_refused(bluesy):
    """A dict assignment let the second copy win, so an edit made to the first
    vanished with no word said."""
    song = io_midi.load(bluesy)
    text = dump(song).splitlines()
    doubled = []
    for line in text:
        doubled.append(line)
        if line.startswith(" G4 "):
            doubled.append(line)
    with pytest.raises(io_seq.SeqParseError, match="twice"):
        io_seq.parse("\n".join(doubled) + "\n")


@pytest.mark.parametrize("broken,expected", [
    (("track  2", "track  x"), "track number"),
    (("zoom   1/16", "zoom   1/12"), "zoom"),
])
def test_a_hand_broken_header_is_a_message_not_a_traceback(bluesy, broken, expected):
    song = io_midi.load(bluesy)
    parsed = io_seq.parse(dump(song).replace(*broken))
    with pytest.raises(io_seq.SeqParseError, match=expected):
        io_seq.diff(song, parsed)


# --------------------------------------------------------------------------
# The CLI's own footguns
# --------------------------------------------------------------------------

def test_a_wildcard_is_expanded_by_the_program(tmp_path):
    """Unix shells expand ``*.mid`` before the program sees it. Windows does not
    — cmd and PowerShell hand the literal string over. Without this, a Windows
    user pointing at a folder of files gets 'no such file: .\\midi\\*.mid' from a
    tool that is working perfectly."""
    from midiseq.cli.seq import expand
    for name in ("a.mid", "b.mid", "c.txt"):
        write_midi(tmp_path / name, [[
            (0, M("note_on", note=60, velocity=64)),
            (480, M("note_off", note=60, velocity=0))]])
    found = expand([str(tmp_path / "*.mid")])
    assert sorted(p.name for p in found) == ["a.mid", "b.mid"]


def test_a_folder_is_walked_recursively(tmp_path):
    """What someone with a shelf of old MIDI files actually means by the folder."""
    from midiseq.cli.seq import expand
    (tmp_path / "deep" / "deeper").mkdir(parents=True)
    write_midi(tmp_path / "top.mid", [[(0, M("note_on", note=60, velocity=64))]])
    write_midi(tmp_path / "deep" / "mid.MIDI",
               [[(0, M("note_on", note=60, velocity=64))]])
    write_midi(tmp_path / "deep" / "deeper" / "low.mid",
               [[(0, M("note_on", note=60, velocity=64))]])
    (tmp_path / "deep" / "notes.txt").write_text("ignore me")
    found = expand([str(tmp_path)])
    assert sorted(p.name for p in found) == ["low.mid", "mid.MIDI", "top.mid"]


def test_a_file_named_twice_is_only_checked_once(tmp_path):
    from midiseq.cli.seq import expand
    write_midi(tmp_path / "a.mid", [[(0, M("note_on", note=60, velocity=64))]])
    assert len(expand([str(tmp_path), str(tmp_path / "a.mid"),
                       str(tmp_path / "*.mid")])) == 1


def test_one_unreadable_file_does_not_stop_the_run(tmp_path, capsys):
    """A shelf of twenty-year-old files will contain at least one that isn't
    really a MIDI file. Reporting it and carrying on beats a traceback."""
    from midiseq.cli.seq import main
    write_midi(tmp_path / "good.mid", [[
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0))]])
    (tmp_path / "bad.mid").write_bytes(b"this is not a midi file")
    assert main(["check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "UNREADABLE" in out
    assert "came back identical" in out and "could not be read" in out


# --------------------------------------------------------------------------
# SMPTE, and reporting on a library rather than a handful
# --------------------------------------------------------------------------

def smpte_file(path):
    """A header declaring SMPTE time division — absolute frames, not beats.
    mido reports the division as a negative ``ticks_per_beat``."""
    import struct
    head = b"MThd" + struct.pack(">IHHh", 6, 0, 1, -6136)
    body = b"\x00\x90\x3c\x40" + b"\x40\x80\x3c\x00" + b"\x00\xff\x2f\x00"
    path.write_bytes(head + b"MTrk" + struct.pack(">I", len(body)) + body)
    return path


def test_a_smpte_file_round_trips_but_has_no_grid(tmp_path):
    """Old sequencers wrote these. The grid's axis *is* musical time, so a file
    with no quarter note has nothing honest to draw — and pretending otherwise
    gave negative cell widths and a 'degenerate time signature' crash three
    layers down. Loading and round-tripping it is still fine."""
    src = smpte_file(tmp_path / "smpte.mid")
    song = io_midi.load(src)
    assert song.is_smpte
    before, after = roundtrip(tmp_path, src)
    assert before == after
    with pytest.raises(ValueError, match="SMPTE"):
        io_text.dump(song, 0, first_bar=1, last_bar=1)
    assert "no musical grid" in io_midi.summary(song)


def test_a_declined_file_is_not_counted_as_a_failure(tmp_path, capsys):
    """Across a big library, 'we refused on purpose' and 'we broke' must not
    land in the same bucket — one is a footnote, the other is the bug list."""
    from midiseq.cli.seq import check_one
    src = write_midi(tmp_path / "f2.mid", [
        [(0, Meta("set_tempo", tempo=500000)),
         (0, M("note_on", note=60, velocity=64)),
         (480, M("note_off", note=60, velocity=0))],
        [(0, Meta("set_tempo", tempo=250000)),
         (0, M("note_on", note=64, velocity=64)),
         (480, M("note_off", note=64, velocity=0))],
    ], fmt=2)
    assert check_one(str(src))["status"] == "declined"
    (tmp_path / "junk.mid").write_bytes(b"nope")
    assert check_one(str(tmp_path / "junk.mid"))["status"] == "unreadable"


def test_a_report_groups_by_problem_kind_not_by_file(tmp_path):
    """Twenty thousand files hit the same handful of bugs thousands of times.
    A per-file list is unreadable; a per-kind list with a few examples is the
    bug list."""
    import json
    from midiseq.cli.seq import main
    for i in range(5):
        write_midi(tmp_path / f"good{i}.mid", [[
            (0, M("note_on", note=60, velocity=64)),
            (480, M("note_off", note=60, velocity=0))]])
    # Same failure mode twice: the point is that they collapse into one entry
    # with a count, not two lines to read.
    (tmp_path / "junk1.mid").write_bytes(b"not a midi file at all")
    (tmp_path / "junk2.mid").write_bytes(b"nor is this one, honestly")
    report = tmp_path / "r.json"
    main(["check", str(tmp_path), "--report", str(report)])
    data = json.loads(report.read_text())
    assert data["files"] == 7
    assert data["counts"]["ok"] == 5 and data["counts"]["unreadable"] == 2
    assert len(data["errors"]) == 1 and data["errors"][0]["files"] == 2
    assert data["survey"]["format"] == {"1": 5} or data["survey"]["format"] == {1: 5}


def test_the_report_surveys_what_is_in_the_library(tmp_path):
    """A free by-product worth having: a big library can tell us what real MIDI
    actually looks like, which is what step 3 onwards should be built against."""
    import json
    from midiseq.cli.seq import main
    write_midi(tmp_path / "a.mid", [[
        (0, Meta("time_signature", numerator=7, denominator=8)),
        (0, M("control_change", control=1, value=64)),
        (0, M("note_on", note=60, velocity=64)),
        (480, M("note_off", note=60, velocity=0))]], ppq=96)
    report = tmp_path / "r.json"
    main(["check", str(tmp_path), "--report", str(report)])
    survey = json.loads(report.read_text())["survey"]
    assert "7/8" in survey["meters"]
    assert "control_change" in survey["event_types"]
    assert "96" in survey["ppq"] or 96 in survey["ppq"]


def test_musical_content_keeps_the_last_of_two_same_tick_meta_events(tmp_path):
    """The model keeps the last tempo (or meter) stated on a tick, so the
    check must not count the first as lost: it never took effect."""
    import mido
    from midiseq.core.io_midi import musical_content
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=400_000, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=300_000, time=960))      # a later one is kept
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    path = tmp_path / "twice.mid"
    mid.save(str(path))
    meta = musical_content(path)["meta"]
    assert [m[0] for m in meta] == [0, 960]
    assert "400000" in repr(meta[0]) and "500000" not in repr(meta)
