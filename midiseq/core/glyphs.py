"""Cell state to characters.

Used by the TUI and the ``.seq`` text view **only** — the GUI paints rectangles
from the same cell matrix and never comes through here.

The two-character grammar:

    char 1 — the event          char 2 — the tie
    ·  nothing                  ·  silence into next cell
    o  note on, soft            ─  sound continues into next cell
    x  note on, medium
    X  note on, loud
    ─  note continuing
    ,  ghost note (other track, read-only)

Which gives, one beat at a time at 1/16 zoom::

    x──────·   quarter note, fills the beat
    x──·x──·   two eighths
    x·x·x·x·   four staccato sixteenths
    ────────   a longer note passing through
    x───────   a note tying over into the next beat
"""

from __future__ import annotations

from dataclasses import dataclass

from .render import Cell, Event, Matrix, Tie


@dataclass(frozen=True)
class GlyphSet:
    empty: str = "·"
    head_soft: str = "o"
    head_medium: str = "x"
    head_loud: str = "X"
    sustain: str = "─"
    carry: str = "─"
    silent: str = "·"
    ghost_head: str = ","
    ghost_sustain: str = "-"
    ghost_carry: str = "-"
    shade: str = "▒"
    plain: str = " "
    beat_sep: str = "│"
    bar_sep: str = "║"
    off_grid_flag: str = "~"
    lane_block: str = "█"


UNICODE = GlyphSet()

#: For terminals and pipes that can't be trusted with box drawing.
ASCII = GlyphSet(
    empty=".", sustain="-", carry="-", silent=".",
    ghost_sustain="'", ghost_carry="'", shade="#", beat_sep="|", bar_sep="H",
    lane_block="#",
)

_BAND_HEADS = {"soft": "head_soft", "medium": "head_medium", "loud": "head_loud"}

#: Ruler labels within a beat. Index 0 is replaced by the beat number.
_SUBDIVISIONS = {
    1: ["#"],
    2: ["#", "&"],
    3: ["#", "t", "l"],
    4: ["#", "e", "&", "a"],
    6: ["#", ".", "t", ".", "l", "."],
    8: ["#", ".", "e", ".", "&", ".", "a", "."],
    16: ["#"] + ["."] * 15,
}


def cell_glyphs(cell: Cell, g: GlyphSet = UNICODE) -> str:
    """One cell as its two characters."""
    if cell.event is Event.EMPTY:
        return g.empty + g.empty
    if cell.event is Event.GHOST_HEAD:
        return g.ghost_head + (g.ghost_carry if cell.tie is Tie.CARRY else g.silent)
    if cell.event is Event.GHOST_SUSTAIN:
        return g.ghost_sustain + (g.ghost_carry if cell.tie is Tie.CARRY else g.silent)
    head = getattr(g, _BAND_HEADS[cell.band]) if cell.event is Event.HEAD else g.sustain
    return head + (g.carry if cell.tie is Tie.CARRY else g.silent)


def _sep(bar_start: bool, g: GlyphSet) -> str:
    return g.bar_sep if bar_start else g.beat_sep


def _gutter_width(matrix: Matrix) -> int:
    return max(4, max((len(r.label) for r in matrix.rows), default=3) + 1)


def _gutter(label: str, accidental: bool, width: int, g: GlyphSet,
            flag: bool = False, cursor: bool = False) -> str:
    mark = ">" if cursor else (g.shade if accidental else g.plain)
    body = label.ljust(width - 1)
    if flag and len(label) < width - 1:
        body = (label + g.off_grid_flag).ljust(width - 1)
    return mark + body[: width - 1]


def ruler(matrix: Matrix, g: GlyphSet = UNICODE, gutter: int | None = None) -> str:
    """``│1 e & a │2 e & a │…`` — beat numbers over the head of each beat."""
    width = gutter if gutter is not None else _gutter_width(matrix)
    out = [" " * width]
    bar_lookup = {b.number: b for b in matrix.bars}
    for i, col in enumerate(matrix.columns):
        if col.beat_start or col.bar_start:
            out.append(_sep(col.bar_start, g) if (col.bar_start or col.beat_start) else "")
        ts = bar_lookup[col.bar].timesig
        cpb = matrix.zoom.cells_per_beat(ts.denominator)
        labels = _SUBDIVISIONS.get(cpb, ["#"] + ["."] * (cpb - 1))
        token = labels[col.subdivision] if col.subdivision < len(labels) else "."
        if token == "#":
            token = str(col.beat)[-1]
        out.append(token.ljust(2))
    out.append(g.bar_sep)
    return "".join(out)


def bar_header(matrix: Matrix, g: GlyphSet = UNICODE, gutter: int | None = None,
               cursor_column: int | None = None) -> str:
    """``┌──── bar 9 ────┬──── bar 10 ────┐`` sized to each bar's actual width."""
    width = gutter if gutter is not None else _gutter_width(matrix)
    out = [" " * width]
    counts: dict[int, int] = {}
    for col in matrix.columns:
        counts[col.bar] = counts.get(col.bar, 0) + 1
    for i, bar in enumerate(matrix.bars):
        n = counts.get(bar.number, 0)
        if not n:
            continue
        span = n * 2 + (bar.beats() - 1 if n > 1 else 0)
        # Width must match the grid: 2 chars per cell plus one separator per beat
        # after the first, plus the leading corner.
        seps = sum(
            1 for c in matrix.columns
            if c.bar == bar.number and (c.beat_start or c.bar_start) and not c.bar_start
        )
        span = n * 2 + seps
        # Narrow bars — 2/4 or 3/8 at 1/4 zoom — can't fit " bar 9 ". Shorten the
        # words, never the number: a truncated "bar 123" out of bar 1234 still
        # parses, and points a text edit at the wrong bar.
        full = f" bar {bar.number} "
        if i == 0 or bar.timesig != matrix.bars[i - 1].timesig:    # the first bar, and where it changes: the window's rule
            full = f" bar {bar.number} ({bar.timesig}) "
        title = full
        for shorter in (f" bar {bar.number} ", f" {bar.number} ", str(bar.number)):
            if len(title) <= span:
                break
            title = shorter
        pad = max(0, span - len(title))
        left, right = pad // 2, pad - pad // 2
        corner = "┌" if i == 0 else "┬"
        out.append(corner + "─" * left + title + "─" * right)
    out.append("┐")
    line = "".join(out)
    if cursor_column is not None:
        pos = _column_x(matrix, cursor_column, width)
        if 0 <= pos < len(line):
            line = line[:pos] + "▼" + line[pos + 1:]
    return line


def _column_x(matrix: Matrix, index: int, gutter: int) -> int:
    """Character offset of a column's head, for cursor and playhead markers."""
    x = gutter
    for i, col in enumerate(matrix.columns):
        if col.beat_start or col.bar_start:
            x += 1
        if i == index:
            return x
        x += 2
    return x


def grid_rows(matrix: Matrix, g: GlyphSet = UNICODE, gutter: int | None = None,
              cursor_row: int | None = None) -> list[str]:
    width = gutter if gutter is not None else _gutter_width(matrix)
    lines = []
    for ri, row in enumerate(matrix.rows):
        parts = [_gutter(row.label, row.accidental, width, g,
                         flag=row.off_grid_flag, cursor=(ri == cursor_row))]
        for col, cell in zip(matrix.columns, row.cells):
            if col.beat_start or col.bar_start:
                parts.append(_sep(col.bar_start, g))
            parts.append(cell_glyphs(cell, g))
        parts.append(g.bar_sep)
        lines.append("".join(parts))
    return lines


#: Rows of the velocity lane, loudest first. Labelling the axis with dynamics
#: rather than 0-127 is the point: "make this softer" becomes something you aim
#: at rather than a number you guess.
LANE_ROWS = (("f", 100), ("mf", 80), ("p", 48), ("pp", 1))


def velocity_lane(matrix: Matrix, g: GlyphSet = UNICODE,
                  gutter: int | None = None) -> list[str]:
    width = gutter if gutter is not None else _gutter_width(matrix)
    lines = []
    for label, floor in LANE_ROWS:
        parts = [(" " + label).ljust(width)[:width]]
        for col, vel in zip(matrix.columns, matrix.velocity_lane):
            if col.beat_start or col.bar_start:
                parts.append(_sep(col.bar_start, g))
            parts.append((g.lane_block if vel >= floor else " ") + " ")
        parts.append(g.bar_sep)
        lines.append("".join(parts))
    return lines


def rule(matrix: Matrix, g: GlyphSet = UNICODE, gutter: int | None = None,
         top: bool = True) -> str:
    width = gutter if gutter is not None else _gutter_width(matrix)
    beat_j, bar_j = ("┼", "╫") if top else ("┴", "╨")
    parts = ["─" * width]
    for col in matrix.columns:
        if col.beat_start or col.bar_start:
            parts.append(bar_j if col.bar_start else beat_j)
        parts.append("──")
    parts.append("╢" if top else "╨")
    return "".join(parts)


def render_text(
    matrix: Matrix,
    g: GlyphSet = UNICODE,
    *,
    show_lane: bool = True,
    show_header: bool = True,
    cursor_row: int | None = None,
    cursor_column: int | None = None,
) -> str:
    """The whole grid as text. This is what ``seq cat`` prints and what the TUI draws."""
    width = _gutter_width(matrix)
    out: list[str] = []
    if show_header:
        out.append(bar_header(matrix, g, width, cursor_column))
    out.append(ruler(matrix, g, width))
    out.extend(grid_rows(matrix, g, width, cursor_row))
    if show_lane:
        out.append(rule(matrix, g, width, top=True))
        out.extend(velocity_lane(matrix, g, width))
        out.append(rule(matrix, g, width, top=False))
    return "\n".join(out)


__all__ = [
    "GlyphSet", "UNICODE", "ASCII", "cell_glyphs", "render_text",
    "ruler", "bar_header", "grid_rows", "velocity_lane", "rule", "LANE_ROWS",
]
