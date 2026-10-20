"""The grid as text: what's on screen, where the cursor is, and the picture.

The terminal's counterpart to ``ui_gui/grid.py``, and like it toolkit-free:
no Textual here. This object owns the scroll position, the musical zoom,
the track and the ghosts toggle, plus the two things a keyboard UI has that
a mouse UI doesn't - a cursor, which is a pitch and an absolute cell, and
the rule that the view follows it. On demand it renders the visible bars
and rows through ``core/render.py`` and ``core/glyphs.py``, the renderer
``seq cat`` prints with, so the terminal shows the same picture by
construction, and hands back a ``Frame``: the lines of text plus styled
spans by *role* (a note, a selected note, a ghost, an accidental row, the
cursor, the playhead), so the Textual widget only maps roles to colours.

The cursor is marked in the text itself as well - ``▼`` over its column in
the bar header and ``>`` in the gutter of its row, both from ``glyphs.py`` -
so the picture reads on a terminal with no colour at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..core import render
from ..core.glyphs import (
    LANE_ROWS, UNICODE, GlyphSet, bar_header, grid_rows, rule, ruler, velocity_lane,
)
from ..core.grid import GridView, Zoom
from ..core.index import SongIndex
from ..core.model import DEFAULT_MIDDLE_C_OCTAVE, Note, Song
from ..core.render import Matrix, Row, row_label
from ..ui_common import beats_text, default_track

HEADER_LINES = 2                    # the bar header and the ruler
LANE_LINES = len(LANE_ROWS) + 2     # the lane between its two rules
MIN_GRID_ROWS = 4                   # fewer than this and the lane gives way to pitches


@dataclass(frozen=True)
class Span:
    """``lines[y][x0:x1]`` plays ``role``: note, selected, ghost, accidental,
    playhead or cursor. Later spans layer over earlier ones."""

    y: int
    x0: int
    x1: int
    role: str


@dataclass
class Frame:
    """One screenful, as text plus roles."""

    lines: list[str]
    spans: list[Span]
    width: int
    height: int
    cursor: tuple[int, int] | None = None     # (x, y) of the cursor cell's first character
    playhead_x: int | None = None
    columns: int = 0                           # cells across that fit


def _gutter_width(labels: Iterable[str]) -> int:
    # The same rule as glyphs.py: room for the longest label plus the shade mark.
    return max(4, max((len(label) for label in labels), default=3) + 1)


def _slice(m: Matrix, start: int, count: int) -> Matrix:
    """The matrix from column ``start``, ``count`` columns wide. Bars with no
    column left are dropped so the first bar shown gets the corner."""
    cols = m.columns[start:start + count]
    shown = {c.bar for c in cols}
    return Matrix(
        rows=[Row(r.pitch, r.label, r.accidental, r.cells[start:start + count], r.off_grid_flag)
              for r in m.rows],
        columns=cols,
        bars=[b for b in m.bars if b.number in shown],
        zoom=m.zoom, track_index=m.track_index, track_name=m.track_name,
        is_drums=m.is_drums, ppq=m.ppq, middle_c_octave=m.middle_c_octave,
        velocity_lane=m.velocity_lane[start:start + count],
    )


class TextView:
    def __init__(self, song: Song, track: int | None = None, zoom: Zoom = Zoom(16), *,
                 ghosts: bool = True, middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE,
                 lane: bool = True, glyphs: GlyphSet = UNICODE) -> None:
        if not song.tracks:
            raise ValueError("song has no tracks")
        self.song = song
        self.index = SongIndex(song)
        self.track = default_track(song) if track is None else track
        self.zoom = zoom
        self.view = GridView(song, zoom, self.index)
        self.ghosts = ghosts
        self.middle_c_octave = middle_c_octave
        self.lane = lane
        self.glyphs = glyphs

        self.width, self.height = 80, 24
        self.first_cell = 0
        self.top_pitch = 72
        self.cursor_pitch = 60
        self.cursor_cell = 0
        #: Note ids. The editor mutates this set in place; the matrix marks
        #: the cells and the frame gives them the ``selected`` role.
        self.selection: set[int] = set()
        #: The playhead's tick, set by the editor before each build.
        self.playhead: int | None = None
        self.matrix: Matrix | None = None
        self.frame: Frame | None = None
        self._xs: list[int] = []                   # character x of each matrix column
        self._col_index: dict[int, int] = {}       # absolute cell -> matrix column
        self.dirty = True
        self.fit_pitch_range()
        self.home_cursor()

    # -- geometry -----------------------------------------------------------

    @property
    def lane_shown(self) -> bool:
        return self.lane and self.height - HEADER_LINES - LANE_LINES >= MIN_GRID_ROWS

    @property
    def visible_rows(self) -> int:
        return max(1, self.height - HEADER_LINES - (LANE_LINES if self.lane_shown else 0))

    @property
    def visible_cols(self) -> int:
        """Cells across, from the left edge, that fit."""
        return len(self._walk(self.first_cell))

    def resize(self, width: int, height: int) -> None:
        if (width, height) != (self.width, self.height):
            self.width, self.height = max(8, width), max(3, height)
            self.ensure_cursor_visible()
            self.dirty = True

    def _gutter(self) -> int:
        low = max(0, self.top_pitch - self.visible_rows + 1)
        drums = self.song.tracks[self.track].is_drums
        return _gutter_width(row_label(p, drums, self.middle_c_octave)
                             for p in range(low, self.top_pitch + 1))

    def _is_beat_start(self, cell: int) -> bool:
        """Whether a separator precedes this cell: the same rule as the
        renderer's ``Column.beat_start`` (a bar start is one too)."""
        view = self.view
        bar = view.bars.bar_containing(view.cell_to_tick(cell))
        first, _ = view.bar_cell_range(bar)
        return (cell - first) % self.zoom.cells_per_beat(bar.timesig.denominator) == 0

    def _walk(self, first: int) -> list[int]:
        """Character x of each cell from ``first`` that fits across the
        width: two characters a cell, one more before each beat."""
        xs: list[int] = []
        x, cell = self._gutter(), first
        while True:
            if self._is_beat_start(cell):
                x += 1
            if x + 2 > self.width:
                break
            xs.append(x)
            x += 2
            cell += 1
        return xs

    def _bar_start(self, cell: int) -> int:
        view = self.view
        return view.bar_cell_range(view.bars.bar_containing(view.cell_to_tick(cell)))[0]

    # -- what's shown ---------------------------------------------------------

    def set_track(self, index: int) -> None:
        if 0 <= index < len(self.song.tracks) and index != self.track:
            self.track = index
            # You can't damage a track you weren't looking at: the other
            # track's notes are ghosts now, and ghosts can't be selected.
            self.selection.clear()
            self.fit_pitch_range()

    def fit_pitch_range(self) -> None:
        """The track's highest note (plus a little air) at the top, and the
        cursor pulled into the rows that shows."""
        rng = self.song.tracks[self.track].pitch_range() or (60, 72)
        self.top_pitch = min(127, rng[1] + 2)
        low = max(0, self.top_pitch - self.visible_rows + 1)
        self.cursor_pitch = max(low, min(self.top_pitch, self.cursor_pitch))
        self.ensure_cursor_visible()

    def set_ghosts(self, on: bool) -> None:
        if on != self.ghosts:
            self.ghosts = on
            self.dirty = True

    def set_zoom(self, zoom: Zoom) -> None:
        """Change the musical zoom keeping the left edge and the cursor on
        the same ticks."""
        if zoom == self.zoom:
            return
        left_tick, cursor_tick = self.view.cell_to_tick(self.first_cell), self.cursor_tick
        self.zoom = zoom
        self.view = GridView(self.song, zoom, self.index)
        self.first_cell = self.view.tick_to_cell(left_tick)
        self.cursor_cell = self.view.tick_to_cell(cursor_tick)
        self.ensure_cursor_visible()

    def zoom_finer(self) -> None:
        self.set_zoom(self.zoom.finer())

    def zoom_coarser(self) -> None:
        self.set_zoom(self.zoom.coarser())

    # -- the cursor ------------------------------------------------------------

    @property
    def cursor_tick(self) -> int:
        return self.view.cell_to_tick(self.cursor_cell)

    def home_cursor(self) -> None:
        """The cursor on the track's first note, or middle C at the start."""
        notes = self.song.tracks[self.track].notes
        if notes:
            first = min(notes, key=lambda n: (n.start, n.pitch))
            self.cursor_pitch, self.cursor_cell = first.pitch, self.view.tick_to_cell(first.start)
        else:
            self.cursor_pitch, self.cursor_cell = 60, 0
        self.ensure_cursor_visible()

    def move_cursor(self, d_pitch: int = 0, d_cells: int = 0) -> None:
        self.cursor_pitch = max(0, min(127, self.cursor_pitch + d_pitch))
        self.cursor_cell = max(0, self.cursor_cell + d_cells)
        self.ensure_cursor_visible()

    def put_cursor(self, pitch: int | None = None, cell: int | None = None) -> None:
        if pitch is not None:
            self.cursor_pitch = max(0, min(127, pitch))
        if cell is not None:
            self.cursor_cell = max(0, cell)
        self.ensure_cursor_visible()

    def move_cursor_bars(self, n: int) -> None:
        """To the start of the bar ``n`` away. Backwards from mid-bar goes
        to this bar's start first, the way a text editor's word motion
        stops at the start of the word you are in."""
        bar = self.view.bars.bar_containing(self.cursor_tick)
        if n < 0 and self.cursor_cell > self.view.bar_cell_range(bar)[0]:
            n += 1
        number = max(1, bar.number + n)
        self.cursor_cell = self.view.bar_cell_range(self.view.bars.bar(number))[0]
        self.ensure_cursor_visible()

    def ensure_cursor_visible(self) -> None:
        """Scroll so the cursor is on screen: pitches by the row, time by the
        bar - the page turns to the cursor's bar, or to the cursor itself
        when the bar is wider than the screen."""
        rows = self.visible_rows
        if self.cursor_pitch > self.top_pitch:
            self.top_pitch = self.cursor_pitch
        elif self.cursor_pitch <= self.top_pitch - rows:
            self.top_pitch = self.cursor_pitch + rows - 1
        self.top_pitch = max(min(rows - 1, 127), min(127, self.top_pitch))

        if self.cursor_cell < self.first_cell:
            self.first_cell = self._bar_start(self.cursor_cell)
            if self.cursor_cell >= self.first_cell + len(self._walk(self.first_cell)):
                self.first_cell = self.cursor_cell
        elif self.cursor_cell >= self.first_cell + len(self._walk(self.first_cell)):
            start = self._bar_start(self.cursor_cell)
            if start <= self.first_cell or self.cursor_cell >= start + len(self._walk(start)):
                start = self.cursor_cell
            self.first_cell = start
        self.dirty = True

    # -- navigation -------------------------------------------------------------

    def go_to_bar(self, number: int) -> None:
        """The bar at the left edge, and the cursor on its first cell."""
        number = max(1, number)
        self.first_cell = self.view.bar_cell_range(self.view.bars.bar(number))[0]
        self.cursor_cell = self.first_cell
        self.dirty = True

    def last_bar(self) -> int:
        end = self.song.end_tick()
        return self.view.bars.bar_containing(max(0, end - 1)).number if end else 1

    def tick_visible(self, tick: int) -> bool:
        cell = self.view.tick_to_cell(tick)
        return self.first_cell <= cell < self.first_cell + self.visible_cols

    def follow(self, tick: int) -> bool:
        """Keep a rolling playhead on screen: once it passes the right edge,
        turn the page so its bar starts at the left. A playhead off to the
        *left* is left alone - you scrolled ahead on purpose. Returns
        whether the view moved."""
        cell = self.view.tick_to_cell(tick)
        if cell < self.first_cell + self.visible_cols:
            return False
        self.first_cell = self._bar_start(cell)
        if cell >= self.first_cell + self.visible_cols:    # a bar wider than the screen
            self.first_cell = cell
        self.dirty = True
        return True

    # -- lookups for the editor ----------------------------------------------------

    def note_at(self, pitch: int, cell: int) -> Note | None:
        """The current track's note sounding in a cell at a pitch - when
        several overlap, the one the renderer paints there (the latest to
        start)."""
        track = self.song.tracks[self.track]
        hits = [n for n in self.view.notes_in_cell_range(track, cell, cell) if n.pitch == pitch]
        return max(hits, key=lambda n: (n.start, n.nid)) if hits else None

    def note_at_cursor(self) -> Note | None:
        return self.note_at(self.cursor_pitch, self.cursor_cell)

    def row_of_pitch(self, pitch: int) -> int | None:
        row = self.top_pitch - pitch
        return row if 0 <= row < self.visible_rows else None

    # -- the picture ------------------------------------------------------------

    def build(self) -> Frame:
        """Render the visible bars and rows and lay the lines out."""
        view = self.view
        first = self.first_cell
        first_bar = view.bars.bar_containing(view.cell_to_tick(first)).number
        last_bar = view.bars.bar_containing(view.cell_to_tick(first + max(1, self.width // 2))).number
        high = self.top_pitch
        low = max(0, high - self.visible_rows + 1)
        m = render.render(
            self.song, self.track, zoom=self.zoom,
            first_bar=first_bar, last_bar=last_bar,
            low_pitch=low, high_pitch=high, ghosts=self.ghosts,
            selection=self.selection, middle_c_octave=self.middle_c_octave,
            index=self.index,
        )
        start = next((i for i, c in enumerate(m.columns) if c.cell >= first), len(m.columns) - 1)
        gutter = _gutter_width(r.label for r in m.rows)
        xs: list[int] = []
        x = gutter
        for col in m.columns[start:]:
            if col.beat_start or col.bar_start:
                x += 1
            if x + 2 > self.width:
                break
            xs.append(x)
            x += 2
        if not xs:                                   # too narrow for one cell: show one anyway
            xs = [gutter + 1]
        m = _slice(m, start, len(xs))
        self.matrix = m
        self._xs = xs
        self._col_index = {c.cell: i for i, c in enumerate(m.columns)}

        g = self.glyphs
        ci_cur = self._col_index.get(self.cursor_cell)
        ri_cur = self.row_of_pitch(self.cursor_pitch)
        lines = [bar_header(m, g, gutter, cursor_column=ci_cur), ruler(m, g, gutter)]
        lines += grid_rows(m, g, gutter, cursor_row=ri_cur)
        if self.lane_shown:
            lines.append(rule(m, g, gutter, top=True))
            lines.extend(velocity_lane(m, g, gutter))
            lines.append(rule(m, g, gutter, top=False))
        lines = [line[:self.width].ljust(self.width) for line in lines]

        spans: list[Span] = []
        y0 = HEADER_LINES
        for ri, row in enumerate(m.rows):
            if row.accidental:
                spans.append(Span(y0 + ri, 0, self.width, "accidental"))
        playhead_x = None
        if self.playhead is not None:
            pi = self._col_index.get(view.tick_to_cell(self.playhead))
            if pi is not None:
                playhead_x = xs[pi]
                for y in range(len(lines)):
                    spans.append(Span(y, playhead_x, playhead_x + 2, "playhead"))
        for ri, row in enumerate(m.rows):
            for ci, cell in enumerate(row.cells):
                if cell.is_note:
                    spans.append(Span(y0 + ri, xs[ci], xs[ci] + 2,
                                      "selected" if cell.selected else "note"))
                elif cell.is_ghost:
                    spans.append(Span(y0 + ri, xs[ci], xs[ci] + 2, "ghost"))
        cursor = None
        if ci_cur is not None and ri_cur is not None:
            cursor = (xs[ci_cur], y0 + ri_cur)
            spans.append(Span(cursor[1], cursor[0], cursor[0] + 2, "cursor"))

        self.frame = Frame(lines, spans, self.width, len(lines), cursor, playhead_x, len(xs))
        self.dirty = False
        return self.frame

    # -- the status line ----------------------------------------------------------

    def status(self) -> str:
        """Where the cursor is and what's under it, then the view: the same
        words the window's status line uses for the hovered square."""
        pitch = self.cursor_pitch
        track = self.song.tracks[self.track]
        parts = [row_label(pitch, track.is_drums, self.middle_c_octave), f"MIDI {pitch}",
                 self.view.bars.position(self.cursor_tick)]
        note = self.note_at_cursor()
        if note is not None:
            parts.append(f"len {beats_text(note.duration, self.song.ppq)}")
            parts.append(f"vel {note.velocity}")
            if self.view.span(note).off_grid:
                parts.append("~ off grid")
        return " . ".join(parts) + "  |  " + self._status_tail()

    def _status_tail(self) -> str:
        t = self.song.tracks[self.track]
        name = f' "{t.name}"' if t.name else ""
        tail = f"trk {self.track}{name} ch{t.channel + 1}  zoom {self.zoom}"
        if not self.ghosts:
            tail += "  ghosts off"
        return tail


__all__ = ["TextView", "Frame", "Span", "HEADER_LINES", "LANE_LINES"]
