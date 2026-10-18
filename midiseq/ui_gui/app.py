"""The window.

Step 3 made it a shell: a grid you can scroll and zoom, a ruler, a velocity
lane, a status line that names the cell under the mouse, File and View
menus. Step 4 made it an editor: the mouse model from PLAN.md §11 (click
empty to insert and hear it, drag to move, right-click to delete, Shift+drag
to marquee, Ctrl to ignore snap, drag an edge to resize), a selection, undo,
an Edit menu and a Notes menu generated from the command registry, a
command palette on Ctrl+P, and New / Save / Save As with an unsaved-changes
guard. Step 5 made it play: a transport bar under the menu (Play/Stop,
rewind, the position and tempo, the audition toggle, which MIDI output), a
playhead that follows the song page by page, and a click in the ruler to
seek.

    seq gui song.mid -t 2
    python -m midiseq.ui_gui song.mid --midi-out "GS Wavetable"

Mouse: wheel scrolls pitches, Shift+wheel scrolls time, Ctrl+wheel changes
the cell width. Hover names the cell. A left click in the ruler moves the
playhead there (and restarts playback from there if rolling). A left drag
in the velocity lane sets the bars it crosses (Shift+drag to one level);
hovering the lane names the bar. One modifier per gesture, never two.

Keys: Space plays and stops; Shift+Space plays from the left edge of the
view; ``.`` and ``,`` zoom the grid finer and coarser (1/4 ... 1/64), also
Ctrl+= and Ctrl+-; + and - change the cell width; arrows move a cell or a
pitch, or - with a selection - move the notes (Up/Down transposes,
Shift+Up/Down by scale degree, Ctrl+Up/Down changes velocity by 5 and
Ctrl+Shift+Up/Down by 1); Ctrl+Left/Right a bar, PageUp/PageDown a screen
of pitches, Home the first bar (playhead too), End the last; Tab and
Shift+Tab change track; G toggles ghosts; F fits the pitch range; A toggles
audition; Q quantises the selection to the grid; Del deletes it; Esc stops,
cancels, deselects; Ctrl+Z / Ctrl+Y undo and redo; Ctrl+A selects all;
Ctrl+X / C / V cut, copy and paste (at the playhead), Ctrl+D duplicates
the selection after itself; Ctrl+P the palette; Ctrl+N / O / S / Shift+S
new, open, save, save as; Ctrl+Q quits.

Editing while playing is allowed and never crashes, but the schedule the
sink is playing is the song as it was when Play was pressed: a note moved
mid-bar sounds in its old place until the next Play. Live-updating the
running schedule is a later step.

Everything DearPyGui-specific is in this file and ``painter.py``. The state
is ``grid.GridWidget``, the editing is ``editor.Editor`` and the picture is
``paint.plan``; all three run without a window and are tested that way. Two
rules from the spike hold here: zoom changes the cell width and nothing else
(no font scale, no widget resize - the transport bar is a fixed row above the
grid and never scales), and the window's size comes from the viewport's
client area and nowhere else.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable

import dearpygui.dearpygui as dpg

from ..audio.ports import list_ports, open_sink
from ..audio.sink_null import NullSink
from ..core import io_midi
from ..core.edit import REGISTRY
from ..core.grid import Zoom
from ..core.model import (
    DEFAULT_MIDDLE_C_OCTAVE,
    MIDDLE_C_FL,
    MIDDLE_C_SCIENTIFIC,
    MIDDLE_C_YAMAHA,
    Song,
    Track,
)
from .editor import (
    PROMPTS, Editor, MenuEntry, TransportState, edit_menu, menu_sections, palette,
)
from .grid import GridWidget
from .painter import DpgPainter

MIDDLE_C = {"c3": MIDDLE_C_YAMAHA, "c4": MIDDLE_C_SCIENTIFIC, "c5": MIDDLE_C_FL}

STATUS_H = 22
MENU_H = 24
TRANSPORT_H = 30        # the bar's row; a fixed size, never scaled by zoom
FLASH_SECONDS = 5.0
SILENT = "(silent)"     # the output combo's entry for the null sink

WINDOW = "midiseq_window"
DRAWLIST = "midiseq_grid"
LAYER_GRID = "midiseq_grid_layer"
LAYER_GESTURE = "midiseq_gesture_layer"
LAYER_HOVER = "midiseq_hover_layer"
LAYER_PLAYHEAD = "midiseq_playhead_layer"
TRANSPORT_BAR = "midiseq_transport"
PLAY_BUTTON = "midiseq_play"
POSITION_TEXT = "midiseq_position"
TEMPO_TEXT = "midiseq_tempo"
AUDITION_CHECK = "midiseq_audition_check"
OUTPUT_COMBO = "midiseq_output"
STATUS = "midiseq_status"
TRACK_MENU = "midiseq_track_menu"
GHOSTS_ITEM = "midiseq_ghosts_item"
AUDITION_ITEM = "midiseq_audition_item"
UNDO_ITEM = "midiseq_menu_edit.undo"
REDO_ITEM = "midiseq_menu_edit.redo"
FILE_DIALOG = "midiseq_file_dialog"
SAVE_DIALOG = "midiseq_save_dialog"
PROMPT = "midiseq_prompt"
PALETTE = "midiseq_palette"
PALETTE_INPUT = "midiseq_palette_input"
PALETTE_LIST = "midiseq_palette_list"
CONFIRM = "midiseq_confirm"
MODALS = (PROMPT, PALETTE, CONFIRM)


# --------------------------------------------------------------------------
# Keys. DearPyGui renamed several constants between 1.x and 2.x; resolving by
# name keeps the app running on whichever is installed. The editor gets
# names, not codes.
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


_KEY_SPECS = {
    "left": ("mvKey_Left",), "right": ("mvKey_Right",), "up": ("mvKey_Up",), "down": ("mvKey_Down",),
    "pgup": ("mvKey_Prior", "mvKey_PageUp"), "pgdn": ("mvKey_Next", "mvKey_PageDown"),
    "home": ("mvKey_Home",), "end": ("mvKey_End",), "tab": ("mvKey_Tab",),
    "escape": ("mvKey_Escape",), "delete": ("mvKey_Delete",),
    "backspace": ("mvKey_Back", "mvKey_Backspace"), "enter": ("mvKey_Return", "mvKey_Enter"),
    "space": ("mvKey_Spacebar", "mvKey_Space"),
    "plus": ("mvKey_Plus", "mvKey_Equal"), "add": ("mvKey_Add",),
    "minus": ("mvKey_Minus",), "subtract": ("mvKey_Subtract",),
    "period": ("mvKey_Period",), "comma": ("mvKey_Comma",),
    "a": ("mvKey_A",), "c": ("mvKey_C",), "d": ("mvKey_D",), "f": ("mvKey_F",), "g": ("mvKey_G",),
    "n": ("mvKey_N",), "o": ("mvKey_O",), "p": ("mvKey_P",), "q": ("mvKey_Q",), "s": ("mvKey_S",),
    "v": ("mvKey_V",), "x": ("mvKey_X",), "y": ("mvKey_Y",), "z": ("mvKey_Z",),
}
KEY_NAMES: dict[int, str] = {}
for _name, _attrs in _KEY_SPECS.items():
    _code = _key(*_attrs)
    if _code is not None:
        KEY_NAMES.setdefault(_code, _name)

#: Keys that act once per press, however long they're held. Arrows repeat.
ONE_SHOT = {"tab", "f", "g", "q", "a", "c", "d", "n", "o", "p", "s", "v", "x", "y", "z",
            "plus", "add", "minus", "subtract", "period", "comma", "escape", "delete",
            "backspace", "enter", "space"}


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------

class GridApp:
    def __init__(self, song: Song, path: Path | None = None, *, track: int | None = None,
                 zoom: Zoom = Zoom(16), ghosts: bool = True,
                 middle_c_octave: int = DEFAULT_MIDDLE_C_OCTAVE,
                 width: int = 1280, height: int = 760, midi_out: str | None = None) -> None:
        self.song = song
        self.path = path
        # Opened once; it outlives every song this window shows.
        self.sink, self.sink_note = open_sink(midi_out)
        self.widget = GridWidget(song, track, zoom, ghosts=ghosts, middle_c_octave=middle_c_octave)
        self.editor = Editor(self.widget, self.sink)
        self.painter = DpgPainter(dpg)
        self.width, self.height = width, height
        self._held: set[str] = set()
        self._down: set[int] = set()
        self._flash: tuple[str, float] | None = None
        self._status_shown = ""
        self._title_shown = ""
        self._undo_labels = ("", "")
        self._transport_shown: TransportState | None = None
        self._palette_query: str | None = None
        self._palette_entries: list[MenuEntry] = []
        self._after_save: Callable[[], None] | None = None

    # -- building -----------------------------------------------------------

    def _title(self) -> str:
        name = self.path.name if self.path else "untitled"
        star = "*" if self.editor.dirty_since_save else ""
        return f"midiseq - {name}{star}"

    def build(self) -> None:
        dpg.create_context()
        dpg.create_viewport(title=self._title(), width=self.width, height=self.height)

        with dpg.window(tag=WINDOW, no_title_bar=True, no_move=True, no_resize=True,
                        no_scrollbar=True):
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="New    Ctrl+N", callback=lambda: self._guard(self.new))
                    dpg.add_menu_item(label="Open...    Ctrl+O",
                                      callback=lambda: self._guard(lambda: dpg.show_item(FILE_DIALOG)))
                    dpg.add_separator()
                    dpg.add_menu_item(label="Save    Ctrl+S", callback=lambda: self.save())
                    dpg.add_menu_item(label="Save as...    Ctrl+Shift+S", callback=lambda: self.save_as())
                    dpg.add_separator()
                    dpg.add_menu_item(label="Quit    Ctrl+Q",
                                      callback=lambda: self._guard(dpg.stop_dearpygui))
                with dpg.menu(label="Edit"):
                    for e in edit_menu():
                        if e.key == "edit.audition":
                            dpg.add_menu_item(label=f"{e.label}    {e.shortcut}", check=True,
                                              tag=AUDITION_ITEM,
                                              default_value=self.editor.transport.audition_enabled,
                                              callback=lambda s, a: self._set_audition(bool(a)))
                            continue
                        dpg.add_menu_item(label=self._menu_label(e), user_data=e.key,
                                          tag=f"midiseq_menu_{e.key}", callback=self._on_entry)
                        if e.key in ("edit.redo", "edit.duplicate"):
                            dpg.add_separator()     # undo | clipboard | selection
                    dpg.add_separator()
                    dpg.add_menu_item(label="Command palette...    Ctrl+P",
                                      callback=lambda: self.open_palette())
                with dpg.menu(label="Notes"):
                    for i, (name, entries) in enumerate(menu_sections()):
                        if i:
                            dpg.add_separator()
                        dpg.add_menu_item(label=f"- {name} -", enabled=False)
                        for e in entries:
                            dpg.add_menu_item(label=self._menu_label(e), user_data=e.key,
                                              callback=self._on_entry)
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
                    dpg.add_menu_item(label="Fit pitch range    F",
                                      callback=lambda: self.widget.fit_pitch_range())
                    dpg.add_separator()
                    with dpg.menu(label="Track    Tab", tag=TRACK_MENU):
                        pass
                    with dpg.menu(label="Middle C is"):
                        for label, octave in (("C3  (Yamaha)", MIDDLE_C_YAMAHA),
                                              ("C4  (scientific)", MIDDLE_C_SCIENTIFIC),
                                              ("C5  (FL Studio)", MIDDLE_C_FL)):
                            dpg.add_menu_item(label=label, user_data=octave,
                                              callback=lambda s, a, u: self.widget.set_middle_c(u))
            self._build_transport_bar()
            with dpg.drawlist(width=self.width,
                              height=max(1, self.height - MENU_H - TRANSPORT_H - STATUS_H),
                              tag=DRAWLIST):
                dpg.add_draw_layer(tag=LAYER_GRID)
                dpg.add_draw_layer(tag=LAYER_GESTURE)
                dpg.add_draw_layer(tag=LAYER_HOVER)
                dpg.add_draw_layer(tag=LAYER_PLAYHEAD)
            dpg.add_text("", tag=STATUS, indent=6)
        dpg.set_primary_window(WINDOW, True)
        self._rebuild_track_menu()
        self.flash(f"MIDI out: {self.sink_note}")

        start_dir = str(self.path.parent) if self.path else ""
        with dpg.file_dialog(directory_selector=False, show=False, modal=True,
                             callback=self._on_file_chosen, tag=FILE_DIALOG,
                             width=720, height=420, default_path=start_dir):
            dpg.add_file_extension(".mid", color=(230, 170, 60, 255))
            dpg.add_file_extension(".*")
        with dpg.file_dialog(directory_selector=False, show=False, modal=True,
                             callback=self._on_save_chosen, tag=SAVE_DIALOG,
                             width=720, height=420, default_path=start_dir,
                             default_filename="untitled"):
            dpg.add_file_extension(".mid", color=(230, 170, 60, 255))
            dpg.add_file_extension(".*")

        with dpg.handler_registry():
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
            dpg.add_mouse_down_handler(callback=self._on_mouse_down)
            dpg.add_mouse_release_handler(callback=self._on_mouse_release)
            dpg.add_mouse_move_handler(callback=self._on_move)
            dpg.add_key_press_handler(callback=self._on_key)
            dpg.add_key_release_handler(callback=self._on_key_release)

        with dpg.theme() as flush:
            with dpg.theme_component(dpg.mvWindowAppItem):
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_item_theme(WINDOW, flush)

        dpg.set_viewport_resize_callback(self._on_resize)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.render_dearpygui_frame()
        self._on_resize()

    def _build_transport_bar(self) -> None:
        """One fixed row: Play/Stop, rewind, position, tempo, audition, output.
        Labels are ASCII because the default font paints anything above
        U+00FF as a question mark."""
        state = self.editor.transport_state()
        ports = list_ports()
        chosen = self.sink_note if self.sink_note in ports else SILENT
        with dpg.group(horizontal=True, tag=TRANSPORT_BAR, indent=6):
            dpg.add_button(label=state.play_label, tag=PLAY_BUTTON, width=52,
                           callback=lambda: self._toggle_play())
            dpg.add_button(label="|<", width=30, callback=lambda: self.editor.rewind())
            dpg.add_text(state.position, tag=POSITION_TEXT)
            dpg.add_text(state.tempo, tag=TEMPO_TEXT)
            dpg.add_checkbox(label="Audition", tag=AUDITION_CHECK, default_value=state.audition,
                             callback=lambda s, a: self._set_audition(bool(a)))
            dpg.add_combo(items=[SILENT] + ports, default_value=chosen, tag=OUTPUT_COMBO,
                          width=260, callback=lambda s, a: self._choose_output(str(a)))

    @staticmethod
    def _menu_label(e: MenuEntry) -> str:
        return f"{e.label}    {e.shortcut}" if e.shortcut else e.label

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

    # -- songs: new, open, save ----------------------------------------------------

    def _install(self, song: Song, path: Path | None) -> None:
        self.editor.transport.stop()        # the old song's schedule must not outlive it
        old = self.widget
        widget = GridWidget(song, None, old.zoom, ghosts=old.ghosts,
                            middle_c_octave=old.middle_c_octave, layout=old.layout)
        widget.resize(old.width, old.height)
        self.song, self.path, self.widget = song, path, widget
        self.editor = Editor(widget, self.sink)
        self._sync_audition_check()
        self._rebuild_track_menu()
        self._update_title()

    def new(self) -> None:
        self._install(empty_song(), None)
        self.flash("new song: one empty track")

    def open(self, path: str | Path) -> bool:
        path = Path(path)
        try:
            song = io_midi.load(path)
        except Exception as exc:  # noqa: BLE001 - UI boundary
            self.flash(f"could not read {path.name}: {exc}")
            return False
        try:
            self._install(song, path)
        except ValueError as exc:
            self.flash(f"{path.name}: {exc}")
            return False
        self.flash(f"opened {path.name}: {len(song.tracks)} tracks")
        return True

    def save(self) -> bool:
        if self.path is None:
            self.save_as()
            return False
        try:
            io_midi.save(self.song, self.path)
        except Exception as exc:  # noqa: BLE001 - UI boundary
            self.flash(f"could not save {self.path.name}: {exc}")
            return False
        self.editor.mark_saved()
        self.flash(f"saved {self.path.name}")
        self._update_title()
        return True

    def save_as(self) -> None:
        dpg.show_item(SAVE_DIALOG)

    def _on_file_chosen(self, sender, app_data) -> None:
        chosen = (app_data or {}).get("file_path_name")
        if chosen:
            self.open(chosen)

    def _on_save_chosen(self, sender, app_data) -> None:
        chosen = (app_data or {}).get("file_path_name")
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() not in (".mid", ".midi"):
            path = path.with_suffix(".mid")
        self.path = path
        if self.save() and self._after_save is not None:
            after, self._after_save = self._after_save, None
            after()

    def _guard(self, then: Callable[[], None]) -> None:
        """Run ``then`` now, or after the unsaved-changes question."""
        if not self.editor.dirty_since_save:
            then()
            return
        self._close_modal()
        name = self.path.name if self.path else "this song"

        def save_then() -> None:
            self._close_modal()
            if self.path is None:
                self._after_save = then
                self.save_as()
            elif self.save():
                then()

        def discard() -> None:
            self._close_modal()
            self.editor.mark_saved()
            then()

        with dpg.window(tag=CONFIRM, label="Unsaved changes", modal=True, autosize=True,
                        no_resize=True, pos=(420, 260), on_close=lambda: self._close_modal()):
            dpg.add_text(f"{name} has unsaved changes.")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=save_then)
                dpg.add_button(label="Don't save", callback=discard)
                dpg.add_button(label="Cancel", callback=lambda: self._close_modal())

    # -- the registry's doors: menu items, prompts, the palette --------------------

    def _on_entry(self, sender, app_data, user_data) -> None:
        self.run_entry(user_data)

    def run_entry(self, key: str) -> None:
        if key in PROMPTS:
            self._prompt(key)
        else:
            self.flash(self.editor.run_key(key))
            self._sync_audition_check()

    def _prompt(self, key: str) -> None:
        if not self.editor.selection:
            self.flash(f"{REGISTRY[key].label.rstrip('.')}: nothing selected")
            return
        self._close_modal()
        fields = PROMPTS[key]

        def ok() -> None:
            raw = {name: str(dpg.get_value(f"{PROMPT}_{name}")) for name, _, _ in fields}
            self._close_modal()
            self.flash(self.editor.run_key(key, raw))

        with dpg.window(tag=PROMPT, label=REGISTRY[key].label.rstrip("."), modal=True,
                        autosize=True, no_resize=True, pos=(380, 220),
                        on_close=lambda: self._close_modal()):
            for name, caption, default in fields:
                dpg.add_input_text(label=caption, default_value=default, width=220,
                                   tag=f"{PROMPT}_{name}", on_enter=True, callback=lambda: ok())
            with dpg.group(horizontal=True):
                dpg.add_button(label="OK", callback=lambda: ok())
                dpg.add_button(label="Cancel", callback=lambda: self._close_modal())
        dpg.focus_item(f"{PROMPT}_{fields[0][0]}")

    def open_palette(self) -> None:
        self._close_modal()
        self._palette_query = None
        with dpg.window(tag=PALETTE, label="Command palette", modal=True, no_resize=True,
                        width=480, height=330, pos=(400, 120), on_close=lambda: self._close_modal()):
            dpg.add_input_text(tag=PALETTE_INPUT, hint="type a command, Enter runs the first match",
                               width=-1)
            dpg.add_listbox(items=[], tag=PALETTE_LIST, num_items=11, width=-1,
                            callback=lambda s, a: self._palette_pick(a))
        dpg.focus_item(PALETTE_INPUT)

    def _palette_refresh(self) -> None:
        if not dpg.does_item_exist(PALETTE):
            return
        query = str(dpg.get_value(PALETTE_INPUT) or "")
        if query == self._palette_query:
            return
        self._palette_query = query
        self._palette_entries = palette(query)
        dpg.configure_item(PALETTE_LIST, items=[self._menu_label(e) for e in self._palette_entries])

    def _palette_pick(self, label: str) -> None:
        for e in self._palette_entries:
            if self._menu_label(e) == label:
                self._close_modal()
                self.run_entry(e.key)
                return

    def _palette_run_first(self) -> None:
        self._palette_refresh()
        if self._palette_entries:
            key = self._palette_entries[0].key
            self._close_modal()
            self.run_entry(key)

    def _close_modal(self) -> None:
        for tag in MODALS:
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

    def _modal_open(self) -> bool:
        if any(dpg.does_item_exist(tag) for tag in MODALS):
            return True
        return any(dpg.does_item_exist(tag) and dpg.is_item_shown(tag)
                   for tag in (FILE_DIALOG, SAVE_DIALOG))

    def flash(self, message: str) -> None:
        """Show a message on the status line for a few seconds."""
        if not message:
            return
        self._flash = (message, time.perf_counter() + FLASH_SECONDS)
        self._set_status(message)

    # -- events -----------------------------------------------------------------

    def _select_track(self, index: int) -> None:
        self.widget.set_track(index)
        self._sync_track_checks()

    def _set_audition(self, on: bool) -> None:
        self.editor.transport.audition_enabled = on
        self.editor.message = f"Audition {'on' if on else 'off'}"

    def _sync_audition_check(self) -> None:
        """The Edit-menu check, the bar's checkbox and the A key are three
        doors on one flag; whichever moved it, the other two follow."""
        on = self.editor.transport.audition_enabled
        for tag in (AUDITION_ITEM, AUDITION_CHECK):
            if dpg.does_item_exist(tag) and dpg.get_value(tag) != on:
                dpg.set_value(tag, on)

    def _toggle_play(self) -> None:
        self.editor.transport.toggle()
        self.editor.playhead_dirty = True

    def _choose_output(self, name: str) -> None:
        """The output combo. Stops (``set_sink`` does), closes the old sink if
        it can be closed, and hands the editor the new one; the song is
        untouched."""
        old = self.sink
        if name == SILENT:
            sink, note = NullSink(keep_log=False), SILENT
        else:
            sink, note = open_sink(name)
        self.sink, self.sink_note = sink, note
        self.editor.set_sink(sink)
        close = getattr(old, "close", None)
        if callable(close) and old is not sink:
            close()
        self.flash(f"MIDI out: {note}")

    def _local_mouse(self) -> tuple[float, float]:
        gx, gy = dpg.get_mouse_pos(local=False)
        rx, ry = dpg.get_item_rect_min(DRAWLIST)
        return gx - rx, gy - ry

    def _on_wheel(self, sender, app_data) -> None:
        if self._modal_open():
            return
        direction = 1 if app_data > 0 else -1
        w = self.widget
        if ctrl_down():
            w.zoom_pixels(direction)
        elif shift_down():
            w.scroll_cells(-4 * direction)
        else:
            w.scroll_pitches(3 * direction)

    def _on_mouse_down(self, sender, app_data) -> None:
        button = int(app_data[0]) if isinstance(app_data, (list, tuple)) else int(app_data)
        if button in self._down:
            return
        self._down.add(button)
        if self._modal_open() or not dpg.is_item_hovered(DRAWLIST):
            return
        x, y = self._local_mouse()
        self.editor.press(x, y, button, shift=shift_down(), ctrl=ctrl_down())

    def _on_mouse_release(self, sender, app_data) -> None:
        button = int(app_data[0]) if isinstance(app_data, (list, tuple)) else int(app_data)
        self._down.discard(button)
        if self.editor.gesture is not None:
            x, y = self._local_mouse()
            self.editor.release(x, y, button, shift=shift_down(), ctrl=ctrl_down())

    def _on_move(self, sender, app_data) -> None:
        if self.editor.gesture is not None:
            x, y = self._local_mouse()
            self.editor.move(x, y, shift=shift_down(), ctrl=ctrl_down())
        if dpg.is_item_hovered(DRAWLIST) and not self._modal_open():
            x, y = dpg.get_drawing_mouse_pos()
            self.widget.set_hover(x, y)
        else:
            self.widget.set_hover(None, None)

    def _on_key(self, sender, app_data) -> None:
        name = KEY_NAMES.get(app_data)
        if name is None:
            return
        if name in ONE_SHOT:
            if name in self._held:
                return
            self._held.add(name)
        ctrl, shift = ctrl_down(), shift_down()

        if dpg.does_item_exist(PALETTE):
            if name == "enter":
                self._palette_run_first()
            elif name == "escape":
                self._close_modal()
            return
        if self._modal_open():
            if name == "escape":
                self._close_modal()
            return

        w = self.widget
        if ctrl and name == "q":
            self._guard(dpg.stop_dearpygui)
        elif ctrl and name == "n":
            self._guard(self.new)
        elif ctrl and name == "o":
            self._guard(lambda: dpg.show_item(FILE_DIALOG))
        elif ctrl and name == "s":
            self.save_as() if shift else self.save()
        elif ctrl and name == "p":
            self.open_palette()
        elif self.editor.key(name, shift=shift, ctrl=ctrl):
            self._sync_audition_check()
        elif name == "left":
            w.scroll_bars(-1) if ctrl else w.scroll_cells(-1)
        elif name == "right":
            w.scroll_bars(1) if ctrl else w.scroll_cells(1)
        elif name == "up":
            w.scroll_pitches(1)
        elif name == "down":
            w.scroll_pitches(-1)
        elif name == "pgup":
            w.scroll_pitches(w.visible_rows)
        elif name == "pgdn":
            w.scroll_pitches(-w.visible_rows)
        elif name == "end":
            w.go_to_bar(w.view.bars.bar_containing(max(0, self.song.end_tick() - 1)).number)
        elif name == "tab":
            n = len(self.song.tracks)
            self._select_track((w.track + (-1 if shift else 1)) % n)
        elif name == "g":
            w.set_ghosts(not w.ghosts)
            if dpg.does_item_exist(GHOSTS_ITEM):
                dpg.set_value(GHOSTS_ITEM, w.ghosts)
        elif name == "f":
            w.fit_pitch_range()
        elif name == "period":
            w.zoom_finer()
        elif name == "comma":
            w.zoom_coarser()
        elif name in ("plus", "add"):
            w.zoom_finer() if ctrl else w.zoom_pixels(+1)
        elif name in ("minus", "subtract"):
            w.zoom_coarser() if ctrl else w.zoom_pixels(-1)

    def _on_key_release(self, sender, app_data) -> None:
        name = KEY_NAMES.get(app_data)
        if name is not None:
            self._held.discard(name)

    def _on_resize(self, *_args) -> None:
        # Size comes from the viewport's client area and nothing else.
        w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        if w <= 0 or h <= 0:
            return
        try:
            top = int(dpg.get_item_rect_min(DRAWLIST)[1])
        except Exception:  # noqa: BLE001 - before the first frame
            top = 0
        if top <= 0 or top > 120:
            top = MENU_H + TRANSPORT_H
        grid_h = max(1, h - top - STATUS_H)
        dpg.configure_item(DRAWLIST, width=w, height=grid_h)
        self.widget.resize(w, grid_h)

    # -- frames -------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        if text != self._status_shown:
            self._status_shown = text
            dpg.set_value(STATUS, text)

    def _update_title(self) -> None:
        title = self._title()
        if title != self._title_shown:
            self._title_shown = title
            dpg.set_viewport_title(title)

    def _update_undo_labels(self) -> None:
        labels = (self.editor.history.undo_label or "", self.editor.history.redo_label or "")
        if labels != self._undo_labels:
            self._undo_labels = labels
            if dpg.does_item_exist(UNDO_ITEM):
                dpg.set_item_label(UNDO_ITEM, f"Undo {labels[0]}    Ctrl+Z" if labels[0] else "Undo    Ctrl+Z")
            if dpg.does_item_exist(REDO_ITEM):
                dpg.set_item_label(REDO_ITEM, f"Redo {labels[1]}    Ctrl+Y" if labels[1] else "Redo    Ctrl+Y")

    def _update_transport_bar(self) -> None:
        state = self.editor.transport_state()
        if state == self._transport_shown:
            return
        self._transport_shown = state
        if dpg.does_item_exist(PLAY_BUTTON):
            dpg.set_item_label(PLAY_BUTTON, state.play_label)
            dpg.set_value(POSITION_TEXT, state.position)
            dpg.set_value(TEMPO_TEXT, state.tempo)
        self._sync_audition_check()

    def frame(self) -> None:
        w, ed = self.widget, self.editor
        ed.frame()                              # the clock first: it may turn the page
        if w.dirty:
            plan = w.build()
            self.painter.repaint(plan.items, LAYER_GRID)
            ed.playhead_dirty = True            # its x is relative to the new left edge
        if ed.preview_dirty:
            self.painter.repaint(ed.preview_items(), LAYER_GESTURE)
        if w.hover_dirty:
            self.painter.repaint(w.hover_items(), LAYER_HOVER)
        if ed.playhead_dirty:
            self.painter.repaint(ed.playhead_items(), LAYER_PLAYHEAD)
        if self._flash is not None and time.perf_counter() >= self._flash[1]:
            self._flash = None
        if self._flash is None:
            self._set_status(ed.status_line())
        self._update_transport_bar()
        self._update_title()
        self._update_undo_labels()
        self._palette_refresh()

    def _screenshot_pose(self) -> None:
        """For ``--screenshot``: select the first note on screen, leave an
        insert drag in progress and park the playhead a few cells in, so the
        picture shows a selection outline, a gesture preview and the playhead
        as well as the grid. Nothing is committed."""
        w, ed = self.widget, self.editor
        m = w.matrix
        if m is None or w.plan is None:
            return
        ed.seek(w.tick_at_col(w.first_cell + 12))
        for ri, row in enumerate(m.rows[:w.plan.rows]):
            for ci, cell in enumerate(row.cells):
                col = m.columns[ci].cell
                if cell.is_note and cell.nid is not None and col >= w.first_cell:
                    x0, y0, x1, y1 = w.layout.cell_rect(ri, col, w.first_cell)
                    ed.press((x0 + x1) / 2, (y0 + y1) / 2, 0)
                    ed.release((x0 + x1) / 2, (y0 + y1) / 2, 0)
                    break
            else:
                continue
            break
        row, cell = min(10, w.plan.rows - 1), w.first_cell + 20
        if ed._note_at(row, cell) is None:
            x0, y0, x1, y1 = w.layout.cell_rect(row, cell, w.first_cell)
            ed.press((x0 + x1) / 2, (y0 + y1) / 2, 0)
            ed.move((x0 + x1) / 2 + 3 * w.layout.cell_w, (y0 + y1) / 2)
        w.set_hover(*w.layout.cell_rect(8, w.first_cell + 6, w.first_cell)[:2])

    def run(self, screenshot: str | None = None) -> int:
        self.build()
        shot_frames_left = 12 if screenshot else None
        while dpg.is_dearpygui_running():
            if shot_frames_left is not None:
                shot_frames_left -= 1
                if shot_frames_left == 6:
                    self._screenshot_pose()
                if shot_frames_left == 3:
                    dpg.output_frame_buffer(screenshot)
                if shot_frames_left == 0:
                    break
            self.frame()
            dpg.render_dearpygui_frame()
        self.editor.transport.stop()
        close = getattr(self.sink, "close", None)
        if callable(close):
            close()
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
           width: int = 1280, height: int = 760, screenshot: str | None = None,
           midi_out: str | None = None) -> int:
    """Open the window. Shared by ``seq gui`` and ``python -m midiseq.ui_gui``.
    ``midi_out`` names the output port (a substring, as ``open_sink`` takes
    it); None picks the default, and no port at all is silent, not fatal."""
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
                      middle_c_octave=middle_c_octave, width=width, height=height,
                      midi_out=midi_out)
    except ValueError as exc:
        print(f"seq gui: {exc}", file=sys.stderr)
        return 1
    if bar > 1:
        app.widget.go_to_bar(bar)
    return app.run(screenshot)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m midiseq.ui_gui",
                                 description="The grid window.")
    ap.add_argument("file", nargs="?", help="a .mid to open (default: an empty song)")
    ap.add_argument("-t", "--track", type=int, default=None)
    ap.add_argument("-z", "--zoom", type=int, default=16, choices=Zoom.SUPPORTED)
    ap.add_argument("--middle-c", choices=sorted(MIDDLE_C), default="c4")
    ap.add_argument("--no-ghosts", action="store_true")
    ap.add_argument("--bar", type=int, default=1, help="start with this bar at the left edge")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=760)
    ap.add_argument("--midi-out", metavar="NAME", default=None,
                    help="the MIDI output to play through (a substring of its name)")
    ap.add_argument("--screenshot", metavar="PNG", default=None,
                    help="paint once, write the frame buffer to PNG, quit (development)")
    args = ap.parse_args(argv)
    return launch(args.file, track=args.track, zoom=Zoom(args.zoom),
                  middle_c_octave=MIDDLE_C[args.middle_c], ghosts=not args.no_ghosts,
                  bar=args.bar, width=args.width, height=args.height,
                  screenshot=args.screenshot, midi_out=args.midi_out)


__all__ = ["GridApp", "launch", "main", "empty_song"]
