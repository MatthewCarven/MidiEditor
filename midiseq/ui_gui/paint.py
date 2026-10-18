"""The paint plan: the cell matrix turned into rectangles, lines and labels,
with no toolkit in sight.

``core/render.py`` says what every cell *is*. This module says where every
rectangle *goes*. The DearPyGui glue (``painter.py``) then turns each
primitive into one draw call and knows nothing about music; this module knows
nothing about DearPyGui. That split is what lets the whole picture be tested
without opening a window - the same reason the glyphs are a separate module
from the matrix on the text side.

Two rules the spike settled (WORKLOG, 2026-10-18):

* **Zoom scales the drawing, never the widgets.** ``Layout.cell_w`` is the
  pixel zoom; nothing here touches a font scale or a widget size.
* **A note is one rectangle**, from its first cell to where it actually stops
  (``Cell.end_fill``), with a head mark at the start. Not one rectangle per
  cell.

Colours are *roles* (``"note_loud"``, ``"ghost"`` ...), looked up in a theme
by the painter, so a test can ask "is the loud note painted loud" without
knowing what loud looks like today.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.render import DYNAMICS, Event, Matrix

# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float
    role: str
    filled: bool = True


@dataclass(frozen=True)
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    role: str


@dataclass(frozen=True)
class Label:
    x: float
    y: float
    text: str
    role: str
    size: int = 12


Primitive = Rect | Line | Label


@dataclass(frozen=True)
class Layout:
    """Pixel geometry. ``cell_w`` is the zoom; ``row_h`` is fixed for now."""

    gutter_w: int = 60      # row labels and the ~ flag
    ruler_h: int = 22       # bar numbers
    lane_h: int = 56        # velocity lane
    row_h: int = 14
    cell_w: int = 14
    head_w: int = 3         # the mark at a note's first cell

    MIN_CELL_W = 4
    MAX_CELL_W = 48

    def with_cell_w(self, cell_w: int) -> "Layout":
        cell_w = max(self.MIN_CELL_W, min(self.MAX_CELL_W, int(cell_w)))
        return Layout(self.gutter_w, self.ruler_h, self.lane_h, self.row_h, cell_w, self.head_w)

    # -- geometry shared by the plan, hit-testing and the hover outline -----

    def visible_cols(self, width: int) -> int:
        return max(1, (width - self.gutter_w) // self.cell_w)

    def visible_rows(self, height: int) -> int:
        return max(1, (height - self.ruler_h - self.lane_h) // self.row_h)

    def x_of(self, cell: int, first_cell: int) -> int:
        return self.gutter_w + (cell - first_cell) * self.cell_w

    def y_of(self, row: int) -> int:
        return self.ruler_h + row * self.row_h

    def cell_rect(self, row: int, cell: int, first_cell: int) -> tuple[int, int, int, int]:
        x, y = self.x_of(cell, first_cell), self.y_of(row)
        return x, y, x + self.cell_w - 1, y + self.row_h - 1

    def x_of_tick(self, tick: int, first_cell: int, ticks_per_cell: int) -> float:
        """Sub-cell x: where a tick falls, not just its cell. The playhead
        and unsnapped previews move smoothly through a cell; ``x_of`` is
        the cell's left edge and would make them jump."""
        return self.gutter_w + (tick - first_cell * ticks_per_cell) / ticks_per_cell * self.cell_w

    def ruler_cell(self, x: float, y: float, first_cell: int, width: int) -> int | None:
        """The absolute cell under a point in the ruler, or None when the
        point is in the gutter, past the last column, or not in the ruler."""
        if y < 0 or y >= self.ruler_h:
            return None
        if x < self.gutter_w or x >= self.gutter_w + self.visible_cols(width) * self.cell_w:
            return None
        return first_cell + int((x - self.gutter_w) // self.cell_w)

    def raw_cell(self, x: float, y: float, first_cell: int, rows: int) -> tuple[int, int]:
        """``(row, absolute cell)`` for a point anywhere, clamped to the grid's
        rows and to cell 0 - for drags that wander off the edge."""
        row = max(0, min(rows - 1, int((y - self.ruler_h) // self.row_h)))
        col = max(0, first_cell + int((x - self.gutter_w) // self.cell_w))
        return row, col

    def hit_test(self, x: float, y: float, first_cell: int, rows: int,
                 width: int) -> tuple[int, int] | None:
        """``(row, absolute cell)`` under a point, or None outside the grid."""
        if x < self.gutter_w or x >= self.gutter_w + self.visible_cols(width) * self.cell_w:
            return None
        if y < self.ruler_h or y >= self.ruler_h + rows * self.row_h:
            return None
        return (int((y - self.ruler_h) // self.row_h),
                first_cell + int((x - self.gutter_w) // self.cell_w))


@dataclass
class PaintPlan:
    width: int
    height: int
    first_cell: int
    cols: int                   # visible columns
    rows: int                   # visible rows
    items: list[Primitive] = field(default_factory=list)

    def by_role(self, role: str) -> list[Primitive]:
        return [p for p in self.items if p.role == role]

    def rects(self, role: str) -> list[Rect]:
        return [p for p in self.items if isinstance(p, Rect) and p.role == role]

    def labels(self, role: str) -> list[Label]:
        return [p for p in self.items if isinstance(p, Label) and p.role == role]


#: The dynamics the lane labels. PLAN.md §2 draws f / mf / p / pp; the other
#: two bands exist in ``DYNAMICS`` but their lines would sit on top of these.
LANE_MARKS = ("pp", "p", "mf", "f")

#: Default colours by role, RGBA. The painter looks these up; nothing in the
#: plan carries a colour.
DEFAULT_THEME: dict[str, tuple[int, int, int, int]] = {
    "bg": (24, 24, 28, 255),
    "accidental_row": (36, 36, 44, 255),
    "bar_line": (96, 96, 110, 255),
    "beat_line": (52, 52, 62, 255),
    "ruler_text": (190, 190, 200, 255),
    "row_label": (190, 190, 200, 255),
    "row_label_accidental": (120, 120, 135, 255),
    "off_grid": (255, 200, 80, 255),
    "note_soft": (140, 180, 90, 255),
    "note_medium": (230, 170, 60, 255),
    "note_loud": (255, 110, 60, 255),
    "ghost": (72, 72, 86, 255),
    "head": (0, 0, 0, 140),
    "lane_axis": (60, 60, 72, 255),
    "lane_label": (120, 120, 135, 255),
    "lane_bar": (110, 150, 220, 255),
    "hover": (255, 255, 255, 255),
    "selected": (255, 250, 170, 255),     # outline round a selected note
    "preview": (255, 255, 255, 110),      # where a dragged or drawn note will land
    "marquee": (120, 180, 255, 255),      # the selection rectangle
    "erase": (255, 90, 90, 255),          # notes a right-drag sweep will delete
    "playhead": (255, 255, 255, 230),     # the transport's position, ruler to lane
}


def span_rect(layout: Layout, first_cell: int, row: int, first_col: int, last_col: int,
              end_fill: float = 1.0) -> tuple[float, float, float, float]:
    """The rectangle for a note occupying ``[first_col, last_col]`` on ``row``,
    stopping ``end_fill`` of the way through its last cell."""
    x0 = layout.x_of(first_col, first_cell)
    y = layout.y_of(row)
    x1 = layout.x_of(last_col, first_cell) + max(1.0, layout.cell_w * end_fill) - 1
    return x0, y + 1, x1, y + layout.row_h - 1


def tick_rect(layout: Layout, first_cell: int, ticks_per_cell: int, row: int,
              start: int, end: int) -> tuple[float, float, float, float]:
    """The rectangle for ``[start, end)`` ticks on ``row`` - sub-cell accurate,
    for previews of unsnapped drags."""
    x0 = layout.x_of_tick(start, first_cell, ticks_per_cell)
    x1 = layout.x_of_tick(end, first_cell, ticks_per_cell) - 1
    y = layout.y_of(row)
    return x0, y + 1, max(x0 + 1, x1), y + layout.row_h - 1


def playhead_plan(layout: Layout, first_cell: int, ticks_per_cell: int, tick: int,
                  cols: int, rows: int) -> list[Primitive]:
    """One vertical line from the top of the ruler to the bottom of the
    lane, or nothing when the tick is off screen. Its own tiny plan, like
    the hover outline, because it moves every frame while the grid under
    it does not."""
    x = layout.x_of_tick(tick, first_cell, ticks_per_cell)
    if x < layout.gutter_w or x >= layout.gutter_w + cols * layout.cell_w:
        return []
    bottom = layout.ruler_h + rows * layout.row_h + layout.lane_h
    return [Line(x, 0, x, bottom, "playhead")]


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

def plan(matrix: Matrix, layout: Layout, *, first_cell: int,
         width: int, height: int) -> PaintPlan:
    """Lay out one screen.

    ``matrix`` may cover more columns than fit (``render`` works in whole
    bars); only ``[first_cell, first_cell + visible)`` is painted. Rows are
    taken from the top of the matrix down to what fits.
    """
    cols = layout.visible_cols(width)
    rows = min(matrix.height, layout.visible_rows(height))
    last_cell = first_cell + cols - 1
    x_of = lambda c: layout.x_of(c, first_cell)   # noqa: E731
    y_of = layout.y_of
    cw, rh = layout.cell_w, layout.row_h
    grid_right = layout.gutter_w + cols * cw
    grid_bottom = layout.ruler_h + rows * rh
    lane_top, lane_bottom = grid_bottom, grid_bottom + layout.lane_h

    out = PaintPlan(width, height, first_cell, cols, rows)
    items = out.items

    items.append(Rect(0, 0, width, height, "bg"))

    # Accidental rows: one wide rectangle each, under everything.
    for ri in range(rows):
        if matrix.rows[ri].accidental:
            y = y_of(ri)
            items.append(Rect(layout.gutter_w, y, grid_right, y + rh, "accidental_row"))

    # Columns: bar and beat lines; bar numbers, with the meter where it changes.
    visible = [(ci, c) for ci, c in enumerate(matrix.columns)
               if first_cell <= c.cell <= last_cell]
    bar_by_number = {b.number: b for b in matrix.bars}
    previous_meter: tuple[int, int] | None = None
    first_visible_bar = True
    for ci, c in visible:
        x = x_of(c.cell)
        if c.bar_start:
            items.append(Line(x, layout.ruler_h - 4, x, lane_bottom, "bar_line"))
            bar = bar_by_number.get(c.bar)
            meter = (bar.timesig.numerator, bar.timesig.denominator) if bar else None
            text = str(c.bar)
            if meter is not None and (
                (first_visible_bar and meter != (4, 4))
                or (previous_meter is not None and meter != previous_meter)
            ):
                text = f"{c.bar} ({meter[0]}/{meter[1]})"
            previous_meter = meter
            first_visible_bar = False
            items.append(Label(x + 3, 3, text, "ruler_text", 13))
        elif c.beat_start:
            items.append(Line(x, layout.ruler_h, x, grid_bottom, "beat_line"))

    # Notes: one rectangle per note, from its first visible cell to where it
    # stops, with a head mark where it begins. Ghosts have no head mark.
    for ri in range(rows):
        row = matrix.rows[ri]
        y = y_of(ri)
        run_nid = None
        run_start = run_last = 0
        run_ghost = False
        run_role = "note_medium"
        run_fill = 1.0
        run_has_head = False
        run_selected = False

        def flush() -> None:
            if run_nid is None:
                return
            x0, y0, x1, y1 = span_rect(layout, first_cell, ri, run_start, run_last, run_fill)
            items.append(Rect(x0, y0, x1, y1, run_role))
            if run_has_head:
                items.append(Rect(x0, y0, x0 + layout.head_w, y1, "head"))
            if run_selected:
                items.append(Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1, "selected", filled=False))

        for ci, c in visible:
            cell = row.cells[ci]
            if cell.event is Event.EMPTY:
                flush()
                run_nid = None
                continue
            ghost = cell.is_ghost
            starts = cell.event in (Event.HEAD, Event.GHOST_HEAD)
            if starts or cell.nid != run_nid or ghost != run_ghost:
                flush()
                run_nid = cell.nid
                run_start = c.cell
                run_ghost = ghost
                run_role = "ghost" if ghost else f"note_{cell.band}"
                run_has_head = starts and not ghost
                run_selected = cell.selected and not ghost
            run_last = c.cell
            run_fill = cell.end_fill
        flush()

    # Row labels (every other one when rows get short) and the ~ flag.
    for ri in range(rows):
        row = matrix.rows[ri]
        y = y_of(ri)
        if rh >= 11 or ri % 2 == 0:
            role = "row_label_accidental" if row.accidental else "row_label"
            items.append(Label(6, y, row.label, role, 12))
        if row.off_grid_flag:
            items.append(Label(layout.gutter_w - 12, y, "~", "off_grid", 13))

    # Velocity lane: the dynamics axis (the four marks PLAN.md draws - six
    # would overlap at this height), then one bar per column with a head.
    items.append(Line(layout.gutter_w, lane_top, grid_right, lane_top, "bar_line"))
    for ceiling, name in DYNAMICS:
        if name not in LANE_MARKS:
            continue
        yy = lane_bottom - int(layout.lane_h * min(ceiling, 127) / 127)
        items.append(Line(layout.gutter_w, yy, grid_right, yy, "lane_axis"))
        items.append(Label(6, yy - 6, name, "lane_label", 11))
    for ci, c in visible:
        v = matrix.velocity_lane[ci]
        if v:
            x = x_of(c.cell)
            h = int(layout.lane_h * v / 127)
            items.append(Rect(x + 1, lane_bottom - h, x + max(2, cw - 2), lane_bottom, "lane_bar"))

    return out


def hover_plan(layout: Layout, first_cell: int, row: int, cell: int) -> list[Primitive]:
    """The outline for the cell under the mouse. Its own tiny plan, so moving
    the mouse never repaints the grid."""
    x0, y0, x1, y1 = layout.cell_rect(row, cell, first_cell)
    return [Rect(x0, y0, x1, y1, "hover", filled=False)]


__all__ = ["Rect", "Line", "Label", "Layout", "PaintPlan", "DEFAULT_THEME", "LANE_MARKS",
           "plan", "hover_plan", "playhead_plan", "span_rect", "tick_rect"]
