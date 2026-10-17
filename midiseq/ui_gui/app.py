"""The window - step 3's shell.

A grid you can scroll and zoom, a ruler, a velocity lane, a status line that
names the cell under the mouse, a File menu to open another file, and a View
menu for the track, the ghosts and the octave convention. **No editing.**
That is step 4, and it waits until this picture has been judged readable
(PLAN.md §14: readability is the expensive thing to change later).

    seq gui song.mid -t 2
    python -m midiseq.ui_gui song.mid

Mouse: wheel scrolls pitches, Shift+wheel scrolls time, Ctrl+wheel changes
the cell width. Hover names the cell. One modifier per gesture, never two:
the first thing Matthew's eyes found (2026-10-18) was that Ctrl+Shift+wheel
and Ctrl+wheel did the same thing on his machine, so the musical zoom lives
on keys instead.

Keys: ``.`` and ``,`` zoom the grid finer and coarser (1/4 ... 1/64 - the
zoom PLAN.md §6 means, the one that changes what a cell is), also Ctrl+= and
Ctrl+-; + and - change the cell width; arrows move a cell or a pitch,
Ctrl+Left/Right a bar, PageUp/PageDown a screen of pitches, Home/End the
first/last bar; Tab and Shift+Tab change track; G toggles ghosts; F fits the
pitch range; Ctrl+Q quits.

Everything DearPyGui-specific is in this file and ``painter.py``. The state
is ``grid.GridWidget`` and the picture is ``paint.plan``; both run without a
window and are tested that way. Two rules from the spike hold here: zoom
changes the cell width and nothing else (no font scale, no widget resize), and
the window's size comes from the viewport's client area and nowhere else.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import dearpygui.dearpygui as dpg

from ..core import io_midi
from ..core.grid import Zoom
from ..core.model import (
    DEFAULT_MIDDLE_C_OCTAVE,
    MIDDLE_C_FL,
    MIDDLE_C_SCIENTIFIC,
    MIDDLE_C_YAMAHA,
    Song,
    Track,
)
from .grid import GridWidget
from .painter import DpgPainter

MIDDLE_C = {"c3": MIDDLE_C_YAMAHA, "c4": MIDDLE_C_SCIENTIFIC, "c5": MIDDLE_C_FL}

STATUS_H = 22
FLASH_SECONDS = 5.0

WINDOW = "midiseq_window"
DRAWLIST = "midiseq_grid"
LAYER_GRID = "midiseq_grid_layer"
LAYER_HOVER = "midiseq_hover_layer"
STATUS = "midiseq_status"
TRACK_MENU = "midiseq_track_menu"
GHOSTS_ITEM = "midiseq_ghosts_item"
FILE_DIALOG = "midiseq_file_dialog"


# --------------------------------------------------------------------------
# Keys. DearPyGui renamed several constants between 1.x and 2.x; resolving by
# name keeps the app running on whichever is installed.
# --------------------------------------------------------------------------

def _key(*names: str) -> int | None:
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
        except Exception:  # noqa: BLE001 - key not in this build
            pass
    return False


def ctrl_down() -> bool:
    return _any_down("mvKey_LControl", "mvKey_RControl", "mvKey_Control", "mvKey_ModCtrl")


def shift_down() -> bool:
    return _any_down("mvKey_LShift", "mvKey_RShift", "mvKey_Shift", "mvKey_ModShift")


KEYS = {
    "left": _key("mvKey_Left"), "right": _key("mvKey_Right"),
    "up": _key("mvKey_Up"), "down": _key("mvKey_Down"),
    "pgup": _key("mvKey_Prior", "mvKey_PageUp"), "pgdn": _key("mvKey_Next", "mvKey_PageDown"),
    "home": _key("mvKey_Home"), "end": _key("mvKey_End"),
    "tab": _key("mvKey_Tab"), "g": _key("mvKey_G"), "f": _key("mvKey_F"), "q": _key("mvKey_Q"),
    "plus": _key("mvKey_Plus", "mvKey_Equal"), "add": _key("mvKey_Add"),
    "minus": _key("mvKey_Minus"), "subtract": _key("mvKey_Subtract"),
    "period": _key("mvKey_Period"), "comma": _key("mvKey_Comma"),
}
#: Keys that act once per press, however long they're held.
ONE_SHOT = {KEYS[k] for k in ("tab", "g", "f", "q", "plus", "add", "minus", "subtract",
                              "period", "comma") if KEYS[k] is not None}


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------

class GridApp:
    def __init__(self, song: Song, path: Path | None = None, *, track: int | None = None,
                 zoom: Zoom = Zoom(16), ghosts: bool = True,
                 middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE,
                 width: int = 1280, height: int = 760) -> None:
        self.song = song
        self.path = path
        self.widget = GridWidget(song, track, zoom, ghosts=ghosts, middle_c_octave=middle_c_octave)
        self.painter = DpgPainter(dpg)
        self.width, self.height = width, height
        self._held: set[int] = set()
        self._flash: tuple[str, float] | None = None
        self._status_shown = ""

    # -- building -----------------------------------------------------------

    def _title(self) -> str:
        return f"midiseq - {self.path.name}" if self.path else "midiseq"

    def build(self) -> None:
        dpg.create_context()
        dpg.create_viewport(title=self._title(), width=self.width, height=self.height)

        with dpg.window(tag=WINDOW, no_title_bar=True, no_move=True, no_resize=True,
                        no_scrollbar=True):
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="Open...", callback=lambda: dpg.show_item(FILE_DIALOG))
                    dpg.add_separator()
                    dpg.add_menu_item(label="Quit    Ctrl+Q", callback=lambda: dpg.stop_dearpygui())
                with dpg.menu(label="View"):
                    dpg.add_menu_item(label="Finer grid (1/4 .. 1/64)    .   Ctrl+=",
                                      callback=lambda: self.widget.zoom_finer())
                    dpg.add_menu_item(label="Coarser grid    ,   Ctrl+-",
                                      callback=lambda: self.widget.zoom_coarser())
                    dpg.add_separator()
                    dpg.add_menu_item(label="Wider cells    +   Ctrl+wheel",
                                      callback=lambda: self.widget.zoom_pixels(+1))
                    dpg.add_menu_item(label="Narrower cells    -",
                                      callback=lambda: self.widget.zoom_pixels(-1))
                    dpg.add_separator()
                    dpg.add_menu_item(label="Ghost tracks    G", check=True, tag=GHOSTS_ITEM,
                                      default_value=self.widget.ghosts,
                                      callback=lambda s, a: self.widget.set_ghosts(bool(a)))
                    dpg.add_menu_item(label="Fit pitch range    F", callback=lambda: self.widget.fit_pitch_range())
                    dpg.add_separator()
                    with dpg.menu(label="Track    Tab", tag=TRACK_MENU):
                        pass
                    with dpg.menu(label="Middle C is"):
                        for label, octave in (("C3  (Yamaha)", MIDDLE_C_YAMAHA),
                                              ("C4  (scientific)", MIDDLE_C_SCIENTIFIC),
                                              ("C5  (FL Studio)", MIDDLE_C_FL)):
                            dpg.add_menu_item(label=label, user_data=octave,
                                              callback=lambda s, a, u: self.widget.set_middle_c(u))
            with dpg.drawlist(width=self.width, height=max(1, self.height - STATUS_H - 30), tag=DRAWLIST):
                dpg.add_draw_layer(tag=LAYER_GRID)
                dpg.add_draw_layer(tag=LAYER_HOVER)
            dpg.add_text("", tag=STATUS, indent=6)
        dpg.set_primary_window(WINDOW, True)
        self._rebuild_track_menu()

        with dpg.file_dialog(directory_selector=False, show=False, modal=True,
                             callback=self._on_file_chosen, tag=FILE_DIALOG,
                             width=720, height=420,
                             default_path=str(self.path.parent) if self.path else ""):
            dpg.add_file_extension(".mid", color=(230, 170, 60, 255))
            dpg.add_file_extension(".*")

        with dpg.handler_registry():
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
            dpg.add_key_press_handler(callback=self._on_key)
            dpg.add_key_release_handler(callback=self._on_key_release)
            dpg.add_mouse_move_handler(callback=self._on_move)

        with dpg.theme() as flush:
            with dpg.theme_component(dpg.mvWindowAppItem):
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_item_theme(WINDOW, flush)

        dpg.set_viewport_resize_callback(self._on_resize)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.render_dearpygui_frame()
        self._on_resize()

    def _rebuild_track_menu(self) -> None:
        dpg.delete_item(TRACK_MENU, children_only=True)
        for i, t in enumerate(self.song.tracks):
            name = t.name or "(unnamed)"
            label = f"{i}   {name}   {len(t.notes)} notes"
            dpg.add_menu_item(label=label, parent=TRACK_MENU, check=True,
                              default_value=(i == self.widget.track), tag=f"midiseq_track_{i}",
                              user_data=i, callback=lambda s, a, u: self._select_track(u))

    def _sync_track_checks(self) -> None:
        for i in range(len(self.song.tracks)):
            tag = f"midiseq_track_{i}"
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, i == self.widget.track)

    # -- opening files --------------------------------------------------------

    def open(self, path: str | Path) -> bool:
        path = Path(path)
        try:
            song = io_midi.load(path)
        except Exception as exc:  # noqa: BLE001 - UI boundary
            self.flash(f"could not read {path.name}: {exc}")
            return False
        old = self.widget
        try:
            widget = GridWidget(song, None, old.zoom, ghosts=old.ghosts,
                                middle_c_octave=old.middle_c_octave, layout=old.layout)
        except ValueError as exc:
            self.flash(f"{path.name}: {exc}")
            return False
        self.song, self.path, self.widget = song, path, widget
        widget.resize(old.width, old.height)
        dpg.set_viewport_title(self._title())
        self._rebuild_track_menu()
        self.flash(f"opened {path.name}: {len(song.tracks)} tracks")
        return True

    def _on_file_chosen(self, sender, app_data) -> None:
        chosen = (app_data or {}).get("file_path_name")
        if chosen:
            self.open(chosen)

    def flash(self, message: str) -> None:
        """Show a message on the status line for a few seconds."""
        self._flash = (message, time.perf_counter() + FLASH_SECONDS)
        self._set_status(message)

    # -- events -----------------------------------------------------------------

    def _select_track(self, index: int) -> None:
        self.widget.set_track(index)
        self._sync_track_checks()

    def _on_wheel(self, sender, app_data) -> None:
        # One modifier per gesture. Ctrl wins if both are held, so a driver
        # that eats Shift+wheel can't turn a zoom into a scroll.
        direction = 1 if app_data > 0 else -1
        w = self.widget
        if ctrl_down():
            w.zoom_pixels(direction)
        elif shift_down():
            w.scroll_cells(-4 * direction)
        else:
            w.scroll_pitches(3 * direction)

    def _on_key(self, sender, app_data) -> None:
        key = app_data
        if key in ONE_SHOT:
            if key in self._held:
                return
            self._held.add(key)
        w, k = self.widget, KEYS
        if key == k["left"]:
            w.scroll_bars(-1) if ctrl_down() else w.scroll_cells(-1)
        elif key == k["right"]:
            w.scroll_bars(1) if ctrl_down() else w.scroll_cells(1)
        elif key == k["up"]:
            w.scroll_pitches(1)
        elif key == k["down"]:
            w.scroll_pitches(-1)
        elif key == k["pgup"]:
            w.scroll_pitches(w.visible_rows)
        elif key == k["pgdn"]:
            w.scroll_pitches(-w.visible_rows)
        elif key == k["home"]:
            w.go_to_bar(1)
        elif key == k["end"]:
            w.go_to_bar(w.view.bars.bar_containing(max(0, self.song.end_tick() - 1)).number)
        elif key == k["tab"]:
            n = len(self.song.tracks)
            self._select_track((w.track + (-1 if shift_down() else 1)) % n)
        elif key == k["g"]:
            w.set_ghosts(not w.ghosts)
            if dpg.does_item_exist(GHOSTS_ITEM):
                dpg.set_value(GHOSTS_ITEM, w.ghosts)
        elif key == k["f"]:
            w.fit_pitch_range()
        elif key == k["period"]:
            w.zoom_finer()
        elif key == k["comma"]:
            w.zoom_coarser()
        elif key in (k["plus"], k["add"]):
            w.zoom_finer() if ctrl_down() else w.zoom_pixels(+1)
        elif key in (k["minus"], k["subtract"]):
            w.zoom_coarser() if ctrl_down() else w.zoom_pixels(-1)
        elif key == k["q"] and ctrl_down():
            dpg.stop_dearpygui()

    def _on_key_release(self, sender, app_data) -> None:
        self._held.discard(app_data)

    def _on_move(self, sender, app_data) -> None:
        if dpg.is_item_hovered(DRAWLIST):
            x, y = dpg.get_drawing_mouse_pos()
            self.widget.set_hover(x, y)
        else:
            self.widget.set_hover(None, None)

    def _on_resize(self, *_args) -> None:
        # Size comes from the viewport's client area and nothing else.
        w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        if w <= 0 or h <= 0:
            return
        try:
            top = int(dpg.get_item_rect_min(DRAWLIST)[1])
        except Exception:  # noqa: BLE001 - before the first frame
            top = 0
        if top <= 0 or top > 80:
            top = 24          # a menu bar's worth, if the measurement is off
        grid_h = max(1, h - top - STATUS_H)
        dpg.configure_item(DRAWLIST, width=w, height=grid_h)
        self.widget.resize(w, grid_h)

    # -- frames -------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        if text != self._status_shown:
            self._status_shown = text
            dpg.set_value(STATUS, text)

    def frame(self) -> None:
        w = self.widget
        if w.dirty:
            plan = w.build()
            self.painter.repaint(plan.items, LAYER_GRID)
        if w.hover_dirty:
            self.painter.repaint(w.hover_items(), LAYER_HOVER)
            if self._flash is None:
                self._set_status(w.status())
        if self._flash is not None and time.perf_counter() >= self._flash[1]:
            self._flash = None
            self._set_status(w.status())

    def run(self, screenshot: str | None = None) -> int:
        self.build()
        shot_frames_left = 12 if screenshot else None
        while dpg.is_dearpygui_running():
            if shot_frames_left is not None:
                shot_frames_left -= 1
                if shot_frames_left == 6 and self.widget.plan is not None:
                    row, cell = 8, self.widget.first_cell + 6
                    x0, y0, _, _ = self.widget.layout.cell_rect(row, cell, self.widget.first_cell)
                    self.widget.set_hover(x0 + 2, y0 + 2)
                if shot_frames_left == 3:
                    dpg.output_frame_buffer(screenshot)
                if shot_frames_left == 0:
                    break
            self.frame()
            dpg.render_dearpygui_frame()
        dpg.destroy_context()
        return 0


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def empty_song() -> Song:
    song = Song(ppq=480)
    song.tracks.append(Track(name="Track 1"))
    song.normalise()
    return song


def launch(path: str | None = None, *, track: int | None = None, zoom: Zoom = Zoom(16),
           middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE, ghosts: bool = True, bar: int = 1,
           width: int = 1280, height: int = 760, screenshot: str | None = None) -> int:
    """Open the window. Shared by ``seq gui`` and ``python -m midiseq.ui_gui``."""
    if path is None:
        song, file = empty_song(), None
    else:
        file = Path(path)
        if not file.exists():
            print(f"seq gui: no such file: {file}", file=sys.stderr)
            return 2
        try:
            song = io_midi.load(file)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(f"seq gui: could not read {file}: {exc}", file=sys.stderr)
            return 1
    if track is not None and not 0 <= track < len(song.tracks):
        print(f"seq gui: no track {track} (file has {len(song.tracks)})", file=sys.stderr)
        return 2
    try:
        app = GridApp(song, file, track=track, zoom=zoom, ghosts=ghosts,
                      middle_c_octave=middle_c_octave, width=width, height=height)
    except ValueError as exc:
        print(f"seq gui: {exc}", file=sys.stderr)
        return 1
    if bar > 1:
        app.widget.go_to_bar(bar)
    return app.run(screenshot)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m midiseq.ui_gui",
                                 description="The grid window (step 3: look, don't touch).")
    ap.add_argument("file", nargs="?", help="a .mid to open (default: an empty song)")
    ap.add_argument("-t", "--track", type=int, default=None)
    ap.add_argument("-z", "--zoom", type=int, default=16, choices=Zoom.SUPPORTED)
    ap.add_argument("--middle-c", choices=sorted(MIDDLE_C), default="c4")
    ap.add_argument("--no-ghosts", action="store_true")
    ap.add_argument("--bar", type=int, default=1, help="start with this bar at the left edge")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=760)
    ap.add_argument("--screenshot", metavar="PNG", default=None,
                    help="paint once, write the frame buffer to PNG, quit (development)")
    args = ap.parse_args(argv)
    return launch(args.file, track=args.track, zoom=Zoom(args.zoom),
                  middle_c_octave=MIDDLE_C[args.middle_c], ghosts=not args.no_ghosts,
                  bar=args.bar, width=args.width, height=args.height,
                  screenshot=args.screenshot)


__all__ = ["GridApp", "launch", "main"]
