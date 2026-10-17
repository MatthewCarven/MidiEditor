"""Generate the example from PLAN.md as a real .mid, so `seq cat` can be checked
against the mockup we designed by hand.

Bars 9-10, 4/4, ppq 480, tempo 96. Track 1 is a held pad (which becomes the ghost
row), track 2 is the Rhodes part the mockup shows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import mido

PPQ = 480
BAR = 4 * PPQ
BEAT = PPQ


def bar_tick(bar: int) -> int:
    return (bar - 1) * BAR


def build() -> mido.MidiFile:
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)

    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(96), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    # Bar 11 drops to 3/4, so the grid has a meter change to prove itself against:
    # cell width should stay constant and the bar should simply come out shorter.
    conductor.append(mido.MetaMessage("time_signature", numerator=3, denominator=4,
                                      time=bar_tick(11)))
    mid.tracks.append(conductor)

    # Track 1 — a pad on D4, held right across both bars. Shows up as ghosts.
    pad = _track("Pad", channel=1, program=89, notes=[
        (62, bar_tick(9), BAR * 2 - 20, 54),
    ])
    # A controller sweep, so the passthrough bucket has something real to carry.
    for i in range(8):
        pad.append(mido.Message("control_change", channel=1, control=1,
                                value=20 + i * 12, time=0 if i else 0))
    mid.tracks.append(pad)

    # Track 2 — "Rhodes", the part drawn in PLAN.md
    rhodes = _track("Rhodes", channel=0, program=4, notes=[
        (60, bar_tick(9) + 0 * BEAT, BEAT - 20, 88),        # C4  quarter, beat 1
        (64, bar_tick(9) + 0 * BEAT, BEAT - 20, 88),        # E4  quarter, beat 1
        (67, bar_tick(9) + 1 * BEAT, BEAT - 20, 104),       # G4  quarter, beat 2
        (69, bar_tick(9) + 2 * BEAT, BEAT - 20, 92),        # A4  quarter, beat 3
        (63, bar_tick(9) + 3 * BEAT, BEAT // 2 - 20, 76),   # D#4 eighth,  beat 4
        (60, bar_tick(10) + 0 * BEAT, BEAT - 20, 88),       # C4  quarter, beat 1
        (67, bar_tick(10) + 1 * BEAT + 240, 60, 84),        # G4  staccato 16th on "&"
        (67, bar_tick(10) + 1 * BEAT + 360, 60, 84),        # G4  staccato 16th on "a"
        # Bar 11 is 3/4 and deliberately humanised: this note lands 37 ticks late,
        # so it can't sit on any cell boundary. The grid should draw it in the
        # nearest cell and raise the ~ flag rather than quietly quantising it.
        (65, bar_tick(11) + 0 * BEAT + 37, BEAT - 40, 70),  # F4, dragging
        (69, bar_tick(11) + 1 * BEAT, BEAT * 2 - 40, 64),   # A4 held to the bar end
    ])
    mid.tracks.append(rhodes)
    return mid


def _track(name: str, channel: int, program: int,
           notes: list[tuple[int, int, int, int]]) -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.Message("program_change", channel=channel, program=program, time=0))
    events: list[tuple[int, int, mido.Message]] = []
    for pitch, start, duration, velocity in notes:
        events.append((start, 1, mido.Message("note_on", channel=channel,
                                              note=pitch, velocity=velocity)))
        events.append((start + duration, 0, mido.Message("note_off", channel=channel,
                                                         note=pitch, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))
    cursor = 0
    for tick, _, msg in events:
        msg.time = tick - cursor
        cursor = tick
        track.append(msg)
    return track


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/bluesy.mid")
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(str(out))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
