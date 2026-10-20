"""Model to a cell-state matrix.

This is the shared surface. Every UI reads the same matrix:

    render.py  ──►  cell-state matrix  ──┬──►  glyphs.py  ──►  TUI, .seq text view
                                         └──►  drawlist rectangles  ──►  GUI (DearPyGui)

That split matters. An earlier draft had this module producing *glyph rows*, which
would have quietly forced the GUI widget to draw characters when it should be
painting rectangles. Cell state is the honest shared abstraction: both UIs agree
by construction, and neither has to pretend to be the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .grid import Bar, GridView, Zoom
from .index import SongIndex
from .edit import SCALES
from .model import (
    DEFAULT_MIDDLE_C_OCTAVE,
    Song,
    Track,
    is_accidental,
    pitch_name,
)


class Event(Enum):
    """What happens at the *start* of a cell — the first of the two characters."""

    EMPTY = "empty"
    HEAD = "head"           # a note begins here
    SUSTAIN = "sustain"     # a note begun earlier is passing through
    GHOST_HEAD = "ghost_head"
    GHOST_SUSTAIN = "ghost_sustain"


class Tie(Enum):
    """Whether sound carries into the next cell — the second character.

    This is what buys note length for free. If a square has a beginning it has a
    remainder, so the tie says whether the remainder is still sounding. Length is
    never ambiguous, even when notes butt up against each other.
    """

    SILENT = "silent"       # released within this cell
    CARRY = "carry"         # still sounding as the next cell starts


#: Velocity bands for the head glyph. The exact number lives in the velocity lane
#: and the status bar; the band is what you read at a glance.
#:
#: Aligned to the dynamics below rather than splitting 1-127 into equal thirds —
#: equal thirds put the default velocity of 80 within a hair of "loud", which
#: would make almost everything you write render as ``X``. These boundaries put
#: the default in the middle of "medium" with room to move in both directions,
#: which is the same reasoning as the default velocity itself.
VELOCITY_BANDS = ((56, "soft"), (100, "medium"), (128, "loud"))


def velocity_band(velocity: int) -> str:
    for ceiling, name in VELOCITY_BANDS:
        if velocity < ceiling:
            return name
    return "loud"


#: Dynamics for the velocity lane axis. Labelling it pp/p/mf/f rather than 0-127
#: means "make this softer" is something you aim at rather than a number you guess.
DYNAMICS = (
    (32, "pp"),
    (56, "p"),
    (80, "mp"),
    (100, "mf"),
    (116, "f"),
    (128, "ff"),
)


def dynamic_name(velocity: int) -> str:
    for ceiling, name in DYNAMICS:
        if velocity < ceiling:
            return name
    return "ff"


@dataclass
class Cell:
    event: Event = Event.EMPTY
    tie: Tie = Tie.SILENT
    nid: int | None = None
    velocity: int = 0
    off_grid: bool = False
    selected: bool = False
    #: How much of this cell the note actually covers, for the last cell of a
    #: note only (1.0 everywhere else). The GUI draws a rectangle that stops
    #: where the note stops rather than at the cell boundary; the glyphs never
    #: look at it, because the tie already tells that story honestly.
    end_fill: float = 1.0

    @property
    def is_note(self) -> bool:
        return self.event in (Event.HEAD, Event.SUSTAIN)

    @property
    def is_ghost(self) -> bool:
        return self.event in (Event.GHOST_HEAD, Event.GHOST_SUSTAIN)

    @property
    def band(self) -> str:
        return velocity_band(self.velocity)


@dataclass
class Row:
    pitch: int
    label: str
    accidental: bool        # shaded, so the black-key pattern reads at a glance
    cells: list[Cell]
    in_scale: bool | None = None  # under a scale filter: in the key, or not; None without one
    off_grid_flag: bool = False   # the `~` gutter mark — "this row is drawn approximately"


@dataclass
class Column:
    cell: int               # absolute cell index
    tick: int
    bar: int
    beat: int               # 1-based
    subdivision: int        # 0-based within the beat
    bar_start: bool
    beat_start: bool


@dataclass
class Matrix:
    """One screenful (or one file's worth) of grid."""

    rows: list[Row]
    columns: list[Column]
    bars: list[Bar]
    zoom: Zoom
    track_index: int
    track_name: str
    is_drums: bool
    ppq: int
    middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE
    velocity_lane: list[int] = field(default_factory=list)  # peak velocity per column

    @property
    def width(self) -> int:
        return len(self.columns)

    @property
    def height(self) -> int:
        return len(self.rows)

    def cell(self, row: int, col: int) -> Cell:
        return self.rows[row].cells[col]


# --------------------------------------------------------------------------
# GM drum names — channel 10 swaps note names for these, and the grid becomes a
# drum machine without changing anything else.
# --------------------------------------------------------------------------

GM_DRUMS = {
    35: "AcBass", 36: "Kick", 37: "Stick", 38: "Snr", 39: "Clap", 40: "SnrEl",
    41: "TomLF", 42: "HH-c", 43: "TomLo", 44: "HH-p", 45: "TomMd", 46: "HH-o",
    47: "TomMH", 48: "TomHM", 49: "Crash", 50: "TomHi", 51: "Ride", 52: "China",
    53: "Bell", 54: "Tamb", 55: "Splash", 56: "Cowbll", 57: "Crsh2", 58: "Vibra",
    59: "Ride2", 60: "BongoH", 61: "BongoL", 62: "CongaH", 63: "CongaO",
    64: "CongaL", 65: "TimbH", 66: "TimbL", 67: "AgogoH", 68: "AgogoL",
    69: "Cabasa", 70: "Maraca", 71: "WhistS", 72: "WhistL", 73: "GuiroS",
    74: "GuiroL", 75: "Claves", 76: "WoodHi", 77: "WoodLo", 78: "CuicaH",
    79: "CuicaL", 80: "TriMut", 81: "TriOpn",
}


def row_label(pitch: int, drums: bool, middle_c_octave: int) -> str:
    if drums:
        return GM_DRUMS.get(pitch, f"#{pitch}")
    return pitch_name(pitch, middle_c_octave)


# --------------------------------------------------------------------------
# The render itself
# --------------------------------------------------------------------------

def render(
    song: Song,
    track_index: int = 0,
    *,
    zoom: Zoom | None = None,
    first_bar: int = 1,
    last_bar: int | None = None,
    low_pitch: int | None = None,
    high_pitch: int | None = None,
    ghosts: bool = True,
    selection: set[int] | None = None,
    middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE,
    occupied_rows_only: bool = False,
    index: SongIndex | None = None,
    scale: tuple[int, str] | None = None,
) -> Matrix:
    """Build the cell-state matrix for one track.

    ``ghosts`` draws the other tracks faintly behind this one — context you can
    see but can't accidentally edit.
    ``scale`` (root pitch class, scale name) marks each row in or out of that
    key, for the views to shade; a drum track has no key.
    """
    if not song.tracks:
        raise ValueError("song has no tracks")
    if not 0 <= track_index < len(song.tracks):
        raise IndexError(f"no track {track_index} (song has {len(song.tracks)})")

    zoom = zoom or Zoom(16)
    selection = selection or set()
    # ``index`` is a SongIndex a UI keeps across repaints; without one every
    # track is scanned, which is fine for a single print.
    view = GridView(song, zoom, index)
    track = song.tracks[track_index]

    # -- columns ----------------------------------------------------------

    if last_bar is None:
        end = song.end_tick()
        last_bar = view.bars.bar_containing(max(0, end - 1)).number if end else first_bar
    last_bar = max(first_bar, last_bar)
    bars = view.bars.bars(first_bar, last_bar)

    columns: list[Column] = []
    for bar in bars:
        start_cell, end_cell = view.bar_cell_range(bar)
        cells_per_beat = zoom.cells_per_beat(bar.timesig.denominator)
        for i, c in enumerate(range(start_cell, end_cell)):
            beat, sub = divmod(i, cells_per_beat)
            columns.append(
                Column(
                    cell=c,
                    tick=view.cell_to_tick(c),
                    bar=bar.number,
                    beat=beat + 1,
                    subdivision=sub,
                    bar_start=(i == 0),
                    beat_start=(sub == 0),
                )
            )
    if not columns:
        raise ValueError("empty bar range")

    first_cell, last_cell = columns[0].cell, columns[-1].cell
    col_of = {c.cell: i for i, c in enumerate(columns)}

    # -- rows -------------------------------------------------------------

    visible = view.notes_in_cell_range(track, first_cell, last_cell)
    if low_pitch is None or high_pitch is None:
        rng = track.pitch_range()
        if rng is None:
            lo_auto, hi_auto = 60, 72
        else:
            lo_auto, hi_auto = rng
            lo_auto, hi_auto = max(0, lo_auto - 2), min(127, hi_auto + 2)
        low_pitch = lo_auto if low_pitch is None else low_pitch
        high_pitch = hi_auto if high_pitch is None else high_pitch
    low_pitch, high_pitch = max(0, low_pitch), min(127, high_pitch)
    if low_pitch > high_pitch:
        low_pitch, high_pitch = high_pitch, low_pitch

    pitches = list(range(high_pitch, low_pitch - 1, -1))   # high at the top
    if occupied_rows_only:
        used = {n.pitch for n in visible}
        if ghosts:
            for i, other in enumerate(song.tracks):
                if i != track_index:
                    used |= {n.pitch for n in view.notes_in_cell_range(other, first_cell, last_cell)}
        pitches = [p for p in pitches if p in used] or pitches[:1]

    rows = [
        Row(
            pitch=p,
            label=row_label(p, track.is_drums, middle_c_octave),
            accidental=is_accidental(p),
            cells=[Cell() for _ in columns],
            in_scale=(None if scale is None or track.is_drums
                      else ((p - scale[0]) % 12) in SCALES.get(scale[1], ())),
        )
        for p in pitches
    ]
    row_of = {r.pitch: i for i, r in enumerate(rows)}

    # -- paint ghosts first, so the editable track always wins the square ---

    if ghosts:
        for i, other in enumerate(song.tracks):
            if i == track_index:
                continue
            for note in view.notes_in_cell_range(other, first_cell, last_cell):
                _paint(note, view, rows, row_of, col_of, selection, ghost=True)

    for note in visible:
        _paint(note, view, rows, row_of, col_of, selection, ghost=False)

    # -- velocity lane ----------------------------------------------------

    lane = [0] * len(columns)
    for note in visible:
        span = view.span(note)
        ci = col_of.get(span.first_cell)
        if ci is not None:
            lane[ci] = max(lane[ci], note.velocity)

    return Matrix(
        rows=rows,
        columns=columns,
        bars=bars,
        zoom=zoom,
        track_index=track_index,
        track_name=track.name,
        is_drums=track.is_drums,
        ppq=song.ppq,
        middle_c_octave=middle_c_octave,
        velocity_lane=lane,
    )


def _paint(note, view: GridView, rows, row_of, col_of, selection, *, ghost: bool) -> None:
    ri = row_of.get(note.pitch)
    if ri is None:
        return
    row = rows[ri]
    span = view.span(note)

    if span.off_grid and not ghost:
        # Draw it in the nearest cell but flag the row: the picture is
        # approximate and we say so, rather than quietly quantising.
        row.off_grid_flag = True

    head_event = Event.GHOST_HEAD if ghost else Event.HEAD
    body_event = Event.GHOST_SUSTAIN if ghost else Event.SUSTAIN

    # The fraction of the last cell the note really covers. A zero-length note
    # still gets a sliver, because nothing ever vanishes for being too short.
    tpc = view.ticks_per_cell
    end_fill = max(0.1, min(1.0, (note.end - span.last_cell * tpc) / tpc))

    for c in range(span.first_cell, span.last_cell + 1):
        ci = col_of.get(c)
        if ci is None:
            continue
        cell = row.cells[ci]
        if ghost and cell.is_note:
            continue    # a real note already owns this square
        cell.event = head_event if c == span.first_cell else body_event
        # The tie says "still sounding as the next cell begins". The last cell is
        # by definition where the note stops, so it always releases.
        cell.tie = Tie.SILENT if c == span.last_cell else Tie.CARRY
        cell.nid = note.nid
        cell.velocity = note.velocity
        cell.off_grid = span.off_grid
        cell.selected = (not ghost) and note.nid in selection
        cell.end_fill = end_fill if c == span.last_cell else 1.0


__all__ = [
    "render", "Matrix", "Row", "Column", "Cell", "Event", "Tie",
    "velocity_band", "dynamic_name", "row_label", "GM_DRUMS", "DYNAMICS",
]
