"""The note index must be invisible: same notes, same order, as the scan it
replaces — and it must notice edits without being told.
"""

from __future__ import annotations

import random

from midiseq.core import edit
from midiseq.core.grid import GridView, Zoom
from midiseq.core.history import History
from midiseq.core.index import NoteIndex, SongIndex
from midiseq.core.model import Song, Track
from midiseq.core.render import render

PPQ = 480
BEAT = PPQ
BAR = 4 * PPQ
SONG_LENGTH = 64 * BAR


def scan(track, lo, hi):
    """The definition the index has to match: sounding somewhere in [lo, hi),
    where a zero-length note counts where it sits, lo included - and an empty
    or inverted range holds nothing, whatever a naive comparison would say."""
    if hi <= lo:
        return []
    return [n for n in track.notes
            if n.start < hi and (n.end > lo or (n.duration == 0 and n.start >= lo))]


def random_song(seed: int, count: int = 400, tracks: int = 2) -> Song:
    """Notes in file order (unsorted), with a few very long ones so a note can
    start far before a window and still be sounding in it."""
    rnd = random.Random(seed)
    song = Song(ppq=PPQ)
    for t in range(tracks):
        track = Track(name=f"T{t}")
        for _ in range(count):
            start = rnd.randrange(0, SONG_LENGTH)
            duration = rnd.choice([0, 30, 120, 480, 1920, 20_000])
            track.notes.append(song.make_note(rnd.randrange(36, 96), start, duration,
                                              rnd.randrange(1, 128)))
        rnd.shuffle(track.notes)
        song.tracks.append(track)
    song.normalise()
    return song


class TestNoteIndex:
    def test_matches_the_scan_everywhere(self):
        for seed in range(6):
            song = random_song(seed)
            track = song.tracks[0]
            idx = NoteIndex(track)
            rnd = random.Random(100 + seed)
            for _ in range(300):
                lo = rnd.randrange(-500, SONG_LENGTH)
                hi = lo + rnd.randrange(-100, 6000)
                assert idx.touching(lo, hi) == scan(track, lo, hi), (seed, lo, hi)

    def test_long_note_starting_far_before_the_window(self):
        song = Song(ppq=PPQ)
        track = Track()
        long = song.make_note(60, 0, 50 * BAR, 80)            # sounds for fifty bars
        later = song.make_note(62, 40 * BAR, BEAT, 80)        # starts inside the window
        decoy = song.make_note(64, 10 * BAR, BEAT, 80)        # over long before it
        track.notes.extend([decoy, long, later])
        song.tracks.append(track)
        got = NoteIndex(track).touching(40 * BAR, 41 * BAR)
        assert got == [long, later]                           # file order, decoy gone

    def test_edges_are_half_open(self):
        song = Song(ppq=PPQ)
        track = Track()
        ends_at_lo = song.make_note(60, 0, 100, 80)
        starts_at_hi = song.make_note(61, 300, 100, 80)
        inside = song.make_note(62, 150, 10, 80)
        zero_at_lo = song.make_note(63, 100, 0, 80)
        zero_inside = song.make_note(64, 200, 0, 80)
        track.notes.extend([ends_at_lo, starts_at_hi, inside, zero_at_lo, zero_inside])
        song.tracks.append(track)
        got = NoteIndex(track).touching(100, 300)
        # The sounding note that ends on lo is over; the one starting on hi
        # hasn't begun; a zero-length note on lo is *there*, and stays.
        assert got == scan(track, 100, 300) == [inside, zero_at_lo, zero_inside]

    def test_empty_and_inverted_ranges(self):
        song = random_song(1)
        idx = NoteIndex(song.tracks[0])
        assert idx.touching(500, 500) == []
        assert idx.touching(900, 100) == []
        assert NoteIndex(Track()).touching(0, 10_000) == []

    def test_sees_an_edit_made_through_a_command(self):
        song = Song(ppq=PPQ)
        song.tracks.append(Track(name="T"))
        song.normalise()
        index = SongIndex(song)
        track = song.tracks[0]
        assert index.touching(track, 0, BAR) == []
        History(song).run(edit.AddNote(0, 60, BEAT, BEAT))
        assert [n.pitch for n in index.touching(track, 0, BAR)] == [60]

    def test_undo_is_seen_too(self):
        song = Song(ppq=PPQ)
        song.tracks.append(Track(name="T"))
        song.normalise()
        history = History(song)
        index = SongIndex(song)
        track = song.tracks[0]
        history.run(edit.AddNote(0, 60, BEAT, BEAT))
        assert len(index.touching(track, 0, BAR)) == 1
        history.undo()
        assert index.touching(track, 0, BAR) == []
        history.redo()
        assert len(index.touching(track, 0, BAR)) == 1

    def test_version_is_not_part_of_track_equality(self):
        a, b = Track(name="T"), Track(name="T")
        a.version = 7
        assert a == b


class TestThroughTheGrid:
    def test_gridview_answers_the_same_with_and_without(self):
        song = random_song(3)
        with_index = GridView(song, Zoom(16), SongIndex(song))
        without = GridView(song, Zoom(16))
        for track in song.tracks:
            for first in range(0, 64 * 16, 37):
                assert (with_index.notes_in_cell_range(track, first, first + 40)
                        == without.notes_in_cell_range(track, first, first + 40))

    def test_render_is_identical_with_an_index(self):
        song = random_song(4, count=200)
        plain = render(song, 0, zoom=Zoom(16), first_bar=5, last_bar=9, ghosts=True)
        fast = render(song, 0, zoom=Zoom(16), first_bar=5, last_bar=9, ghosts=True,
                      index=SongIndex(song))
        assert fast == plain


class TestEndFill:
    def _cell(self, duration: int, col: int):
        song = Song(ppq=PPQ)
        track = Track(name="T")
        track.notes.append(song.make_note(60, 0, duration, 80))
        song.tracks.append(track)
        song.normalise()
        m = render(song, 0, zoom=Zoom(16), low_pitch=60, high_pitch=60, ghosts=False)
        return m.rows[0].cells[col]

    def test_note_ending_halfway_through_its_last_cell(self):
        # 1/16 at 480 ppq is 120 ticks a cell; 300 ticks ends 60 into cell 2.
        assert self._cell(300, 2).end_fill == 0.5
        assert self._cell(300, 0).end_fill == 1.0

    def test_note_filling_its_last_cell_exactly(self):
        assert self._cell(240, 1).end_fill == 1.0

    def test_zero_length_note_keeps_a_sliver(self):
        assert self._cell(0, 0).end_fill == 0.1
