"""Audition, and the clock.

Clicking an empty square plays the note. Dropping a dragged note or selection
plays it back. That's what makes the grid explorable rather than something you
have to already understand — the interface teaches you what it can do by being
pokeable.

It isn't a special feature: it fires the same "play selection" request that a
menu entry, a palette entry and a keyboard shortcut also fire. One class, four
doors.

**The sink takes a set of notes with relative timing, not a single note.** An
earlier sketch had "audition this one note", which is fine for click-to-insert
and completely wrong for dropping a chord or a phrase. Getting the interface
right now costs nothing; getting it wrong means rewriting every command later.

Sound arrives in step 5. Until then the null sink swallows requests silently and
the gesture works with no audio layer at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .model import Note, Song


@dataclass(frozen=True)
class AuditionNote:
    pitch: int
    velocity: int
    channel: int
    offset: int      # ticks from the start of the request, so chords stay chords
    duration: int    # ticks


@dataclass(frozen=True)
class Audition:
    """One request. A single note is just a request with one entry."""

    notes: tuple[AuditionNote, ...]
    ppq: int
    us_per_beat: int

    @property
    def is_empty(self) -> bool:
        return not self.notes

    def seconds(self) -> float:
        if not self.notes:
            return 0.0
        end = max(n.offset + n.duration for n in self.notes)
        return end / self.ppq * (self.us_per_beat / 1_000_000)

    @classmethod
    def of(cls, song: Song, notes: Sequence[Note], channel: int = 0) -> "Audition":
        """Build a request from real notes, preserving their relative timing."""
        notes = [n for n in notes if n is not None]
        if not notes:
            return cls((), song.ppq, song.tempo_at(0).us_per_beat)
        origin = min(n.start for n in notes)
        tempo = song.tempo_at(origin)
        return cls(
            tuple(
                AuditionNote(n.pitch, n.velocity, channel, n.start - origin, n.duration)
                for n in sorted(notes, key=lambda n: (n.start, n.pitch))
            ),
            song.ppq,
            tempo.us_per_beat,
        )


class Sink(Protocol):
    """Where auditions and playback go. Implemented by the null sink now and by
    python-rtmidi in step 5."""

    def play(self, audition: Audition) -> None: ...
    def stop(self) -> None: ...

    @property
    def rolling(self) -> bool: ...


class Transport:
    """Owns the playhead and the audition policy.

    Two guards, both because audition should stay a delight rather than a nuisance:
    a toggle for when you're moving forty notes and don't want forty little
    performances, and a rule that it never fires while the transport is already
    rolling and would play over the top of the song.
    """

    def __init__(self, song: Song, sink: Sink) -> None:
        self.song = song
        self.sink = sink
        self.audition_enabled = True
        self.playhead = 0

    def audition(self, notes: Sequence[Note], channel: int = 0) -> bool:
        """Returns whether anything was actually sent — handy for tests and for
        greying out the toggle."""
        if not self.audition_enabled or self.sink.rolling:
            return False
        request = Audition.of(self.song, notes, channel)
        if request.is_empty:
            return False
        self.sink.play(request)
        return True

    def seconds_at(self, tick: int) -> float:
        """Walk the tempo map. The only place in the codebase that thinks in seconds."""
        seconds = 0.0
        cursor = 0
        for i, change in enumerate(self.song.tempo_map):
            nxt = (self.song.tempo_map[i + 1].tick
                   if i + 1 < len(self.song.tempo_map) else None)
            end = tick if nxt is None or nxt > tick else nxt
            if end > cursor:
                seconds += (end - cursor) / self.song.ppq * (change.us_per_beat / 1e6)
                cursor = end
            if cursor >= tick:
                break
        return seconds


__all__ = ["Audition", "AuditionNote", "Sink", "Transport"]
