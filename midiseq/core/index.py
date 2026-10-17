"""Finding the notes that touch a range of ticks, fast.

``GridView.notes_in_cell_range`` used to scan every note of a track on every
repaint, and ``render`` does that once per track when ghosts are on. On a
37,000-note file with 38 tracks the scan cost more than drawing the screen
did (the DearPyGui spike, 2026-10-18). This is the index that replaces it.

The question is always the same one: *which notes sound anywhere in
``[lo, hi)``?* — ``start < hi and end > lo``. Sorted by start, the first half
is a bisect. The second half is the awkward one, because a long note can start
far before ``lo`` and still be sounding at it. So alongside the sorted starts
we keep the running maximum of ``end`` — monotonic by construction — and
bisect *that* for the first note whose prefix could possibly reach ``lo``.
Everything before it ends too early; everything from there to the start
bisect is a candidate and gets the exact test. Cost: two bisects plus the
candidates, which is roughly the notes on screen plus however many long notes
overlap them.

Results come back in the track's own order, so a screen painted from the
index is identical to one painted from the scan — including which of two
overlapping notes wins a square.

The index rebuilds itself when the track's ``version`` changes. Every edit
goes through ``Command._apply`` in ``edit.py``, which bumps the version, so
nothing else has to remember to invalidate anything — and code that mutates
``track.notes`` behind the commands' back gets the stale answer it deserves.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from .model import Note, Song, Track


def sounds_in(note: Note, lo: int, hi: int) -> bool:
    """Does ``note`` sound anywhere in ``[lo, hi)``?

    A zero-length note counts where it sits, including on ``lo`` itself. Real
    exporters write them, and the grid's promise is that nothing ever
    vanishes for being too short to draw.
    """
    if note.start >= hi:
        return False
    return note.end > lo or (note.end == note.start and note.start >= lo)


class NoteIndex:
    """One track's notes, ordered by start, with the running maximum of end."""

    __slots__ = ("_track", "_version", "_count", "_notes", "_pos", "_starts", "_max_end")

    def __init__(self, track: Track) -> None:
        self._track = track
        self._version = -1
        self._count = -1
        self._notes: list[Note] = []
        self._pos: list[int] = []
        self._starts: list[int] = []
        self._max_end: list[int] = []
        self.refresh()

    @property
    def track(self) -> Track:
        return self._track

    def refresh(self) -> None:
        """Rebuild if the track has changed since the last build. Cheap when it
        hasn't: one version compare and one ``len``."""
        track = self._track
        if track.version == self._version and len(track.notes) == self._count:
            return
        order = sorted(range(len(track.notes)),
                       key=lambda i: (track.notes[i].start, i))
        notes = [track.notes[i] for i in order]
        running: list[int] = []
        best = -1
        for n in notes:
            if n.end > best:
                best = n.end
            running.append(best)
        self._notes = notes
        self._pos = order
        self._starts = [n.start for n in notes]
        self._max_end = running
        self._version = track.version
        self._count = len(track.notes)

    def touching(self, lo: int, hi: int) -> list[Note]:
        """Notes with ``start < hi and end > lo``: sounding somewhere in
        ``[lo, hi)``. Same answer, same order, as the linear scan."""
        self.refresh()
        if hi <= lo or not self._notes:
            return []
        stop = bisect_left(self._starts, hi)        # first note starting at or after hi
        first = bisect_left(self._max_end, lo)      # first prefix whose max end reaches lo
        if first >= stop:
            return []
        hits = [(self._pos[i], self._notes[i])
                for i in range(first, stop) if sounds_in(self._notes[i], lo, hi)]
        hits.sort(key=lambda h: h[0])
        return [n for _, n in hits]


class SongIndex:
    """A ``NoteIndex`` per track, built on first use.

    Keyed by the track *object*, not its position, so a track that moves or is
    replaced can't be answered from a stranger's index.
    """

    def __init__(self, song: Song) -> None:
        self.song = song
        self._by_id: dict[int, NoteIndex] = {}

    def for_track(self, track: Track) -> NoteIndex:
        idx = self._by_id.get(id(track))
        if idx is None or idx.track is not track:
            idx = self._by_id[id(track)] = NoteIndex(track)
        return idx

    def touching(self, track: Track, lo: int, hi: int) -> list[Note]:
        return self.for_track(track).touching(lo, hi)


__all__ = ["NoteIndex", "SongIndex", "sounds_in"]
