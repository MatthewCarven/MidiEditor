""".mid in, .mid out — with the passthrough rule built in from the start.

``.mid`` is the only save format; there is no custom canonical file. That makes
one thing non-negotiable::

    load  .mid ──┬──► notes we model        → the grid, editable
                 └──► everything else       → opaque bucket, keyed by tick
                      (SysEx, CC automation, pitch bend, unknown meta)

    save  .mid ◄───── merge both back, in tick order

Without the bucket, you open someone's file, nudge one note, save, and their
mod-wheel automation is gone — because the model only knew about notes and mido
faithfully wrote out exactly the nothing it was given.

**The guarantee: opening and saving a file you didn't edit gives back the same
music.** Not the same bytes — mido re-encodes, and a ``note_on`` with velocity 0
comes back as a ``note_off`` — but nothing lost and nothing invented. What that
costs in this file is the ordering rules in ``_events_for`` and the two hints on
``Track``; both are cheap now and miserable to retrofit.
"""

from __future__ import annotations

from pathlib import Path

import mido

from .model import (
    OpaqueEvent,
    Song,
    TempoChange,
    TimeSigChange,
    Track,
)

class Unsupported(ValueError):
    """We decline to write this, deliberately, rather than write it wrong.

    Worth its own type: across a big library the difference between "we broke"
    and "we refused on purpose" is the difference between a bug list and a
    footnote, and a caller can't tell those apart from a message string.
    """


#: Events the grid understands and re-synthesises on save. Everything else is
#: opaque and gets written back untouched.
_MODELLED = {"note_on", "note_off"}
#: Song-level events kept in the maps rather than the bucket.
_SONG_LEVEL = {"set_tempo", "time_signature"}
#: A delimiter, not data. Its *position* is kept on ``Track.end_tick_hint``; the
#: message itself is re-made by the writer. Keeping it in the passthrough bucket
#: would put an end-of-track marker in the middle of a written track, and
#: everything after it would be unreachable on the next read.
_STRUCTURAL = {"end_of_track"}


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def load(path: str | Path) -> Song:
    """Read a MIDI file into the model."""
    mid = mido.MidiFile(str(path))
    song = Song(ppq=mid.ticks_per_beat, midi_format=mid.type,
                title=Path(path).stem)
    # The file's own tempo and meter, not ours. See Song.clear_maps.
    song.clear_maps()

    if mid.type == 0 and len(mid.tracks) == 1:
        # Format 0 puts every channel in one track. Split *before* pairing note
        # ons to offs — after pairing the channel is gone and can't be recovered,
        # which is what made the previous attempt at this a stub.
        song.tracks = _split_format0(mid.tracks[0], song)
    else:
        for mtrack in mid.tracks:
            song.tracks.append(_load_track(_absolute(mtrack), song))

    song.normalise()
    return song


def _absolute(mtrack) -> list[tuple[int, "mido.Message"]]:
    """Delta times to absolute ticks, which is how everything else here thinks."""
    out, tick = [], 0
    for msg in mtrack:
        tick += msg.time
        out.append((tick, msg))
    return out


def _load_track(events: list[tuple[int, "mido.Message"]], song: Song,
                end_tick: int | None = None) -> Track:
    track = Track()
    open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
    last_tick = 0
    # The track's channel is decided by its *notes*. Taking it from whatever
    # channel-bearing message arrived first put a whole part on the wrong
    # instrument: a GM file that sets up the drum kit on channel 10 before
    # playing a piano on channel 1 opened as piano and saved as drums.
    note_channel: int | None = None
    any_channel: int | None = None

    for order, (tick, msg) in enumerate(events):
        last_tick = max(last_tick, tick)
        kind = msg.type

        if kind in _STRUCTURAL:
            track.end_tick_hint = max(track.end_tick_hint or 0, tick)
            continue

        if kind == "track_name":
            track.name = msg.name
            track.passthrough.append(OpaqueEvent(tick, msg.copy(), order))
            continue

        if kind == "program_change":
            if track.program is None:
                track.program = msg.program
            if any_channel is None:
                any_channel = msg.channel
            track.passthrough.append(OpaqueEvent(tick, msg.copy(), order))
            continue

        if kind == "set_tempo":
            song.tempo_map.append(TempoChange(tick, msg.tempo))
            continue

        if kind == "time_signature":
            song.timesig_map.append(TimeSigChange(
                tick, msg.numerator, msg.denominator,
                msg.clocks_per_click, msg.notated_32nd_notes_per_beat))
            continue

        if kind in _MODELLED:
            ch = msg.channel
            if note_channel is None:
                note_channel = ch
            if any_channel is None:
                any_channel = ch
            key = (ch, msg.note)
            # note_on with velocity 0 is the widely-used note_off.
            if kind == "note_on" and msg.velocity > 0:
                open_notes.setdefault(key, []).append((tick, msg.velocity))
            else:
                stack = open_notes.get(key)
                if stack:
                    start, vel = stack.pop(0)
                    # A zero-length note sounds nothing, but it was in the file
                    # and padding it to one tick is inventing data.
                    track.notes.append(song.make_note(
                        msg.note, start, max(0, tick - start), vel,
                        channel=ch, off_velocity=msg.velocity))
                # An unmatched note_off is dropped: there is no note to end.
            continue

        if hasattr(msg, "channel") and any_channel is None:
            any_channel = msg.channel
        # Everything else — CC, pitch bend, aftertouch, SysEx, unknown meta.
        track.passthrough.append(OpaqueEvent(tick, msg.copy(), order))

    # Notes still sounding at the end of the track get closed there rather than
    # silently vanishing.
    # "There" is the end_of_track when the track has one; a format 0 channel's
    # slice is handed the file's end by the split, since the marker itself
    # rides with the first channel only.
    end = max(last_tick, track.end_tick_hint or 0, end_tick or 0)
    for (ch, pitch), stack in open_notes.items():
        for start, vel in stack:
            track.notes.append(song.make_note(
                pitch, start, max(0, end - start), vel, channel=ch))

    if note_channel is not None:
        track.channel = note_channel
    elif any_channel is not None:
        track.channel = any_channel
    track.notes.sort(key=lambda n: (n.start, n.pitch))
    return track


def _split_format0(mtrack, song: Song) -> list[Track]:
    """Format 0 → one track per channel that has notes.

    Meta and SysEx carry no channel, so they ride with the first track. Saving as
    format 0 merges everything back into one, which puts them where they started.
    """
    events = _absolute(mtrack)
    track_end = max((tick for tick, _ in events), default=0)
    channels = sorted({
        msg.channel for _, msg in events
        if msg.type in _MODELLED and not (msg.type == "note_on" and msg.velocity == 0)
    })
    if len(channels) <= 1:
        return [_load_track(events, song)]

    tracks = []
    for i, ch in enumerate(channels):
        # Filtering the original list keeps the file's own ordering, which a sort
        # can't do here anyway: mido Messages aren't comparable with MetaMessages.
        # Channel-less events (meta, SysEx) ride with the first track only —
        # duplicating them would multiply the tempo map by the channel count.
        # So do events on a channel that never sounds a note: a volume CC on an
        # otherwise silent channel has no track to belong to, and dropping it is
        # exactly the loss the passthrough bucket exists to prevent.
        mine = [(tick, m) for tick, m in events
                if getattr(m, "channel", None) == ch
                or (i == 0 and getattr(m, "channel", None) not in channels)]
        track = _load_track(mine, song, end_tick=track_end)
        track.channel = ch
        if not track.name:
            track.name = f"ch{ch + 1}"
        tracks.append(track)
    return tracks


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

# Ordering of events that land on the same tick. Two of these are correctness,
# not tidiness:
#
#   note_off before note_on — otherwise re-striking a pitch on the beat where the
#   previous one ends kills the new note instead of the old one.
#
#   everything else before note_on — a program change or CC at the same tick as a
#   note is meant to be in force *for* that note.
#
_RANK_NAME = 0        # the track's name belongs at the top of the track
_RANK_META = 1        # tempo and meter, which frame everything after them
_RANK_NOTE_OFF = 2
_RANK_OTHER = 3       # CC, program change, pitch bend, SysEx, unknown meta
_RANK_NOTE_ON = 4
#: A zero-length note is the one case where a note_off must follow its own
#: note_on: at the same tick the general rule would release it before it sounds,
#: leaving an unmatched off and a hanging on. Some exporters really do write
#: these, and padding them to one tick would be inventing data.
_RANK_ZERO_LENGTH_OFF = 5


def save(song: Song, path: str | Path, *, midi_format: int | None = None) -> None:
    """Write the model back to ``.mid``, passthrough and all.

    Format, PPQ, track order and track names come from the song rather than from
    our preferences — a format 0 file that goes in comes back out as format 0.
    """
    fmt = song.midi_format if midi_format is None else midi_format
    if fmt == 2:
        # Format 2 is a bag of independent patterns, each with its own tempo at
        # its own tick 0. The model pools tempo and meter song-wide, so writing
        # one back would collapse every pattern onto the last tempo declared —
        # silently doubling the speed of everything else. Refusing is honest;
        # pretending is not.
        raise Unsupported(
            "format 2 files hold independent patterns with their own tempos, "
            "which this model pools song-wide. Pass midi_format=1 to write it "
            "as a multi-track song instead.")
    if fmt not in (0, 1):
        raise ValueError(f"unsupported MIDI format {fmt}")
    if fmt == 0 and len(song.tracks) > 1:
        mid = mido.MidiFile(type=0, ticks_per_beat=song.ppq)
        merged: list[tuple] = []
        for i, track in enumerate(song.tracks):
            merged += _events_for(track, song, conductor=(i == 0))
        mid.tracks.append(_to_mido_track(
            merged, max((t.end_tick_hint or 0) for t in song.tracks)))
    else:
        mid = mido.MidiFile(type=fmt, ticks_per_beat=song.ppq)
        for i, track in enumerate(song.tracks):
            mid.tracks.append(_to_mido_track(
                _events_for(track, song, conductor=(i == 0)),
                track.end_tick_hint or 0))
        if not mid.tracks:
            mid.tracks.append(_to_mido_track(
                _events_for(Track(), song, conductor=True), 0))
    mid.save(str(path))


def _events_for(track: Track, song: Song, *, conductor: bool) -> list[tuple]:
    """``(tick, rank, tiebreak, message)`` for one track.

    ``conductor`` gets the song-level maps. In format 1 that's track 0, which is
    where every sequencer looks for them; in format 0 there's only one track and
    it gets them by default.
    """
    out: list[tuple] = []

    if conductor:
        for change in song.tempo_map:
            out.append((change.tick, _RANK_META, 0,
                        mido.MetaMessage("set_tempo", tempo=change.us_per_beat)))
        for change in song.timesig_map:
            out.append((change.tick, _RANK_META, 1, mido.MetaMessage(
                "time_signature",
                numerator=change.numerator, denominator=change.denominator,
                clocks_per_click=change.clocks_per_click,
                notated_32nd_notes_per_beat=change.notated_32nd_notes_per_beat)))

    for event in track.passthrough:
        rank = _RANK_NAME if event.message.type == "track_name" else _RANK_OTHER
        out.append((event.tick, rank, event.order, event.message.copy()))

    for note in track.notes:
        channel = track.channel if note.channel is None else note.channel
        out.append((note.start, _RANK_NOTE_ON, note.pitch, mido.Message(
            "note_on", channel=channel, note=note.pitch,
            velocity=note.velocity)))
        out.append((note.end,
                    _RANK_NOTE_OFF if note.duration else _RANK_ZERO_LENGTH_OFF,
                    note.pitch, mido.Message(
                        "note_off", channel=channel, note=note.pitch,
                        velocity=note.off_velocity)))

    return out


def _to_mido_track(events: list[tuple], end_hint: int) -> "mido.MidiTrack":
    """Sort, convert absolute ticks back to deltas, and close the track."""
    mtrack = mido.MidiTrack()
    events = sorted(events, key=lambda e: (e[0], e[1], e[2]))
    tick = 0
    for abs_tick, _rank, _tie, msg in events:
        msg.time = abs_tick - tick
        tick = abs_tick
        mtrack.append(msg)
    # Trailing silence is real: a file says "four empty bars after the last note"
    # by putting end_of_track there. mido would otherwise close the track on the
    # last event and quietly shorten the song.
    mtrack.append(mido.MetaMessage("end_of_track", time=max(0, end_hint - tick)))
    return mtrack


# --------------------------------------------------------------------------
# Comparing — what the round-trip tests actually assert
# --------------------------------------------------------------------------

def musical_content(path: str | Path) -> dict:
    """Everything a file *says*, normalised so re-encoding doesn't count as a change.

    ``note_on`` velocity 0 and ``note_off`` mean the same thing; delta times are
    an encoding detail and absolute ticks are the truth; same-tick ordering of
    unrelated events is not information. Diffing raw bytes would fail on all
    three and tell us nothing. This is the shape the guarantee is written in.
    """
    mid = mido.MidiFile(str(path))
    notes: list[tuple] = []
    others: list[tuple] = []
    meta: list[tuple] = []

    for ti, mtrack in enumerate(mid.tracks):
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        tick = 0
        for msg in mtrack:
            tick += msg.time
            if msg.type == "end_of_track":
                continue
            if msg.type in _SONG_LEVEL:
                # A list, not a set: a tempo restated at a section boundary is a
                # real event, and collapsing repeats would hide losing one. Two
                # on the *same* tick collapse to the last, as the model keeps
                # them (``model._dedupe``): the first never took effect.
                meta.append((tick, msg.type, _meta_value(msg)))
            elif msg.type in _MODELLED:
                key = (msg.channel, msg.note)
                if msg.type == "note_on" and msg.velocity > 0:
                    open_notes.setdefault(key, []).append((tick, msg.velocity))
                elif open_notes.get(key):
                    start, vel = open_notes[key].pop(0)
                    notes.append((msg.channel, msg.note, start, tick, vel,
                                  msg.velocity))
            else:
                others.append((tick, msg.type, str(msg.bytes())))
        for (channel, note), starts in open_notes.items():
            # Never released: the loader closes it at the track's end, so the
            # comparison does the same rather than calling the result invented.
            for start, vel in starts:
                notes.append((channel, note, start, tick, vel, 0))
    last_word: dict[tuple[int, str], tuple] = {}
    for item in meta:
        last_word[item[:2]] = item
    return {
        "ppq": mid.ticks_per_beat,
        "format": mid.type,
        "tracks": len(mid.tracks),
        "notes": sorted(notes),
        "other": sorted(others),
        "meta": sorted(last_word.values()),
    }


def _meta_value(msg) -> tuple:
    if msg.type == "set_tempo":
        return (msg.tempo,)
    return (msg.numerator, msg.denominator,
            msg.clocks_per_click, msg.notated_32nd_notes_per_beat)


# --------------------------------------------------------------------------

def summary(song: Song) -> str:
    """One line per track — what ``seq stat`` prints."""
    lines = [
        f"{song.title or '(untitled)'} · "
        + (f"SMPTE division {song.ppq} (no musical grid)" if song.is_smpte
           else f"ppq {song.ppq}")
        + f" · format {song.midi_format} · {len(song.tracks)} track(s)"
    ]
    tempo = song.tempo_at(0)
    ts = song.timesig_at(0)
    lines.append(f"  {tempo.bpm:.4g} bpm · {ts}"
                 + (f" · {len(song.tempo_map) - 1} tempo change(s)"
                    if len(song.tempo_map) > 1 else "")
                 + (f" · {len(song.timesig_map) - 1} meter change(s)"
                    if len(song.timesig_map) > 1 else ""))
    for i, t in enumerate(song.tracks):
        rng = t.pitch_range()
        span = ""
        if rng:
            from .model import pitch_name
            span = f" · {pitch_name(rng[0])}–{pitch_name(rng[1])}"
        lines.append(
            f"  [{i}] {t.name or '(unnamed)':<20} ch{t.channel + 1:<3}"
            f" {len(t.notes):>5} notes{span}"
            f" · {len(t.passthrough)} passthrough"
            + ("  [drums]" if t.is_drums else "")
        )
    return "\n".join(lines)


__all__ = ["load", "save", "summary", "musical_content", "Unsupported"]
