"""The grid widget's state: what's on screen, where, and what the mouse is over.

No DearPyGui here either. This object owns the scroll position, the two zooms
(musical: cells per beat; pixel: cell width), the track being shown and the
ghosts toggle, and turns them into a paint plan on demand. ``app.py`` owns
the window and forwards wheel and key events to the methods below.

The one rule inherited from PySynthRack's zoom bug: a zoom changes
``layout.cell_w`` and nothing else. No widget resizes, no font rescales, so
nothing can feed back.
"""

from __future__ import annotations

import time

from ..core import render
from ..core.grid import GridView, Zoom
from ..core.index import SongIndex
from ..core.model import DEFAULT_MIDDLE_C_OCTAVE, Song
from ..core.render import Matrix
from .paint import Layout, PaintPlan, Primitive, hover_plan, plan, playhead_plan


def default_track(song: Song) -> int:
    """The first track with notes - the same rule ``seq cat`` uses."""
    for i, t in enumerate(song.tracks):
        if t.notes:
            return i
    return 0


def beats_text(duration: int, ppq: int) -> str:
    beats = duration / ppq
    text = f"{beats:.2f}".rstrip("0").rstrip(".")
    return f"{text or '0'} beat{'s' if beats != 1 else ''}"


class GridWidget:
    def __init__(self, song: Song, track: int | None = None, zoom: Zoom = Zoom(16), *,
                 ghosts: bool = True, middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE,
                 layout: Layout = Layout()) -> None:
        if not song.tracks:
            raise ValueError("song has no tracks")
        self.song = song
        self.index = SongIndex(song)
        self.track = default_track(song) if track is None else track
        self.zoom = zoom
        self.view = GridView(song, zoom, self.index)
        self.ghosts = ghosts
        self.middle_c_octave = middle_c_octave
        self.layout = layout

        self.width = 0
        self.height = 0
        self.first_cell = 0
        self.top_pitch = 72
        #: Note ids. The editor mutates this set in place; the matrix marks
        #: the cells and the plan outlines them.
        self.selection: set[int] = set()
        self.hover: tuple[int, int] | None = None     # (row, absolute cell)
        self.matrix: Matrix | None = None
        self.plan: PaintPlan | None = None
        self._col_index: dict[int, int] = {}          # absolute cell -> matrix column
        self.dirty = True
        self.hover_dirty = False
        self.render_ms = 0.0
        self.plan_ms = 0.0
        self.fit_pitch_range()

    # -- geometry -----------------------------------------------------------

    @property
    def visible_cols(self) -> int:
        return self.layout.visible_cols(self.width)

    @property
    def visible_rows(self) -> int:
        return self.layout.visible_rows(self.height)

    def resize(self, width: int, height: int) -> None:
        if (width, height) != (self.width, self.height):
            self.width, self.height = width, height
            self.dirty = True

    # -- what's shown ---------------------------------------------------------

    def set_track(self, index: int) -> None:
        if 0 <= index < len(self.song.tracks) and index != self.track:
            self.track = index
            # You can't damage a track you weren't looking at: the other
            # track's notes are ghosts now, and ghosts can't be selected.
            self.selection.clear()
            self.fit_pitch_range()
            self.dirty = True

    def fit_pitch_range(self) -> None:
        """Put the track's highest note (plus a little air) at the top."""
        rng = self.song.tracks[self.track].pitch_range() or (60, 72)
        self.top_pitch = min(127, rng[1] + 2)
        self.dirty = True

    def set_ghosts(self, on: bool) -> None:
        if on != self.ghosts:
            self.ghosts = on
            self.dirty = True

    def set_middle_c(self, octave: int) -> None:
        if octave != self.middle_c_octave:
            self.middle_c_octave = octave
            self.dirty = True

    # -- navigation -----------------------------------------------------------

    def scroll_cells(self, n: int) -> None:
        self.first_cell = max(0, self.first_cell + n)
        self.dirty = True

    def scroll_bars(self, n: int) -> None:
        bar = self.view.bars.bar_containing(self.view.cell_to_tick(self.first_cell))
        self.go_to_bar(bar.number + n)

    def go_to_bar(self, number: int) -> None:
        number = max(1, number)
        self.first_cell = self.view.bar_cell_range(self.view.bars.bar(number))[0]
        self.dirty = True

    def scroll_pitches(self, n: int) -> None:
        """Positive moves the view up (higher pitches at the top)."""
        self.top_pitch = max(self.visible_rows - 1, min(127, self.top_pitch + n))
        self.dirty = True

    def tick_visible(self, tick: int) -> bool:
        return self.first_cell <= self.view.tick_to_cell(tick) < self.first_cell + self.visible_cols

    def follow(self, tick: int) -> bool:
        """Keep a rolling playhead on screen: once it passes the right edge,
        turn the page so its bar starts at the left. Page-wise rather than
        continuous, so the notes you are editing hold still until they must
        move. A playhead off to the *left* is left alone - you scrolled
        ahead on purpose. Returns whether the view moved."""
        cell = self.view.tick_to_cell(tick)
        if cell < self.first_cell + self.visible_cols:
            return False
        self.go_to_bar(self.view.bars.bar_containing(tick).number)
        if cell >= self.first_cell + self.visible_cols:    # a bar wider than the window
            self.first_cell = cell
        return True

    def set_cell_w(self, cell_w: int) -> None:
        new = self.layout.with_cell_w(cell_w)
        if new != self.layout:
            self.layout = new
            self.dirty = True

    def zoom_pixels(self, direction: int) -> None:
        factor = 1.25 if direction > 0 else 0.8
        self.set_cell_w(round(self.layout.cell_w * factor))

    def set_zoom(self, zoom: Zoom) -> None:
        """Change the musical zoom keeping the left edge on the same tick."""
        if zoom == self.zoom:
            return
        left_tick = self.view.cell_to_tick(self.first_cell)
        self.zoom = zoom
        self.view = GridView(self.song, zoom, self.index)
        self.first_cell = self.view.tick_to_cell(left_tick)
        self.dirty = True

    def zoom_finer(self) -> None:
        self.set_zoom(self.zoom.finer())

    def zoom_coarser(self) -> None:
        self.set_zoom(self.zoom.coarser())

    # -- the picture ----------------------------------------------------------

    def build(self) -> PaintPlan:
        """Render the visible bars and rows, then lay them out. Records how
        long each half took, because the spike showed render.py was the
        bigger cost and it is worth keeping an eye on."""
        t0 = time.perf_counter()
        first = self.first_cell
        last = first + self.visible_cols - 1
        view = self.view
        first_bar = view.bars.bar_containing(view.cell_to_tick(first)).number
        last_bar = view.bars.bar_containing(view.cell_to_tick(last)).number
        high = self.top_pitch
        low = max(0, high - self.visible_rows + 1)
        self.matrix = render.render(
            self.song, self.track, zoom=self.zoom,
            first_bar=first_bar, last_bar=last_bar,
            low_pitch=low, high_pitch=high, ghosts=self.ghosts,
            selection=self.selection,
            middle_c_octave=self.middle_c_octave, index=self.index,
        )
        self._col_index = {c.cell: i for i, c in enumerate(self.matrix.columns)}
        t1 = time.perf_counter()
        self.plan = plan(self.matrix, self.layout, first_cell=first,
                         width=self.width, height=self.height)
        t2 = time.perf_counter()
        self.render_ms = (t1 - t0) * 1e3
        self.plan_ms = (t2 - t1) * 1e3
        self.dirty = False
        self.hover_dirty = True
        return self.plan

    # -- lookups for the editor ---------------------------------------------------

    def cell_info(self, row: int, col: int):
        """``(Row, Cell)`` for a visible square, or None off the matrix."""
        m = self.matrix
        if m is None or not 0 <= row < m.height:
            return None
        ci = self._col_index.get(col)
        if ci is None:
            return None
        return m.rows[row], m.rows[row].cells[ci]

    def pitch_at_row(self, row: int) -> int | None:
        m = self.matrix
        if m is None or not 0 <= row < m.height:
            return None
        return m.rows[row].pitch

    def row_of_pitch(self, pitch: int) -> int | None:
        m = self.matrix
        if m is None:
            return None
        row = self.top_pitch - pitch
        return row if 0 <= row < m.height else None

    def tick_at_col(self, col: int) -> int:
        return self.view.cell_to_tick(col)

    # -- the mouse --------------------------------------------------------------

    def set_hover(self, x: float | None, y: float | None) -> bool:
        """Point the mouse at a pixel (or None for "not over the grid").
        Returns whether the hovered cell changed."""
        if x is None or y is None or self.plan is None:
            new = None
        else:
            new = self.layout.hit_test(x, y, self.first_cell, self.plan.rows, self.width)
        if new != self.hover:
            self.hover = new
            self.hover_dirty = True
            return True
        return False

    def hover_items(self) -> list[Primitive]:
        self.hover_dirty = False
        if self.hover is None:
            return []
        row, cell = self.hover
        return hover_plan(self.layout, self.first_cell, row, cell)

    def playhead_items(self, tick: int) -> list[Primitive]:
        """The playhead line at ``tick``, in this view; nothing off screen."""
        if self.plan is None:
            return []
        return playhead_plan(self.layout, self.first_cell, self.view.ticks_per_cell, tick,
                             self.plan.cols, self.plan.rows)

    def status(self) -> str:
        """The status line: where the mouse is, and what's under it."""
        m = self.matrix
        if m is None or self.hover is None or self.hover[0] >= m.height:
            return self._status_tail()
        row_i, cell = self.hover
        row = m.rows[row_i]
        tick = self.view.cell_to_tick(cell)
        parts = [row.label, f"MIDI {row.pitch}", self.view.bars.position(tick)]
        ci = self._col_index.get(cell)
        if ci is not None:
            c = row.cells[ci]
            if c.is_ghost:
                parts.append("ghost")
            elif c.nid is not None:
                note = self.song.tracks[self.track].note_by_id(c.nid)
                if note is not None:
                    parts.append(f"len {beats_text(note.duration, self.song.ppq)}")
                parts.append(f"vel {c.velocity}")
                if c.off_grid:
                    parts.append("~ off grid")
        return " . ".join(parts) + "  |  " + self._status_tail()

    def _status_tail(self) -> str:
        t = self.song.tracks[self.track]
        name = f' "{t.name}"' if t.name else ""
        return (f"trk {self.track}{name} ch{t.channel + 1}  zoom {self.zoom}  "
                f"cell {self.layout.cell_w}px  ghosts {'on' if self.ghosts else 'off'}")


__all__ = ["GridWidget", "default_track", "beats_text"]
