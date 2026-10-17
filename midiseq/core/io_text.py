"""The ``.seq`` view.

**This is a view, not a save format.** ``.mid`` is the only canonical file; mido
writes it. What lives here is what ``seq cat`` prints and what the TUI draws —
exportable, editable in vim, re-importable, but nothing depends on it and nothing
breaks if you never touch it.

That demotion is what let the format stay simple. There is no lossless-round-trip
apparatus, no annotation block, no "grid is truth for existence, annotations are
truth for detail" rule, because none of that has to hold when the real file is
the ``.mid`` sitting next to it.

Only rows that actually contain notes are printed, which is what keeps a full
song readable::

    %seq 1
    song   title "Bluesy"  ppq 480  tempo 96  time 4/4  middle-c C4
    track  2  name "Rhodes"  chan 1  program 5
    zoom   1/16

    @9     │1 e & a │2 e & a │3 e & a │4 e & a │
      A4   │········│········│x──────·│········│
      G4   │········│X──────·│········│········│
      C4   │x──────·│········│········│········│

The header declares its own ``middle-c`` convention, so a file written in one and
opened under another shifts its *labels* and not its notes. A file can't be
silently misread.
"""

from __future__ import annotations

from .glyphs import UNICODE, GlyphSet, render_text
from .grid import Zoom
from .model import DEFAULT_MIDDLE_C_OCTAVE, DEFAULT_VELOCITY, Song, pitch_name
from .render import render

#: What velocity each head glyph means when text is the only source. Declared in
#: the header of every file, so a reader never has to guess and a writer can
#: change its mind without breaking old files.
#:
#: ``x`` is the default velocity rather than the middle of the medium band: a note
#: typed into a text file should come out identical to one clicked into the grid,
#: not a near-miss. The other two sit clear of the band edges (soft <56,
#: medium <100, loud >=100) so a rebuilt file re-renders with the glyphs it had.
DEFAULT_BANDS = {"o": 40, "x": DEFAULT_VELOCITY, "X": 112}


def header(song: Song, track_index: int, zoom: Zoom,
           middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE) -> str:
    track = song.tracks[track_index]
    tempo = song.tempo_at(0)
    ts = song.timesig_at(0)
    middle_c = pitch_name(60, middle_c_octave)
    lines = [
        "%seq 1",
        f'song   title "{song.title}"  ppq {song.ppq}  tempo {tempo.bpm:.6g}  '
        f"time {ts}  middle-c {middle_c}",
        f'track  {track_index}  name "{track.name}"  chan {track.channel + 1}'
        + (f"  program {track.program}" if track.program is not None else "")
        + ("  drums yes" if track.is_drums else ""),
        f"zoom   {zoom}   bands  "
        + "  ".join(f"{k}={v}" for k, v in DEFAULT_BANDS.items()),
    ]
    return "\n".join(lines)


def dump(
    song: Song,
    track_index: int = 0,
    *,
    zoom: Zoom | None = None,
    first_bar: int = 1,
    last_bar: int | None = None,
    ghosts: bool = False,
    full: bool = False,
    lane: bool = True,
    glyphs: GlyphSet = UNICODE,
    middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE,
    bars_per_block: int = 2,
) -> str:
    """Render a track as text.

    ``full`` prints every chromatic row in range rather than only the occupied
    ones. ``bars_per_block`` wraps long songs into readable chunks instead of one
    enormous line.
    """
    zoom = zoom or Zoom(16)
    if last_bar is None:
        probe = render(song, track_index, zoom=zoom, first_bar=first_bar,
                       ghosts=False, occupied_rows_only=True)
        last_bar = probe.bars[-1].number

    out = [header(song, track_index, zoom, middle_c_octave), ""]

    bar = first_bar
    while bar <= last_bar:
        stop = min(last_bar, bar + bars_per_block - 1)
        matrix = render(
            song, track_index,
            zoom=zoom, first_bar=bar, last_bar=stop,
            ghosts=ghosts, occupied_rows_only=not full,
            middle_c_octave=middle_c_octave,
        )
        out.append(render_text(matrix, glyphs, show_lane=lane, show_header=True))
        out.append("")
        bar = stop + 1

    return "\n".join(out).rstrip() + "\n"


__all__ = ["dump", "header"]
