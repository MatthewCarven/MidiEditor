"""Audition, playback, and the clock.

Clicking an empty square plays the note. Dropping a dragged note or selection
plays it back. That's what makes the grid explorable rather than something you
have to already understand - the interface teaches you what it can do by being
pokeable.

It isn't a special feature: it fires the same "play selection" request that a
menu entry, a palette entry and a keyboard shortcut also fire. One class, four
doors.

**The sink takes a set of notes with relative timing, not a single note.** An
earlier sketch had "audition this one note", which is fine for click-to-insert
and completely wrong for dropping a chord or a phrase. Getting the interface
right now costs nothing; getting it wrong means rewriting every command later.

Step 5 added the rest of the clock. ``schedule()`` turns a song, from a tick,
into a sorted list of raw MIDI messages with the second each one is due,
walking the tempo map; ``TempoClock`` is that walk and its inverse, which is
what the playhead reads. ``Transport.play`` hands a ``Schedule`` to the sink
and asks it for ``elapsed()`` seconds; the transport turns that back into a
tick. The sink never knows about ticks, tempo or notes - only bytes and
seconds - so the null sink, the rtmidi sink and a test clock are the same
shape.

This is still the only place in the codebase that thinks in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .model import Note, Song

NOTE_OFF = 0x80
NOTE_ON = 0x90
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
PITCH_BEND = 0xE0
ALL_SOUND_OFF = 120
ALL_NOTES_OFF = 123

#: Same-second ordering. Releases before attacks, so a pitch re-struck on the
#: tick the previous one ends is two notes, not one; set-up messages (program,
#: controllers) before the notes they are meant for; a zero-length note's
#: release *after* its own attack, or it is released before it sounds.
ORDER_OFF, ORDER_OTHER, ORDER_ON, ORDER_OFF_AFTER_ON = 0, 1, 2, 3


@dataclass(frozen=True)
class ScheduledEvent:
    """One raw MIDI message and the second it is due, relative to the start
    of its schedule."""

    seconds: float
    message: tuple[int, ...]
    tick: int = 0
    order: int = ORDER_OTHER

    @property
    def status(self) -> int:
        return self.message[0] & 0xF0

    @property
    def channel(self) -> int:
        return self.message[0] & 0x0F

    @property
    def is_note_on(self) -> bool:
        return self.status == NOTE_ON and len(self.message) > 2 and self.message[2] > 0

    @property
    def is_note_off(self) -> bool:
        return self.status == NOTE_OFF or (self.status == NOTE_ON and len(self.message) > 2
                                           and self.message[2] == 0)

    def sort_key(self) -> tuple[float, int, int]:
        return (self.seconds, self.order, self.tick)


@dataclass
class Schedule:
    """What a sink plays: events in order, and where they came from."""

    events: list[ScheduledEvent]
    start_tick: int
    end_tick: int
    seconds: float

    @property
    def is_empty(self) -> bool:
        return not self.events


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

    def events(self) -> list[ScheduledEvent]:
        """The request as raw messages. A zero-length note still gets a
        thirty-second's worth of sound: an audition that can't be heard is
        no audition."""
        per_tick = self.us_per_beat / self.ppq / 1_000_000
        floor = max(1, self.ppq // 8)
        out: list[ScheduledEvent] = []
        for n in self.notes:
            ch = n.channel & 0x0F
            vel = max(1, min(127, n.velocity))
            dur = max(n.duration, floor)
            out.append(ScheduledEvent(n.offset * per_tick, (NOTE_ON | ch, n.pitch, vel),
                                      n.offset, ORDER_ON))
            out.append(ScheduledEvent((n.offset + dur) * per_tick, (NOTE_OFF | ch, n.pitch, 0),
                                      n.offset + dur, ORDER_OFF))
        out.sort(key=ScheduledEvent.sort_key)
        return out


class Sink(Protocol):
    """Where auditions and playback go.

    Implemented by the null sink (silent, logs, a test clock) and by
    python-rtmidi (``audio/sink_rtmidi.py``). Bytes and seconds only.
    """

    def play(self, audition: Audition) -> None:
        """A short request, fire and forget. Overlapping ones are fine."""
        ...

    def start(self, schedule: Schedule) -> None:
        """Roll: play the schedule from its first event. ``rolling`` stays
        true until the last event has sounded or ``stop()``."""
        ...

    def stop(self) -> None:
        """Silence, now: pending events dropped, sounding notes released."""
        ...

    def elapsed(self) -> float:
        """Seconds since ``start()``; 0 when not rolling."""
        ...

    @property
    def rolling(self) -> bool: ...


# --------------------------------------------------------------------------
# The clock: ticks <-> seconds through the tempo map
# --------------------------------------------------------------------------

class TempoClock:
    """Walks the song's tempo map. Reads the map on every call, so a tempo
    added after construction is honoured - cheap, because tempo maps are
    short."""

    def __init__(self, song: Song) -> None:
        self.song = song

    def _segments(self) -> list[tuple[int, int]]:
        """``(start_tick, us_per_beat)`` from tick 0, whatever the map says.
        A file with no tempo event plays at the MIDI default, 120 bpm; a map
        whose first event is late plays the default until it."""
        changes = sorted(self.song.tempo_map, key=lambda c: c.tick)
        out: list[tuple[int, int]] = []
        if not changes or changes[0].tick > 0:
            out.append((0, self.song.tempo_at(0).us_per_beat))
        for c in changes:
            if out and out[-1][0] == c.tick:
                out[-1] = (c.tick, c.us_per_beat)   # a restated tempo: the last word wins
            else:
                out.append((c.tick, c.us_per_beat))
        return [(tick, max(1, us)) for tick, us in out]    # 0 us/beat is a corrupt file, not a crash

    def seconds_at(self, tick: int) -> float:
        ppq = self.song.ppq
        seconds = 0.0
        segments = self._segments()
        for i, (start, us) in enumerate(segments):
            if tick <= start:
                break
            end = segments[i + 1][0] if i + 1 < len(segments) else tick
            end = min(end, tick)
            if end > start:
                seconds += (end - start) / ppq * (us / 1_000_000)
        return seconds

    def tick_at(self, seconds: float) -> int:
        """The inverse: the tick that is ``seconds`` into the song."""
        if seconds <= 0:
            return 0
        ppq = self.song.ppq
        segments = self._segments()
        remaining = seconds
        # A hair of slack before the floor, so seconds_at(t) comes back as t
        # rather than t - 1 when the float lands a billionth short.
        slack = 1e-6
        for i, (start, us) in enumerate(segments):
            per_tick = us / 1_000_000 / ppq
            if i + 1 < len(segments):
                length = (segments[i + 1][0] - start) * per_tick
                if remaining < length - slack * per_tick:
                    return start + int(remaining / per_tick + slack)
                remaining -= length
            else:
                return start + int(remaining / per_tick + slack)
        return 0


# --------------------------------------------------------------------------
# A song as a schedule
# --------------------------------------------------------------------------

#: Channel messages that set a channel up: the latest of each before the
#: start point is sent first when playing from the middle of a song, so the
#: right instrument, volume and bend are in force from the first note.
_SETUP_STATUSES = {PROGRAM_CHANGE, CONTROL_CHANGE, PITCH_BEND}


def _channel_bytes(message) -> tuple[int, ...] | None:
    """Raw bytes of a sendable message; None for meta events."""
    if getattr(message, "is_meta", False):
        return None
    try:
        raw = tuple(message.bytes())
    except Exception:  # noqa: BLE001 - not a mido message
        return None
    return raw if raw else None


def schedule(song: Song, start_tick: int = 0, end_tick: int | None = None) -> Schedule:
    """Every sendable event from ``start_tick`` (inclusive) to ``end_tick``
    (exclusive, default: the end), as raw bytes with seconds relative to the
    start, in the order a sink should send them.

    Notes sounding at ``start_tick`` restrike there with what is left of them
    (chase), so playing from the middle of a held chord plays the chord; a
    note that ended at or before the start is skipped. Set-up messages before
    the start are folded into a prelude at second zero: the last program
    change, the last value of each controller, the last pitch bend, per
    channel.
    """
    clock = TempoClock(song)
    start_tick = max(0, start_tick)
    t0 = clock.seconds_at(start_tick)
    events: list[ScheduledEvent] = []
    setup: dict[tuple, tuple[int, int, tuple[int, ...]]] = {}

    for track in song.tracks:
        for ev in track.passthrough:
            raw = _channel_bytes(ev.message)
            if raw is None:
                continue
            status = raw[0] & 0xF0
            if ev.tick < start_tick:
                if status in _SETUP_STATUSES:
                    key = (raw[0], raw[1]) if status == CONTROL_CHANGE else (raw[0],)
                    prior = setup.get(key)
                    if prior is None or (ev.tick, ev.order) >= prior[:2]:
                        setup[key] = (ev.tick, ev.order, raw)
                continue
            if end_tick is not None and ev.tick >= end_tick:
                continue
            events.append(ScheduledEvent(clock.seconds_at(ev.tick) - t0, raw, ev.tick, ORDER_OTHER))

        for n in track.notes:
            if end_tick is not None and n.start >= end_tick:
                continue
            if n.start < start_tick:
                if n.end <= start_tick:
                    continue
                on_tick = start_tick            # chase: sounding at the start, so restrike with what is left
            else:
                on_tick = n.start
            ch = (n.channel if n.channel is not None else track.channel) & 0x0F
            vel = max(1, min(127, n.velocity))
            off_tick = max(n.end, n.start)
            events.append(ScheduledEvent(clock.seconds_at(on_tick) - t0,
                                         (NOTE_ON | ch, n.pitch & 0x7F, vel), on_tick, ORDER_ON))
            events.append(ScheduledEvent(clock.seconds_at(off_tick) - t0,
                                         (NOTE_OFF | ch, n.pitch & 0x7F, n.off_velocity & 0x7F),
                                         off_tick, ORDER_OFF if n.duration > 0 else ORDER_OFF_AFTER_ON))

    if not any(e.order == ORDER_ON for e in events):
        return Schedule([], start_tick, start_tick, 0.0)    # set-up alone is nothing to play

    # The prelude goes in front: a set-up message the file has *at* the start
    # tick must have the last word over the stale value folded from before it,
    # and the sort is stable.
    prelude = [ScheduledEvent(0.0, raw, start_tick, ORDER_OTHER) for _, _, raw in sorted(setup.values())]
    events = prelude + events
    events.sort(key=ScheduledEvent.sort_key)
    end = max((e.tick for e in events), default=start_tick)
    length = max((e.seconds for e in events), default=0.0)
    return Schedule(events, start_tick, end, length)


# --------------------------------------------------------------------------
# The transport
# --------------------------------------------------------------------------

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
        self._start_tick = 0
        self._schedule: Schedule | None = None

    # -- audition -----------------------------------------------------------

    def audition(self, notes: Sequence[Note], channel: int = 0) -> bool:
        """Returns whether anything was actually sent - handy for tests and for
        greying out the toggle."""
        if not self.audition_enabled or self.sink.rolling:
            return False
        request = Audition.of(self.song, notes, channel)
        if request.is_empty:
            return False
        self.sink.play(request)
        return True

    # -- the clock ----------------------------------------------------------

    def seconds_at(self, tick: int) -> float:
        """Walk the tempo map. The only place in the codebase that thinks in seconds."""
        return TempoClock(self.song).seconds_at(tick)

    def tick_at(self, seconds: float) -> int:
        return TempoClock(self.song).tick_at(seconds)

    # -- playback -----------------------------------------------------------

    @property
    def rolling(self) -> bool:
        return self.sink.rolling

    def play(self, from_tick: int | None = None) -> bool:
        """Roll from ``from_tick`` (default: the playhead, which a schedule
        that ran out leaves at its end). Returns whether there was anything
        to play."""
        if self.rolling:
            self.stop()
        start = self.position() if from_tick is None else max(0, from_tick)
        self.playhead = start
        sched = schedule(self.song, start)
        if sched.is_empty:
            self._schedule = None
            return False
        self._start_tick = start
        self._schedule = sched
        self.sink.start(sched)
        return True

    def stop(self) -> None:
        """Stop where we are; the playhead stays there. A schedule that had
        already run out leaves the playhead at its end, whether or not anyone
        asked for the position in between."""
        self.playhead = self.position()
        self._schedule = None
        self.sink.stop()

    def toggle(self) -> bool:
        """Space. Returns whether the transport is rolling afterwards."""
        if self.rolling:
            self.stop()
            return False
        return self.play()

    def resync(self) -> None:
        """After an edit while rolling: the sink's schedule is stale, so play
        again from where we are. Notes sounding there restrike (chase), so a
        held pad survives an edit to its neighbour with a blink, not a hole."""
        if self.rolling:
            self.play(self.position())

    def position(self) -> int:
        """The playhead's tick: live while rolling, where it stopped otherwise.
        A schedule that ran out leaves the playhead at its end."""
        if self.rolling:
            elapsed = self.sink.elapsed()
            if self.rolling:    # read again: a run-out between the two reads leaves elapsed() at zero
                tick = self.tick_at(self.seconds_at(self._start_tick) + elapsed)
                return tick if self._schedule is None else min(tick, self._schedule.end_tick)
        if self._schedule is not None:
            self.playhead = self._schedule.end_tick
            self._schedule = None
        return self.playhead


__all__ = ["Audition", "AuditionNote", "Sink", "Transport", "TempoClock",
           "Schedule", "ScheduledEvent", "schedule",
           "NOTE_OFF", "NOTE_ON", "CONTROL_CHANGE", "PROGRAM_CHANGE", "PITCH_BEND",
           "ALL_SOUND_OFF", "ALL_NOTES_OFF",
           "ORDER_OFF", "ORDER_OTHER", "ORDER_ON", "ORDER_OFF_AFTER_ON"]
