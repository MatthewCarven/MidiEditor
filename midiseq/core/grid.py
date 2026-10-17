"""Model to cells, and back.

The grid's horizontal axis is *musical* time. That choice is why tempo changes
cost the display nothing — a rall. from 120 to 60 changes nothing on screen,
because only the playhead's clock cares about seconds. Meter changes make bars
narrower or wider while cell width stays constant.

Zoom is expressed as a note value: at 1/16, one cell is a sixteenth note, so a
beat gets four cells.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .model import Song, TimeSigChange


@dataclass(frozen=True)
class Zoom:
    """One cell = a 1/``denominator`` note."""

    denominator: int = 16

    #: Coarser than 1/4 would put more than one beat in a cell, which the ruler
    #: can't label honestly. Not a useful zoom for a note grid anyway.
    SUPPORTED = (4, 8, 16, 32, 64)

    def __post_init__(self) -> None:
        if self.denominator not in self.SUPPORTED:
            raise ValueError(
                f"unsupported zoom 1/{self.denominator} "
                f"(supported: {', '.join('1/%d' % d for d in self.SUPPORTED)})"
            )

    def cells_per_beat(self, timesig_denominator: int = 4) -> int:
        """Depends on the *meter*, not just the zoom.

        A beat is one unit of the time signature's denominator, so in 4/4 a beat
        is a quarter note and 1/16 zoom gives four cells; in 6/8 a beat is an
        eighth and the same zoom gives two. Getting this from the zoom alone
        would mislabel the ruler in compound time.
        """
        return max(1, self.denominator // timesig_denominator)

    def ticks_per_cell(self, ppq: int) -> int:
        return ppq * 4 // self.denominator

    def finer(self) -> "Zoom":
        return Zoom(min(self.SUPPORTED[-1], self.denominator * 2))

    def coarser(self) -> "Zoom":
        return Zoom(max(self.SUPPORTED[0], self.denominator // 2))

    def __str__(self) -> str:
        return f"1/{self.denominator}"


@dataclass(frozen=True)
class Bar:
    number: int             # 1-based, as musicians count
    start_tick: int
    timesig: TimeSigChange
    ticks: int

    @property
    def end_tick(self) -> int:
        return self.start_tick + self.ticks

    def cells(self, zoom: Zoom, ppq: int) -> int:
        return max(1, self.ticks // zoom.ticks_per_cell(ppq))

    def beats(self) -> int:
        return self.timesig.numerator


class BarMap:
    """Walks the time signature map so bar boundaries stay correct across meter
    changes. A 3/4 bar is simply shorter than a 4/4 one; cell width never changes.
    """

    def __init__(self, song: Song) -> None:
        self.song = song
        self._bars: list[Bar] = []
        self._built_to = -1

    def _build_to(self, tick: int) -> None:
        song = self.song
        if not self._bars:
            cursor, number = 0, 1
        else:
            last = self._bars[-1]
            cursor, number = last.end_tick, last.number + 1

        # Meter changes only take effect at a bar line, which is how notation and
        # every sequencer treats them.
        guard = 0
        while cursor <= tick:
            guard += 1
            if guard > 1_000_000:
                raise RuntimeError("bar map runaway — zero-length bar?")
            ts = song.timesig_at(cursor)
            ticks = ts.ticks_per_bar(song.ppq)
            if ticks <= 0:
                raise ValueError(f"degenerate time signature {ts} at tick {cursor}")
            self._bars.append(Bar(number, cursor, ts, ticks))
            cursor += ticks
            number += 1
        self._built_to = max(self._built_to, tick)

    def bar_containing(self, tick: int) -> Bar:
        self._build_to(max(0, tick))
        for bar in self._bars:
            if bar.start_tick <= tick < bar.end_tick:
                return bar
        return self._bars[-1]

    def bar(self, number: int) -> Bar:
        """1-based, as musicians count."""
        if number < 1:
            raise ValueError("bars are numbered from 1")
        while len(self._bars) < number:
            self._build_to(self._bars[-1].end_tick if self._bars else 0)
        return self._bars[number - 1]

    def bars(self, first: int, last: int) -> list[Bar]:
        return [self.bar(n) for n in range(first, last + 1)]

    def position(self, tick: int) -> str:
        """``'9:2.1'`` — bar 9, beat 2, first subdivision. What the status bar shows."""
        bar = self.bar_containing(tick)
        ppq = self.song.ppq
        into = tick - bar.start_tick
        beat_ticks = int(4 / bar.timesig.denominator * ppq)
        beat, rem = divmod(into, beat_ticks)
        sixteenth = rem // max(1, ppq // 4)
        return f"{bar.number}:{beat + 1}.{sixteenth + 1}"


@dataclass(frozen=True)
class CellSpan:
    """Where a note lands on the grid, and whether the grid is telling the truth."""

    first_cell: int
    last_cell: int              # inclusive
    fills_last_cell: bool       # runs to the very end of its final cell
    off_grid_start: bool        # note-on isn't on a cell boundary
    off_grid_end: bool          # note-off isn't on a cell boundary

    @property
    def off_grid(self) -> bool:
        """Only the *start* counts as the grid lying.

        An off-grid end is already represented honestly: the tie character says
        "releases within this cell", and the grid never claimed to show exactly
        where inside it. An off-grid *start* is different — the head is drawn in
        a cell the note doesn't actually begin on, which is a real lie and earns
        the ``~`` flag. Treating both as off-grid flagged every ordinary
        slightly-detached note and made the warning meaningless.
        """
        return self.off_grid_start


class GridView:
    """Converts between ticks and cell columns for one zoom level.

    Cell columns are absolute: column 0 is tick 0. Views take a slice of them.
    """

    def __init__(self, song: Song, zoom: Zoom = Zoom(16)) -> None:
        if song.is_smpte:
            raise ValueError(
                "this file measures time in SMPTE frames, not beats, so it has "
                "no bars to draw a grid on. It still loads and round-trips "
                "intact — `seq check` and `seq stat` work — but there is nothing "
                "musical to divide into cells.")
        self.song = song
        self.zoom = zoom
        self.bars = BarMap(song)

    @property
    def ticks_per_cell(self) -> int:
        return self.zoom.ticks_per_cell(self.song.ppq)

    # -- conversions ------------------------------------------------------

    def tick_to_cell(self, tick: int) -> int:
        return tick // self.ticks_per_cell

    def cell_to_tick(self, cell: int) -> int:
        return cell * self.ticks_per_cell

    def snap(self, tick: int) -> int:
        """Nearest cell boundary. What insert and move use unless Ctrl is held."""
        tpc = self.ticks_per_cell
        return ((tick + tpc // 2) // tpc) * tpc

    def is_on_grid(self, tick: int) -> bool:
        return tick % self.ticks_per_cell == 0

    def bar_cell_range(self, bar: Bar) -> tuple[int, int]:
        """Half-open ``[first, last)`` cell columns covering a bar."""
        return self.tick_to_cell(bar.start_tick), self.tick_to_cell(bar.end_tick)

    # -- notes ------------------------------------------------------------

    def span(self, note) -> CellSpan:
        """Which cells a note occupies, and whether it sits honestly on them.

        A note shorter than one cell still gets one cell — it just releases within
        it, which is exactly what ``x·`` means. Nothing ever vanishes because it
        was too short to draw.

        ``fills_last_cell`` is for the GUI, which can draw a rectangle that stops
        where the note actually stops rather than at the cell boundary. It has no
        say in the tie character: the tie means "still sounding as the next cell
        begins", and by definition the last cell is where the note stops, so the
        last cell's tie is always silent.
        """
        tpc = self.ticks_per_cell
        first = note.start // tpc
        # A note ending exactly on a boundary belongs to the cell before it.
        last = max(first, (note.end - 1) // tpc)
        return CellSpan(
            first_cell=first,
            last_cell=last,
            fills_last_cell=note.end >= (last + 1) * tpc,
            off_grid_start=note.start % tpc != 0,
            off_grid_end=note.end % tpc != 0,
        )

    def notes_in_cell_range(self, track, first_cell: int, last_cell: int) -> list:
        """Notes touching ``[first_cell, last_cell]`` inclusive — including ones that
        started earlier and are still sounding, which is how ties survive scrolling."""
        lo = self.cell_to_tick(first_cell)
        hi = self.cell_to_tick(last_cell + 1)
        return [n for n in track.notes if n.start < hi and n.end > lo]


__all__ = ["Zoom", "Bar", "BarMap", "GridView", "CellSpan"]
