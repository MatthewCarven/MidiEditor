"""The editing layer: PLAN.md §11's mouse model, the selection, undo and
audition - with no toolkit in sight.

    on a note                    on empty space
  click      select + play     click      insert + play
  drag       move it           drag       insert, drag out length
  edge drag  resize            shift+drag marquee select
  shift+clk  add to set        right      (nothing)
  right      delete            ctrl+drag  ignore snap
  right+drag erase sweep
  ctrl+drag  ignore snap

No tool modes. Esc deselects - clicking empty space can't, because it
inserts. Right-click-delete has no confirmation because undo is load-bearing:
every edit here is a command object run through ``History``. The one line
not in the plan's table is the edge drag, which resizes; ``ResizeNotes`` had
been waiting for a gesture since step 1.

Pointer events arrive in drawlist pixels; the window translates from
DearPyGui and does nothing else. Keys arrive as names (``"up"``,
``"delete"``, ``"z"``) with modifier flags. Everything here is tested by
poking it with coordinates.

Audition goes through ``Transport.audition``, so its guards apply - the
toggle, and never while the transport rolls - and dropping a chord sounds
like a chord. Sound itself is step 5; until then the null sink keeps a log.

The menus and the palette are generated from ``core/edit.py``'s registry:
``menu_sections()`` and ``palette()`` are the two front doors, and
``PROMPTS`` says which commands need a parameter and what to ask, as data,
so the window builds one generic dialog for all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audio.sink_null import NullSink
from ..core import edit
from ..core.edit import REGISTRY, Category, Command
from ..core.history import History
from ..core.model import DEFAULT_VELOCITY, Note, parse_pitch
from ..core.transport import Sink, Transport
from .grid import GridWidget
from .paint import Primitive, Rect, tick_rect

LEFT, RIGHT = 0, 1
EDGE_PX = 5                      # the resize grab zone at a note's right edge

#: Registry entries that only make sense as gestures, never as menu items.
GESTURE_ONLY = {"note.add", "note.move", "note.resize"}

#: Editor-level actions that live beside the registry in the palette and the
#: Edit menu. They aren't commands (nothing to undo), so they aren't in the
#: registry; the editor runs them by key like everything else.
ACTIONS = [
    ("edit.undo", "Undo", "Ctrl+Z"),
    ("edit.redo", "Redo", "Ctrl+Y"),
    ("edit.select_all", "Select all", "Ctrl+A"),
    ("edit.deselect", "Deselect", "Esc"),
    ("edit.audition", "Audition on edit (toggle)", "A"),
]

#: What to ask before running a command that takes a parameter: a list of
#: (field, caption, default). Values come back as strings and are parsed in
#: ``Editor.build``. Commands not listed run as they are.
PROMPTS: dict[str, list[tuple[str, str, str]]] = {
    "xform.transpose.chromatic": [("semitones", "Semitones (+ up, - down)", "1")],
    "xform.transpose.diatonic": [("degrees", "Scale degrees (+ up, - down)", "1"),
                                 ("key", "Key (C major, A minor, D harmonic minor)", "C major")],
    "xform.invert": [("axis", "Axis note, e.g. G4 (blank: the midpoint)", "")],
    "xform.quantise": [("strength", "Strength, percent", "100"),
                       ("ends", "Note ends too? y/n", "n")],
    "xform.snap_to_scale": [("key", "Key (C major, A minor, D harmonic minor)", "C major")],
    "note.velocity": [("velocity", "Velocity: 96, +10, -10 or x0.8", "+10")],
}

NOTE_NAMES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def pitch_class(text: str) -> int:
    """``"C"`` -> 0, ``"F#"`` -> 6, ``"Bb"`` -> 10. Octave digits are ignored."""
    t = text.strip().lower()
    if not t or t[0] not in NOTE_NAMES:
        raise ValueError(f"not a note name: {text!r}")
    pc = NOTE_NAMES[t[0]]
    for ch in t[1:]:
        if ch == "#":
            pc += 1
        elif ch == "b":
            pc -= 1
        elif ch.isdigit() or ch == "-":
            break
        else:
            raise ValueError(f"not a note name: {text!r}")
    return pc % 12


def parse_key(text: str) -> tuple[int, str]:
    """``"A minor"`` -> (9, "minor"). The scale names are ``edit.SCALES``."""
    parts = text.strip().split(None, 1)
    if not parts:
        raise ValueError("a key looks like 'C major' or 'A minor'")
    root = pitch_class(parts[0])
    scale = parts[1].strip().lower() if len(parts) > 1 else "major"
    if scale not in edit.SCALES:
        raise ValueError(f"unknown scale {scale!r}; one of {', '.join(edit.SCALES)}")
    return root, scale


def velocity_change(text: str) -> dict:
    """``"96"`` sets, ``"+10"`` / ``"-10"`` nudge, ``"x0.8"`` scales."""
    s = text.strip().lower().replace(" ", "")
    if not s:
        raise ValueError("say a velocity: 96, +10, -10 or x0.8")
    if s[0] in "x*":
        return {"scale": float(s[1:])}
    if s[0] in "+-":
        return {"delta": int(s)}
    return {"velocity": int(s)}


@dataclass(frozen=True)
class MenuEntry:
    key: str
    label: str
    shortcut: str | None
    needs_prompt: bool


def _entry(key: str, label: str, shortcut: str | None) -> MenuEntry:
    return MenuEntry(key, label, shortcut, key in PROMPTS)


def menu_sections() -> list[tuple[str, list[MenuEntry]]]:
    """The Notes menu, grouped so the separator teaches the taxonomy."""
    return [
        (name, [_entry(s.key, s.label, s.shortcut) for s in specs if s.key not in GESTURE_ONLY])
        for name, specs in edit.notes_menu()
    ]


def edit_menu() -> list[MenuEntry]:
    """The Edit menu: the editor's own actions, then the registry's EDIT
    commands that aren't gestures (Delete)."""
    entries = [_entry(k, label, sc) for k, label, sc in ACTIONS]
    entries += [_entry(s.key, s.label, s.shortcut) for s in edit.menu_model(Category.EDIT)
                if s.key not in GESTURE_ONLY]
    return entries


def all_entries() -> list[MenuEntry]:
    seen: set[str] = set()
    out: list[MenuEntry] = []
    for e in edit_menu() + [e for _, group in menu_sections() for e in group]:
        if e.key not in seen:
            seen.add(e.key)
            out.append(e)
    return out


def palette(query: str) -> list[MenuEntry]:
    """Type "inv", get Invert. Substring matches first, then words whose
    letters appear in order, so "tsd" still finds "Transpose - scale degrees"."""
    q = query.strip().lower()
    entries = all_entries()
    if not q:
        return entries
    def subsequence(needle: str, hay: str) -> bool:
        it = iter(hay)
        return all(ch in it for ch in needle)
    direct = [e for e in entries if q in e.label.lower()]
    loose = [e for e in entries if e not in direct and subsequence(q, e.label.lower())]
    return direct + loose


# --------------------------------------------------------------------------
# Gestures
# --------------------------------------------------------------------------

@dataclass
class Gesture:
    kind: str                    # insert | move | resize | marquee | erase
    button: int
    x0: float
    y0: float
    row0: int
    col0: int
    ctrl: bool = False
    shift: bool = False
    nids: set[int] = field(default_factory=set)
    anchor: Note | None = None   # the note under the press, for move/resize
    x: float = 0.0
    y: float = 0.0
    row: int = 0
    col: int = 0
    swept: set[int] = field(default_factory=set)
    moved: bool = False          # left the press cell at some point


class Editor:
    def __init__(self, widget: GridWidget, sink: Sink | None = None) -> None:
        self.widget = widget
        self.song = widget.song
        self.history = History(self.song)
        self.sink: Sink = sink or NullSink()
        self.transport = Transport(self.song, self.sink)
        self.selection = widget.selection          # one set, shared with the picture
        self.gesture: Gesture | None = None
        self.key_root, self.key_scale = 0, "major"   # for diatonic moves; C major until told
        self.saved_depth = 0
        self.message = ""
        self.preview_dirty = False
        self.history.on_change.append(self._after_history)

    # -- state ------------------------------------------------------------------

    @property
    def dirty_since_save(self) -> bool:
        return self.history.depth != self.saved_depth

    def mark_saved(self) -> None:
        self.saved_depth = self.history.depth

    @property
    def track(self) -> int:
        return self.widget.track

    def _after_history(self, command: Command | None) -> None:
        self._prune_selection()
        self.widget.dirty = True

    def _prune_selection(self) -> None:
        if not self.selection:
            return
        alive = {n.nid for t in self.song.tracks for n in t.notes}
        self.selection &= alive

    def _changed(self) -> None:
        self.widget.dirty = True

    # -- running commands -------------------------------------------------------

    def run(self, command: Command, *, audition: bool = True) -> Command:
        self.history.run(command)
        if audition:
            self.transport.audition(command.auditions())
        self.message = f"{command.label}: {len(command.touched)} note{'s' if len(command.touched) != 1 else ''}"
        return command

    def undo(self) -> str:
        c = self.history.undo()
        self.message = f"Undid {c.label}" if c else "Nothing to undo"
        return self.message

    def redo(self) -> str:
        c = self.history.redo()
        self.message = f"Redid {c.label}" if c else "Nothing to redo"
        return self.message

    # -- geometry ---------------------------------------------------------------

    @property
    def _tpc(self) -> int:
        return self.widget.view.ticks_per_cell

    def _hit(self, x: float, y: float) -> tuple[int, int] | None:
        w = self.widget
        if w.plan is None:
            return None
        return w.layout.hit_test(x, y, w.first_cell, w.plan.rows, w.width)

    def _raw(self, x: float, y: float) -> tuple[int, int]:
        w = self.widget
        rows = w.plan.rows if w.plan is not None else 1
        return w.layout.raw_cell(x, y, w.first_cell, rows)

    def _note_at(self, row: int, col: int) -> Note | None:
        info = self.widget.cell_info(row, col)
        if info is None:
            return None
        _, cell = info
        if cell.nid is None or cell.is_ghost:
            return None
        return self.song.tracks[self.track].note_by_id(cell.nid)

    def _exact_tick(self, x: float) -> int:
        """The tick under a pixel, ignoring the grid (Ctrl)."""
        w = self.widget
        return max(0, round(w.first_cell * self._tpc + (x - w.layout.gutter_w) / w.layout.cell_w * self._tpc))

    def _ticks_from_px(self, dx: float) -> int:
        return round(dx / self.widget.layout.cell_w * self._tpc)

    def _note_end_px(self, note: Note) -> float:
        """Where a note's rectangle ends on screen."""
        w = self.widget
        x0, _, x1, _ = tick_rect(w.layout, w.first_cell, self._tpc, 0, note.start, note.end)
        return x1

    # -- the pointer ------------------------------------------------------------

    def press(self, x: float, y: float, button: int, *, shift: bool = False,
              ctrl: bool = False) -> None:
        if self.gesture is not None:
            return
        hit = self._hit(x, y)
        if hit is None:
            return
        row, col = hit
        note = self._note_at(row, col)

        if button == RIGHT:
            # Right on a note deletes; right-drag sweeps. One command on
            # release, so a sweep is one undo.
            g = Gesture("erase", button, x, y, row, col, x=x, y=y, row=row, col=col)
            if note is not None:
                g.swept.add(note.nid)
            self.gesture = g
            self.preview_dirty = True
            return
        if button != LEFT:
            return

        if note is None:
            kind = "marquee" if shift else "insert"
            self.gesture = Gesture(kind, button, x, y, row, col, ctrl=ctrl, shift=shift,
                                   x=x, y=y, row=row, col=col)
            self.preview_dirty = True
            return

        # On a note.
        if shift:
            if note.nid in self.selection:
                self.selection.discard(note.nid)
            else:
                self.selection.add(note.nid)
            self.transport.audition([note])
            self._changed()
            return
        if note.nid not in self.selection:
            self.selection.clear()
            self.selection.add(note.nid)
            self._changed()
        self.transport.audition([note])
        w = self.widget
        end_px = self._note_end_px(note)
        start_px = tick_rect(w.layout, w.first_cell, self._tpc, 0, note.start, note.end)[0]
        kind = "resize" if (x >= end_px - EDGE_PX and end_px - start_px > 2 * EDGE_PX) else "move"
        self.gesture = Gesture(kind, button, x, y, row, col, ctrl=ctrl, shift=shift,
                               nids=set(self.selection), anchor=note, x=x, y=y, row=row, col=col)
        self.preview_dirty = True

    def move(self, x: float, y: float, *, shift: bool = False, ctrl: bool = False) -> None:
        g = self.gesture
        if g is None:
            return
        row, col = self._raw(x, y)
        g.x, g.y, g.row, g.col, g.ctrl = x, y, row, col, ctrl
        if (row, col) != (g.row0, g.col0):
            g.moved = True
        if g.kind == "erase":
            note = self._note_at(row, col)
            if note is not None:
                g.swept.add(note.nid)
        self.preview_dirty = True

    def release(self, x: float, y: float, button: int, *, shift: bool = False,
                ctrl: bool = False) -> None:
        g = self.gesture
        if g is None or button != g.button:
            return
        self.move(x, y, shift=shift, ctrl=ctrl)
        self.gesture = None
        self.preview_dirty = True
        tpc = self._tpc

        if g.kind == "insert":
            pitch = self.widget.pitch_at_row(g.row0)
            if pitch is None:
                return
            c0, c1 = sorted((g.col0, g.col))
            start = self._exact_tick(g.x0) if (g.ctrl and g.col >= g.col0) else self.widget.tick_at_col(c0)
            duration = (c1 - c0 + 1) * tpc
            self.run(edit.AddNote(self.track, pitch, start, duration, DEFAULT_VELOCITY))

        elif g.kind == "move":
            d_pitch = g.row0 - g.row
            d_ticks = self._ticks_from_px(g.x - g.x0) if g.ctrl else (g.col - g.col0) * tpc
            if g.anchor is not None and g.anchor.start + d_ticks < 0:
                d_ticks = -g.anchor.start
            if d_pitch or d_ticks:
                self.run(edit.MoveNotes(g.nids, d_pitch, d_ticks))

        elif g.kind == "resize" and g.anchor is not None:
            if g.ctrl:
                end = self._exact_tick(g.x)
            else:
                end = (max(g.col, self.widget.view.tick_to_cell(g.anchor.start)) + 1) * tpc
            delta = max(1, end - g.anchor.start) - g.anchor.duration
            if delta:
                self.run(edit.ResizeNotes(g.nids, d_ticks=delta))

        elif g.kind == "marquee":
            hits = self._notes_in_box(g.row0, g.col0, g.row, g.col)
            if hits:
                self.selection |= hits
                self._changed()

        elif g.kind == "erase":
            if g.swept:
                self.selection -= g.swept
                self.run(edit.DeleteNotes(g.swept), audition=False)

    def cancel(self) -> None:
        self.gesture = None
        self.preview_dirty = True

    def _notes_in_box(self, r0: int, c0: int, r1: int, c1: int) -> set[int]:
        rows = range(min(r0, r1), max(r0, r1) + 1)
        cols = range(min(c0, c1), max(c0, c1) + 1)
        out: set[int] = set()
        for row in rows:
            for col in cols:
                info = self.widget.cell_info(row, col)
                if info is not None and info[1].nid is not None and not info[1].is_ghost:
                    out.add(info[1].nid)
        return out

    # -- keys -------------------------------------------------------------------

    def key(self, name: str, *, shift: bool = False, ctrl: bool = False) -> bool:
        """Returns whether the key was the editor's to handle. Unhandled keys
        fall through to navigation in the window."""
        if name == "escape":
            if self.gesture is not None:
                self.cancel()
            elif self.selection:
                self.deselect()
            return True
        if name in ("delete", "backspace"):
            self.delete_selection()
            return True
        if ctrl and name == "z":
            self.redo() if shift else self.undo()
            return True
        if ctrl and name == "y":
            self.redo()
            return True
        if ctrl and name == "a":
            self.select_all()
            return True
        if name == "a" and not ctrl:
            self.toggle_audition()
            return True
        if name == "q" and not ctrl:
            self.quantise_to_grid()
            return True
        if self.selection and not ctrl and name in ("up", "down", "left", "right"):
            if name in ("up", "down"):
                step = 1 if name == "up" else -1
                self.transpose_diatonic(step) if shift else self.transpose(step)
            else:
                self.nudge(1 if name == "right" else -1)
            return True
        return False

    # -- actions ----------------------------------------------------------------

    def select_all(self) -> None:
        self.selection.clear()
        self.selection.update(n.nid for n in self.song.tracks[self.track].notes)
        self.message = f"Selected {len(self.selection)} notes"
        self._changed()

    def deselect(self) -> None:
        self.selection.clear()
        self.message = ""
        self._changed()

    def delete_selection(self) -> None:
        if self.selection:
            self.run(edit.DeleteNotes(set(self.selection)), audition=False)

    def toggle_audition(self) -> bool:
        self.transport.audition_enabled = not self.transport.audition_enabled
        self.message = f"Audition {'on' if self.transport.audition_enabled else 'off'}"
        return self.transport.audition_enabled

    def transpose(self, semitones: int) -> None:
        if self.selection:
            self.run(edit.TransposeChromatic(set(self.selection), semitones))

    def transpose_diatonic(self, degrees: int) -> None:
        if self.selection:
            self.run(edit.TransposeDiatonic(set(self.selection), degrees,
                                            root=self.key_root, scale=self.key_scale))

    def nudge(self, cells: int) -> None:
        if self.selection:
            self.run(edit.MoveNotes(set(self.selection), 0, cells * self._tpc))

    def quantise_to_grid(self) -> None:
        if self.selection:
            self.run(edit.Quantise(set(self.selection), self._tpc))

    # -- the registry's door ------------------------------------------------------

    def build(self, key: str, raw: dict[str, str] | None = None) -> Command:
        """Make the command for a registry key from the prompt's raw strings.
        Raises ``ValueError`` with a sentence when a value doesn't parse."""
        raw = raw or {}
        sel = set(self.selection)
        if key == "note.delete":
            return edit.DeleteNotes(sel)
        if key == "note.velocity":
            return edit.SetVelocity(sel, **velocity_change(raw.get("velocity", "+10")))
        if key == "xform.transpose.chromatic":
            return edit.TransposeChromatic(sel, int(raw.get("semitones", "1")))
        if key == "xform.transpose.diatonic":
            if raw.get("key", "").strip():
                self.key_root, self.key_scale = parse_key(raw["key"])
            return edit.TransposeDiatonic(sel, int(raw.get("degrees", "1")),
                                          root=self.key_root, scale=self.key_scale)
        if key == "xform.invert":
            axis_text = raw.get("axis", "").strip()
            axis = parse_pitch(axis_text, self.widget.middle_c_octave) if axis_text else None
            return edit.Invert(sel, axis)
        if key == "xform.retrograde":
            return edit.Retrograde(sel)
        if key == "xform.augment":
            return edit.Augment(sel)
        if key == "xform.diminish":
            return edit.Diminish(sel)
        if key == "xform.quantise":
            strength = float(raw.get("strength", "100")) / 100.0
            ends = raw.get("ends", "n").strip().lower().startswith("y")
            return edit.Quantise(sel, self._tpc, strength=strength, ends=ends)
        if key == "xform.snap_to_scale":
            if raw.get("key", "").strip():
                self.key_root, self.key_scale = parse_key(raw["key"])
            return edit.SnapToScale(sel, root=self.key_root, scale=self.key_scale)
        raise KeyError(key)

    def run_key(self, key: str, raw: dict[str, str] | None = None) -> str:
        """Run a menu or palette entry by its key. Returns the status message."""
        if key == "edit.undo":
            return self.undo()
        if key == "edit.redo":
            return self.redo()
        if key == "edit.select_all":
            self.select_all()
            return self.message
        if key == "edit.deselect":
            self.deselect()
            return "Deselected"
        if key == "edit.audition":
            self.toggle_audition()
            return self.message
        if key not in REGISTRY:
            raise KeyError(key)
        if not self.selection:
            self.message = f"{REGISTRY[key].label.rstrip('.')}: nothing selected"
            return self.message
        try:
            command = self.build(key, raw)
        except ValueError as exc:
            self.message = f"{REGISTRY[key].label.rstrip('.')}: {exc}"
            return self.message
        self.run(command)
        return self.message

    # -- the picture ------------------------------------------------------------

    def preview_items(self) -> list[Primitive]:
        """What the gesture in progress will do, drawn over the grid."""
        self.preview_dirty = False
        g = self.gesture
        w = self.widget
        if g is None or w.plan is None:
            return []
        tpc = self._tpc
        items: list[Primitive] = []

        def note_box(pitch: int, start: int, end: int, role: str, filled: bool = True) -> None:
            row = w.row_of_pitch(pitch)
            if row is None:
                return
            x0, y0, x1, y1 = tick_rect(w.layout, w.first_cell, tpc, row, start, end)
            items.append(Rect(x0, y0, x1, y1, role, filled=filled))

        if g.kind == "insert":
            pitch = w.pitch_at_row(g.row0)
            if pitch is not None:
                c0, c1 = sorted((g.col0, g.col))
                start = self._exact_tick(g.x0) if (g.ctrl and g.col >= g.col0) else w.tick_at_col(c0)
                note_box(pitch, start, start + (c1 - c0 + 1) * tpc, "preview")
        elif g.kind == "move":
            d_pitch = g.row0 - g.row
            d_ticks = self._ticks_from_px(g.x - g.x0) if g.ctrl else (g.col - g.col0) * tpc
            track = self.song.tracks[self.track]
            for nid in g.nids:
                n = track.note_by_id(nid)
                if n is not None:
                    note_box(n.pitch + d_pitch, max(0, n.start + d_ticks),
                             max(0, n.start + d_ticks) + n.duration, "preview")
        elif g.kind == "resize" and g.anchor is not None:
            if g.ctrl:
                end = self._exact_tick(g.x)
            else:
                end = (max(g.col, w.view.tick_to_cell(g.anchor.start)) + 1) * tpc
            end = max(g.anchor.start + 1, end)
            delta = end - g.anchor.end
            track = self.song.tracks[self.track]
            for nid in g.nids:
                n = track.note_by_id(nid)
                if n is not None:
                    note_box(n.pitch, n.start, max(n.start + 1, n.end + delta), "preview")
        elif g.kind == "marquee":
            x0, x1 = sorted((g.x0, g.x))
            y0, y1 = sorted((g.y0, g.y))
            items.append(Rect(x0, y0, x1, y1, "marquee", filled=False))
        elif g.kind == "erase":
            track = self.song.tracks[self.track]
            for nid in g.swept:
                n = track.note_by_id(nid)
                if n is not None:
                    note_box(n.pitch, n.start, n.end, "erase", filled=False)
        return items

    def status_tail(self) -> str:
        parts = []
        if self.selection:
            parts.append(f"sel {len(self.selection)}")
        if not self.transport.audition_enabled:
            parts.append("audition off")
        if self.history.can_undo:
            parts.append(f"undo: {self.history.undo_label}")
        if self.message:
            parts.append(self.message)
        return "  ".join(parts)


__all__ = ["Editor", "Gesture", "MenuEntry", "PROMPTS", "ACTIONS", "menu_sections",
           "edit_menu", "all_entries", "palette", "pitch_class", "parse_key",
           "velocity_change", "LEFT", "RIGHT", "EDGE_PX"]
