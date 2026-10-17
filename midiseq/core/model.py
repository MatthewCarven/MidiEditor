"""The data model.

Two rules that everything else depends on:

1. **Time is always in ticks.** Never seconds, never cells. Ticks are the only
   representation that survives tempo changes without lying, and the grid's axis
   is musical time. Seconds are the playhead's problem, nobody else's.

2. **Notes have stable IDs.** Selection is a *set of notes*, and it has to survive
   edits — transposing a selection must not lose it. Object identity would work
   in memory but breaks the moment anything is serialised, so notes carry an
   integer ``nid`` handed out by the Song.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator, Sequence

# --------------------------------------------------------------------------
# Pitch naming
# --------------------------------------------------------------------------

_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_ACCIDENTAL = [False, True, False, True, False, False, True, False, True, False, True, False]

#: Middle C (MIDI 60) is labelled C4 under scientific/ASA notation, C3 under the
#: Yamaha convention, C5 in FL Studio. The MIDI spec has no opinion — it only
#: defines note numbers. This is a pure display-layer offset.
MIDDLE_C_SCIENTIFIC = 4
MIDDLE_C_YAMAHA = 3
MIDDLE_C_FL = 5

DEFAULT_MIDDLE_C_OCTAVE = MIDDLE_C_SCIENTIFIC


def is_accidental(pitch: int) -> bool:
    """True for the black keys — the rows that get shaded."""
    return _ACCIDENTAL[pitch % 12]


def pitch_name(pitch: int, middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE) -> str:
    """``60 -> 'C4'`` by default, ``'C3'`` under the Yamaha convention."""
    octave = (pitch // 12) - 5 + middle_c_octave
    return f"{_SHARP_NAMES[pitch % 12]}{octave}"


def parse_pitch(name: str, middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE) -> int:
    """``'C4' -> 60``. Accepts sharps (``C#4``) and flats (``Db4``)."""
    s = name.strip()
    if not s:
        raise ValueError("empty pitch name")
    letter = s[0].upper()
    if letter not in "ABCDEFG":
        raise ValueError(f"bad pitch name: {name!r}")
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter]
    i = 1
    while i < len(s) and s[i] in "#b♯♭":
        base += 1 if s[i] in "#♯" else -1
        i += 1
    try:
        octave = int(s[i:])
    except ValueError as exc:
        raise ValueError(f"bad pitch name: {name!r}") from exc
    return base + (octave + 5 - middle_c_octave) * 12


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

#: New notes land here rather than at 127. If notes arrive at maximum you can only
#: ever go down, so nobody varies them. Landing at mf puts louder and softer each
#: one gesture away in opposite directions.
DEFAULT_VELOCITY = 80


@dataclass(frozen=True)
class Note:
    """A single note. Immutable — edits produce replacements, which keeps undo honest.

    ``start`` and ``duration`` are ticks. ``nid`` is stable for the note's whole
    life, including across moves and transpositions, so a selection survives them.
    """

    nid: int
    pitch: int          # 0-127
    start: int          # ticks from the start of the song
    duration: int       # ticks; 0 is legal — some exporters emit zero-length notes
    velocity: int = DEFAULT_VELOCITY

    #: Which MIDI channel this note sounds on, or ``None`` for "the track's".
    #: A format 1 track is allowed to carry more than one channel, and squashing
    #: them onto the track's channel moves whole parts to the wrong instrument.
    #: Notes created by editing leave this None and inherit, which is what the
    #: grid means by "this track".
    channel: int | None = None
    #: Release velocity. Almost always 0, occasionally not, and "occasionally"
    #: is enough to matter when the promise is that nothing is lost.
    off_velocity: int = 0

    @property
    def end(self) -> int:
        return self.start + self.duration

    def moved(self, *, pitch: int | None = None, start: int | None = None) -> "Note":
        return replace(
            self,
            pitch=self.pitch if pitch is None else max(0, min(127, pitch)),
            start=self.start if start is None else max(0, start),
        )

    def resized(self, duration: int) -> "Note":
        # An *edit* never produces a zero-length note — you can't see or grab one.
        # Loading a file can, which is why the field itself allows it.
        return replace(self, duration=max(1, duration))

    def with_velocity(self, velocity: int) -> "Note":
        return replace(self, velocity=max(1, min(127, velocity)))


# --------------------------------------------------------------------------
# Passthrough — the rule that stops us eating people's data
# --------------------------------------------------------------------------

@dataclass
class OpaqueEvent:
    """A MIDI event the model doesn't understand, kept verbatim so it survives a
    load/save round trip.

    Without this, you open someone's file, nudge one note, save, and their
    mod-wheel automation is gone — because the model only knew about notes and
    mido faithfully wrote out exactly the nothing it was given.
    """

    tick: int
    message: object     # a mido.Message or MetaMessage, untouched
    order: int = 0      # original position, so same-tick events keep their order


# --------------------------------------------------------------------------
# Tracks and song
# --------------------------------------------------------------------------

@dataclass
class Track:
    name: str = ""
    channel: int = 0
    program: int | None = None
    notes: list[Note] = field(default_factory=list)
    passthrough: list[OpaqueEvent] = field(default_factory=list)

    #: Where the source file's ``end_of_track`` sat. That delimiter is structural
    #: rather than musical, so it doesn't belong in the passthrough bucket — but
    #: its *position* is real data: it's how a file says "there are four bars of
    #: silence after the last note". Dropped on load, restored on save.
    end_tick_hint: int | None = None

    #: Bumped by ``Command._apply`` whenever ``notes`` changes. The note index
    #: (core/index.py) keys its cache on it. Not part of equality: two tracks
    #: with the same notes are the same track, however they got there.
    version: int = field(default=0, compare=False, repr=False)

    @property
    def is_drums(self) -> bool:
        """Channel 10 (0-indexed 9) means percussion — the grid swaps note names
        for GM drum names."""
        return self.channel == 9

    def sorted_notes(self) -> list[Note]:
        return sorted(self.notes, key=lambda n: (n.start, n.pitch))

    def note_by_id(self, nid: int) -> Note | None:
        for n in self.notes:
            if n.nid == nid:
                return n
        return None

    def pitch_range(self) -> tuple[int, int] | None:
        if not self.notes:
            return None
        pitches = [n.pitch for n in self.notes]
        return min(pitches), max(pitches)

    def end_tick(self) -> int:
        return max((n.end for n in self.notes), default=0)


@dataclass(frozen=True)
class TempoChange:
    tick: int
    us_per_beat: int    # microseconds per quarter note, as MIDI stores it

    @property
    def bpm(self) -> float:
        return 60_000_000.0 / self.us_per_beat


@dataclass(frozen=True)
class TimeSigChange:
    tick: int
    numerator: int
    denominator: int
    #: How the metronome clicks and how a beam is counted. Nothing here reads
    #: them, but a 6/8 file that says "click every dotted quarter" says it with
    #: these, and rebuilding them at the defaults changes the click.
    clocks_per_click: int = 24
    notated_32nd_notes_per_beat: int = 8

    def ticks_per_bar(self, ppq: int) -> int:
        return int(self.numerator * (4 / self.denominator) * ppq)

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"


DEFAULT_TEMPO = TempoChange(0, 500_000)          # 120 bpm
DEFAULT_TIMESIG = TimeSigChange(0, 4, 4)


@dataclass
class Song:
    #: Ticks per quarter note. A MIDI header can instead declare **SMPTE** time
    #: division, which mido reports as a *negative* number: absolute frames per
    #: second, not musical time. Such a file has no beat, so it has no grid — see
    #: ``is_smpte``. The value is kept exactly as read so it writes back exactly.
    ppq: int = 480
    title: str = ""
    midi_format: int = 1
    tracks: list[Track] = field(default_factory=list)
    tempo_map: list[TempoChange] = field(default_factory=lambda: [DEFAULT_TEMPO])
    timesig_map: list[TimeSigChange] = field(default_factory=lambda: [DEFAULT_TIMESIG])
    _next_nid: int = 1

    # -- note identity ----------------------------------------------------

    def new_nid(self) -> int:
        nid = self._next_nid
        self._next_nid += 1
        return nid

    def make_note(self, pitch: int, start: int, duration: int,
                  velocity: int = DEFAULT_VELOCITY, *,
                  channel: int | None = None, off_velocity: int = 0) -> Note:
        return Note(self.new_nid(), pitch, start, duration, velocity,
                    channel, off_velocity)

    def note_by_id(self, nid: int) -> tuple[int, Note] | None:
        """Returns ``(track_index, note)`` — commands need to know which track."""
        for i, track in enumerate(self.tracks):
            n = track.note_by_id(nid)
            if n is not None:
                return i, n
        return None

    @property
    def is_smpte(self) -> bool:
        """True when the file measures time in SMPTE frames rather than beats.

        The grid's horizontal axis *is* musical time — that's the choice the
        whole design rests on. A SMPTE file has no quarter note to divide, so
        there is nothing honest to draw. Round-tripping it is fine; rendering it
        is not, and pretending otherwise produced negative cell widths and a
        "degenerate time signature" crash three layers down.
        """
        return self.ppq <= 0

    # -- musical context --------------------------------------------------

    # The MIDI default applies until a file says otherwise, and *that is not the
    # same as the file saying it*. Keeping the fallback here rather than seeding
    # it into the map is what lets a file with no tempo event round-trip without
    # one: the map holds what the file said, the lookup knows the default.

    def tempo_at(self, tick: int) -> TempoChange:
        current = DEFAULT_TEMPO
        for change in self.tempo_map:
            if change.tick > tick:
                break
            current = change
        return current

    def timesig_at(self, tick: int) -> TimeSigChange:
        current = DEFAULT_TIMESIG
        for change in self.timesig_map:
            if change.tick > tick:
                break
            current = change
        return current

    def end_tick(self) -> int:
        return max((t.end_tick() for t in self.tracks), default=0)

    def clear_maps(self) -> None:
        """Drop the constructed-in defaults before a loader fills in a file's own.

        A ``Song`` built from nothing wants sensible defaults; a ``Song`` built
        from a file wants the file's. Without this the loader *appends* to the
        defaults and every loaded song carries a 120 bpm / 4-4 event at tick 0
        that isn't in the file. Harmless while reading — ``tempo_at`` takes the
        last entry at or before the tick, so the real value wins — but the writer
        would faithfully write the phantom back out, inventing an event and
        breaking the guarantee that an unedited file round-trips to the same
        music. ``normalise`` can't catch it: it only inserts a default when one
        is *missing*.
        """
        self.tempo_map.clear()
        self.timesig_map.clear()

    def normalise(self) -> None:
        """Sort and de-duplicate the maps.

        Deliberately does *not* guarantee an entry at tick 0. An earlier version
        inserted DEFAULT_TEMPO when the map didn't start at 0, which is the same
        invent-an-event mistake as the constructor seeding: a file that declares
        no tempo would get a 120 bpm event written into it that it never had.
        ``tempo_at``/``timesig_at`` supply the MIDI default instead, which is
        where a default belongs — in the reading, not in the data.
        """
        self.tempo_map.sort(key=lambda c: c.tick)
        self.timesig_map.sort(key=lambda c: c.tick)
        # A format 1 file often restates the meter in more than one track. Two
        # identical events at the same tick are one event said twice; keep one so
        # the writer doesn't emit the duplicate. A *differing* event at the same
        # tick is a real (if odd) thing to say, and the later one wins, which is
        # what tempo_at/timesig_at already do.
        self.tempo_map = _dedupe(self.tempo_map)
        self.timesig_map = _dedupe(self.timesig_map)

        # Keep _next_nid ahead of anything loaded from disk.
        highest = max((n.nid for t in self.tracks for n in t.notes), default=0)
        self._next_nid = max(self._next_nid, highest + 1)


def _dedupe(changes: list) -> list:
    """Collapse two events at the *same tick*, keeping the last.

    Same tick only. An earlier version also dropped a later change that repeated
    the current value — a song restating 120 bpm at each section boundary, which
    plenty of DAWs write. That's a real event said again, and deleting it means
    saving a file gives back less than went in.
    """
    out: list = []
    for change in changes:
        if out and out[-1].tick == change.tick:
            out[-1] = change
        else:
            out.append(change)
    return out


__all__ = [
    "Note", "Track", "Song", "OpaqueEvent", "TempoChange", "TimeSigChange",
    "DEFAULT_VELOCITY", "DEFAULT_TEMPO", "DEFAULT_TIMESIG",
    "DEFAULT_MIDDLE_C_OCTAVE", "MIDDLE_C_SCIENTIFIC", "MIDDLE_C_YAMAHA", "MIDDLE_C_FL",
    "pitch_name", "parse_pitch", "is_accidental",
]
