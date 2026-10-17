"""DearPyGui grid spike - the gate on the 2026-10-18 toolkit decision.

Renders one track of a ``.mid`` as the step-sequencer grid in a single
DearPyGui drawlist, virtualised twice over: ``core/render.py`` only builds the
bars and pitch rows that are on screen, and only those cells are drawn.

    python tools/spike_dpg_grid.py examples/bluesy.mid
    python tools/spike_dpg_grid.py long.mid -t 3 --bench 8     # autopilot, stats, quit

Interactive:
    wheel               scroll pitches          Shift+wheel     scroll time
    Ctrl+wheel          cell width (pixels)     Ctrl+Shift+wheel  1/4 .. 1/64 zoom
    arrows              one cell / one pitch    Ctrl+Left/Right one bar
    PageUp/PageDown     a screen of pitches     Home            bar 1
    hover               names the cell in the status line
    Esc                 quit

Two clocks run on every repaint, kept apart on purpose: how long
``render.render`` takes to build the visible matrix (midiseq's cost) and how
long the DearPyGui draw calls take (the toolkit's cost). ``--bench N`` scrolls
and zooms by itself for N seconds, prints both distributions and the frame
rate, and quits - that is the number the go/no-go reads.

Two rules the step-3 widget inherits from here:

* zoom scales the *drawing* (cell width), never a widget or the global font -
  PySynthRack's zoom/size feedback bug is what happens otherwise;
* notes are painted as *spans* (one rectangle per note, a head mark at its
  first cell), not one rectangle per cell. ``--per-cell`` draws the naive way so
  the difference can be measured.

This is a spike: no paint-plan layer, no tests, no editing. It will be thrown
away once step 3's widget exists, and nothing in it is imported by anything.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import dearpygui.dearpygui as dpg  # noqa: E402

from midiseq.core import io_midi, render  # noqa: E402
from midiseq.core.grid import GridView, Zoom  # noqa: E402
from midiseq.core.model import DEFAULT_MIDDLE_C_OCTAVE, pitch_name  # noqa: E402
from midiseq.core.render import DYNAMICS, Event  # noqa: E402

# --- layout constants (pixels) --------------------------------------------

GUTTER_W = 60        # row labels and the ~ flag
RULER_H = 22         # bar numbers
LANE_H = 56          # velocity lane
STATUS_H = 24        # one line of text under the drawlist
MIN_CELL_W, MAX_CELL_W = 4, 48
ROW_H = 14

# --- colours ----------------------------------------------------------------

BG = (24, 24, 28, 255)
ACCIDENTAL_ROW = (36, 36, 44, 255)
BAR_LINE = (96, 96, 110, 255)
BEAT_LINE = (52, 52, 62, 255)
TEXT = (190, 190, 200, 255)
DIM_TEXT = (120, 120, 135, 255)
NOTE = {"soft": (140, 180, 90, 255), "medium": (230, 170, 60, 255), "loud": (255, 110, 60, 255)}
GHOST = (72, 72, 86, 255)
HEAD_MARK = (0, 0, 0, 140)
OFF_GRID = (255, 200, 80, 255)
HOVER = (255, 255, 255, 255)
LANE_BAR = (110, 150, 220, 255)
LANE_AXIS = (60, 60, 72, 255)


def _key(*names: str) -> int | None:
    """First DearPyGui key constant that exists under any of these names.
    DPG renamed several between 1.x and 2.x; resolving by name keeps the
    spike running on whichever is installed."""
    for n in names:
        code = getattr(dpg, n, None)
        if code is not None:
            return code
    return None


def _any_down(*names: str) -> bool:
    for n in names:
        code = getattr(dpg, n, None)
        if code is None:
            continue
        try:
            if dpg.is_key_down(code):
                return True
        except Exception:  # noqa: BLE001 - key not in this DPG build
            pass
    return False


def ctrl_down() -> bool:
    return _any_down("mvKey_LControl", "mvKey_RControl", "mvKey_Control", "mvKey_ModCtrl")


def shift_down() -> bool:
    return _any_down("mvKey_LShift", "mvKey_RShift", "mvKey_Shift", "mvKey_ModShift")


class Spike:
    def __init__(self, song, track: int, zoom: Zoom, *, per_cell: bool, width: int, height: int):
        self.song = song
        self.track = track
        self.zoom = zoom
        self.view = GridView(song, zoom)
        self.per_cell = per_cell
        self.vw, self.vh = width, height

        self.col0 = 0                      # first visible absolute cell
        self.cell_w = 14
        rng = song.tracks[track].pitch_range() or (60, 72)
        self.top_pitch = min(127, rng[1] + 2)

        self.hover: tuple[int, int] | None = None   # (row index, absolute cell)
        self.dirty = True
        self.hover_dirty = False
        self.last_matrix = None
        self.samples: list[tuple[float, float, int, int, int]] = []   # render ms, draw ms, items, rows, cols
        self.fps_samples: list[float] = []

    # -- geometry -----------------------------------------------------------

    @property
    def grid_w(self) -> int:
        return max(self.cell_w, self.vw - GUTTER_W)

    @property
    def grid_h(self) -> int:
        return max(ROW_H, self.vh - STATUS_H - RULER_H - LANE_H)

    @property
    def visible_cols(self) -> int:
        return max(1, self.grid_w // self.cell_w)

    @property
    def visible_rows(self) -> int:
        return max(1, self.grid_h // ROW_H)

    def x_of(self, cell: int) -> int:
        return GUTTER_W + (cell - self.col0) * self.cell_w

    def y_of(self, row: int) -> int:
        return RULER_H + row * ROW_H

    def cell_at(self, x: float, y: float) -> tuple[int, int] | None:
        if x < GUTTER_W or y < RULER_H or y >= RULER_H + self.visible_rows * ROW_H:
            return None
        col = self.col0 + int((x - GUTTER_W) // self.cell_w)
        row = int((y - RULER_H) // ROW_H)
        if col < 0 or row >= self.visible_rows:
            return None
        return row, col

    # -- navigation ---------------------------------------------------------

    def scroll_cells(self, n: int) -> None:
        self.col0 = max(0, self.col0 + n)
        self.dirty = True

    def scroll_bars(self, n: int) -> None:
        bar = self.view.bars.bar_containing(self.view.cell_to_tick(self.col0))
        target = max(1, bar.number + n)
        self.col0 = self.view.bar_cell_range(self.view.bars.bar(target))[0]
        self.dirty = True

    def scroll_pitches(self, n: int) -> None:
        self.top_pitch = max(self.visible_rows - 1, min(127, self.top_pitch + n))
        self.dirty = True

    def set_cell_w(self, w: int) -> None:
        w = max(MIN_CELL_W, min(MAX_CELL_W, w))
        if w != self.cell_w:
            self.cell_w = w
            self.dirty = True

    def set_zoom(self, zoom: Zoom) -> None:
        """Change the musical zoom keeping the left edge on the same tick."""
        if zoom == self.zoom:
            return
        left_tick = self.view.cell_to_tick(self.col0)
        self.zoom = zoom
        self.view = GridView(self.song, zoom)
        self.col0 = self.view.tick_to_cell(left_tick)
        self.dirty = True

    # -- paint --------------------------------------------------------------

    def paint(self) -> None:
        t0 = time.perf_counter()
        first_cell = self.col0
        last_cell = self.col0 + self.visible_cols - 1
        view = self.view
        first_bar = view.bars.bar_containing(view.cell_to_tick(first_cell)).number
        last_bar = view.bars.bar_containing(view.cell_to_tick(last_cell)).number
        high = self.top_pitch
        low = max(0, high - self.visible_rows + 1)
        m = render.render(
            self.song, self.track, zoom=self.zoom,
            first_bar=first_bar, last_bar=last_bar,
            low_pitch=low, high_pitch=high, ghosts=True,
        )
        self.last_matrix = m
        t1 = time.perf_counter()

        items = self._draw(m, first_cell, last_cell)
        t2 = time.perf_counter()

        self.samples.append(((t1 - t0) * 1e3, (t2 - t1) * 1e3, items, m.height, m.width))
        self.dirty = False
        self.hover_dirty = True

    def _draw(self, m, first_cell: int, last_cell: int) -> int:
        layer = "layer_grid"
        dpg.delete_item(layer, children_only=True)
        items = 0
        cw, rh = self.cell_w, ROW_H
        grid_right = GUTTER_W + self.visible_cols * cw
        grid_bottom = RULER_H + self.visible_rows * rh
        lane_top = grid_bottom
        lane_bottom = lane_top + LANE_H

        dpg.draw_rectangle((0, 0), (self.vw, self.vh - STATUS_H), fill=BG, color=BG, parent=layer)
        items += 1

        # accidental rows: one wide rectangle each, under everything
        for ri, row in enumerate(m.rows):
            if row.accidental:
                y = self.y_of(ri)
                dpg.draw_rectangle((GUTTER_W, y), (grid_right, y + rh),
                                   fill=ACCIDENTAL_ROW, color=ACCIDENTAL_ROW, parent=layer)
                items += 1

        # columns: bar and beat lines, bar numbers in the ruler
        visible_columns = [(ci, c) for ci, c in enumerate(m.columns)
                           if first_cell <= c.cell <= last_cell]
        for ci, c in visible_columns:
            x = self.x_of(c.cell)
            if c.bar_start:
                dpg.draw_line((x, RULER_H - 4), (x, lane_bottom), color=BAR_LINE, parent=layer)
                items += 1
                ts = m.bars[c.bar - m.bars[0].number].timesig if m.bars else None
                label = f"{c.bar}" if ts is None or (ts.numerator, ts.denominator) == (4, 4) \
                    else f"{c.bar} ({ts.numerator}/{ts.denominator})"
                dpg.draw_text((x + 3, 3), label, color=TEXT, size=13, parent=layer)
                items += 1
            elif c.beat_start:
                dpg.draw_line((x, RULER_H), (x, grid_bottom), color=BEAT_LINE, parent=layer)
                items += 1

        # notes: spans (one rectangle per note) or cells (the naive way)
        for ri, row in enumerate(m.rows):
            y = self.y_of(ri)
            run_nid = None
            run_start = None
            run_cell = None
            run_ghost = False
            run_band = "medium"

            def flush() -> int:
                if run_nid is None:
                    return 0
                x0 = self.x_of(run_start)
                x1 = self.x_of(run_cell) + cw - 1
                colour = GHOST if run_ghost else NOTE[run_band]
                dpg.draw_rectangle((x0, y + 1), (x1, y + rh - 1), fill=colour, color=colour, parent=layer)
                n = 1
                if not run_ghost:
                    dpg.draw_rectangle((x0, y + 1), (x0 + 3, y + rh - 1), fill=HEAD_MARK, color=HEAD_MARK, parent=layer)
                    n += 1
                return n

            for ci, c in visible_columns:
                cell = row.cells[ci]
                if cell.event is Event.EMPTY:
                    items += flush()
                    run_nid = None
                    continue
                ghost = cell.is_ghost
                starts = cell.event in (Event.HEAD, Event.GHOST_HEAD)
                if self.per_cell or run_nid != cell.nid or starts or ghost != run_ghost:
                    items += flush()
                    run_nid, run_start, run_ghost, run_band = cell.nid, c.cell, ghost, cell.band
                run_cell = c.cell
            items += flush()

        # row labels and the ~ flag; every other label when rows get short
        for ri, row in enumerate(m.rows):
            y = self.y_of(ri)
            if rh >= 11 or ri % 2 == 0:
                dpg.draw_text((6, y), row.label, color=DIM_TEXT if row.accidental else TEXT, size=12, parent=layer)
                items += 1
            if row.off_grid_flag:
                dpg.draw_text((GUTTER_W - 12, y), "~", color=OFF_GRID, size=13, parent=layer)
                items += 1

        # velocity lane
        dpg.draw_line((GUTTER_W, lane_top), (grid_right, lane_top), color=BAR_LINE, parent=layer)
        items += 1
        for ceiling, name in DYNAMICS:
            yy = lane_bottom - int(LANE_H * min(ceiling, 127) / 127)
            dpg.draw_line((GUTTER_W, yy), (grid_right, yy), color=LANE_AXIS, parent=layer)
            dpg.draw_text((6, yy - 6), name, color=DIM_TEXT, size=11, parent=layer)
            items += 2
        for ci, c in visible_columns:
            v = m.velocity_lane[ci]
            if v:
                x = self.x_of(c.cell)
                h = int(LANE_H * v / 127)
                dpg.draw_rectangle((x + 1, lane_bottom - h), (x + max(2, cw - 2), lane_bottom),
                                   fill=LANE_BAR, color=LANE_BAR, parent=layer)
                items += 1
        return items

    def paint_hover(self) -> None:
        dpg.delete_item("layer_hover", children_only=True)
        self.hover_dirty = False
        if self.hover is None or self.last_matrix is None:
            self._status("")
            return
        row, col = self.hover
        m = self.last_matrix
        if row >= m.height:
            self._status("")
            return
        x, y = self.x_of(col), self.y_of(row)
        dpg.draw_rectangle((x, y), (x + self.cell_w - 1, y + ROW_H - 1), color=HOVER, thickness=1, parent="layer_hover")
        r = m.rows[row]
        tick = self.view.cell_to_tick(col)
        pos = self.view.bars.position(tick)
        ci = next((i for i, c in enumerate(m.columns) if c.cell == col), None)
        cell = r.cells[ci] if ci is not None else None
        what = ""
        if cell is not None and cell.nid is not None:
            what = f" . {'ghost' if cell.is_ghost else cell.band} vel {cell.velocity}"
            if cell.off_grid:
                what += " ~"
        self._status(f"{r.label} . MIDI {r.pitch} . {pos}{what}")

    def _status(self, where: str) -> None:
        s = self.samples[-1] if self.samples else (0.0, 0.0, 0, 0, 0)
        fps = dpg.get_frame_rate()
        tr = self.song.tracks[self.track]
        text = (f"{where}  |  trk {self.track} \"{tr.name}\"  zoom {self.zoom}  cell {self.cell_w}px  "
                f"|  render {s[0]:.1f} ms  draw {s[1]:.1f} ms  {s[2]} items  "
                f"{s[3]}x{s[4]} matrix  {fps:.0f} fps")
        dpg.set_value("status", text)

    # -- stats --------------------------------------------------------------

    def report(self) -> str:
        if not self.samples:
            return "no paints recorded"

        def pct(values, p):
            values = sorted(values)
            k = max(0, min(len(values) - 1, int(round((p / 100) * (len(values) - 1)))))
            return values[k]

        r = [s[0] for s in self.samples]
        d = [s[1] for s in self.samples]
        n = [s[2] for s in self.samples]
        lines = [
            f"paints: {len(self.samples)}   matrix (typical): {statistics.median(s[3] for s in self.samples):.0f} rows x "
            f"{statistics.median(s[4] for s in self.samples):.0f} cols   mode: {'per-cell' if self.per_cell else 'spans'}",
            f"render.py ms   p50 {pct(r, 50):6.1f}   p95 {pct(r, 95):6.1f}   max {max(r):6.1f}",
            f"dpg draw  ms   p50 {pct(d, 50):6.1f}   p95 {pct(d, 95):6.1f}   max {max(d):6.1f}",
            f"items/paint    p50 {pct(n, 50):6.0f}   p95 {pct(n, 95):6.0f}   max {max(n):6.0f}",
        ]
        if self.fps_samples:
            lines.append(f"frame rate     p50 {pct(self.fps_samples, 50):6.0f}   min {min(self.fps_samples):6.0f}")
        return "\n".join(lines)


def pick_track(song) -> int:
    return max(range(len(song.tracks)), key=lambda i: len(song.tracks[i].notes))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("-t", "--track", type=int, default=None)
    ap.add_argument("-z", "--zoom", type=int, default=16, choices=Zoom.SUPPORTED)
    ap.add_argument("--bench", type=float, default=0.0, metavar="SECONDS",
                    help="autopilot: scroll and zoom by itself, print stats, quit")
    ap.add_argument("--per-cell", action="store_true", help="one rectangle per cell (the naive way)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=760)
    ap.add_argument("--bar", type=int, default=1, help="start with this bar at the left edge")
    ap.add_argument("--screenshot", metavar="PNG", default=None,
                    help="paint once, write the frame buffer to PNG, quit")
    args = ap.parse_args(argv)

    song = io_midi.load(args.file)
    track = args.track if args.track is not None else pick_track(song)
    if not 0 <= track < len(song.tracks):
        print(f"no track {track}", file=sys.stderr)
        return 2
    spike = Spike(song, track, Zoom(args.zoom), per_cell=args.per_cell, width=args.width, height=args.height)
    if args.bar > 1:
        spike.col0 = spike.view.bar_cell_range(spike.view.bars.bar(args.bar))[0]

    dpg.create_context()
    dpg.create_viewport(title=f"midiseq spike - {Path(args.file).name}", width=args.width, height=args.height)

    with dpg.window(tag="main", no_title_bar=True, no_move=True, no_resize=True, no_scrollbar=True):
        with dpg.drawlist(width=args.width, height=args.height - STATUS_H, tag="dl"):
            dpg.add_draw_layer(tag="layer_grid")
            dpg.add_draw_layer(tag="layer_hover")
        dpg.add_text("", tag="status")
    dpg.set_primary_window("main", True)

    k_left, k_right = _key("mvKey_Left"), _key("mvKey_Right")
    k_up, k_down = _key("mvKey_Up"), _key("mvKey_Down")
    k_pgup, k_pgdn = _key("mvKey_Prior", "mvKey_PageUp"), _key("mvKey_Next", "mvKey_PageDown")
    k_home, k_esc = _key("mvKey_Home"), _key("mvKey_Escape")

    def on_wheel(sender, app_data):
        direction = 1 if app_data > 0 else -1
        if ctrl_down() and shift_down():
            spike.set_zoom(spike.zoom.finer() if direction > 0 else spike.zoom.coarser())
        elif ctrl_down():
            spike.set_cell_w(int(round(spike.cell_w * (1.25 if direction > 0 else 0.8))))
        elif shift_down():
            spike.scroll_cells(-4 * direction)
        else:
            spike.scroll_pitches(3 * direction)

    def on_key(sender, app_data):
        if app_data == k_esc:
            dpg.stop_dearpygui()
        elif app_data == k_left:
            spike.scroll_bars(-1) if ctrl_down() else spike.scroll_cells(-1)
        elif app_data == k_right:
            spike.scroll_bars(1) if ctrl_down() else spike.scroll_cells(1)
        elif app_data == k_up:
            spike.scroll_pitches(1)
        elif app_data == k_down:
            spike.scroll_pitches(-1)
        elif app_data == k_pgup:
            spike.scroll_pitches(spike.visible_rows)
        elif app_data == k_pgdn:
            spike.scroll_pitches(-spike.visible_rows)
        elif app_data == k_home:
            spike.col0 = 0
            spike.dirty = True

    def on_move(sender, app_data):
        if dpg.is_item_hovered("dl"):
            x, y = dpg.get_drawing_mouse_pos()
            h = spike.cell_at(x, y)
        else:
            h = None
        if h != spike.hover:
            spike.hover = h
            spike.hover_dirty = True

    with dpg.handler_registry():
        dpg.add_mouse_wheel_handler(callback=on_wheel)
        dpg.add_key_press_handler(callback=on_key)
        dpg.add_mouse_move_handler(callback=on_move)

    def on_resize(sender, app_data):
        # Size comes from the viewport's client area and nothing else; zoom is
        # untouched. (The viewport's own width/height include the window
        # decorations, which clipped the lane and the status line.)
        w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        if w <= 0 or h <= 0:
            return
        spike.vw, spike.vh = w, h
        dpg.configure_item("dl", width=w, height=max(1, h - STATUS_H))
        spike.dirty = True

    with dpg.theme() as flush_theme:
        with dpg.theme_component(dpg.mvWindowAppItem):
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
    dpg.bind_item_theme("main", flush_theme)

    dpg.set_viewport_resize_callback(on_resize)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.render_dearpygui_frame()
    on_resize(None, None)

    bench_until = time.perf_counter() + args.bench if args.bench > 0 else None
    frame = 0
    last_fps_sample = time.perf_counter()
    zoom_phase = 0
    shot_frames_left = 12 if args.screenshot else None
    while dpg.is_dearpygui_running():
        if shot_frames_left is not None:
            # A few frames so the window exists and the first paint has landed,
            # then the frame buffer, then a few more so the write completes.
            shot_frames_left -= 1
            if shot_frames_left == 6:
                spike.hover = (8, spike.col0 + 6)
                spike.hover_dirty = True
            if shot_frames_left == 3:
                dpg.output_frame_buffer(args.screenshot)
            if shot_frames_left == 0:
                break
        if bench_until is not None:
            now = time.perf_counter()
            if now >= bench_until:
                break
            frame += 1
            spike.scroll_cells(2)                       # a steady scroll, every frame
            if frame % 45 == 0:
                spike.scroll_pitches(2 if (frame // 45) % 2 else -2)
            if frame % 120 == 0:
                zoom_phase += 1
                spike.set_cell_w([14, 8, 20, 32, 6][zoom_phase % 5])
            if frame % 300 == 0:
                spike.set_zoom(spike.zoom.finer() if zoom_phase % 2 else spike.zoom.coarser())
            if spike.last_matrix is not None:
                spike.hover = (min(5, spike.visible_rows - 1), spike.col0 + 10)
                spike.hover_dirty = True
            if now - last_fps_sample >= 0.5:
                spike.fps_samples.append(dpg.get_frame_rate())
                last_fps_sample = now
        if spike.dirty:
            spike.paint()
        if spike.hover_dirty:
            spike.paint_hover()
        dpg.render_dearpygui_frame()

    report = spike.report()
    dpg.destroy_context()
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
