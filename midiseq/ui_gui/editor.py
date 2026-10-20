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
like a chord.

Step 5 gave the editor the transport proper: ``seek`` (a click in the
ruler), Space, Shift+Space and Home as keys, and ``frame()``, which the
window calls once a frame to learn where the playhead is and turn the page
when it runs off the right edge. ``transport_state()`` is what the transport
bar shows, as data, so the bar's labels are tested here and not in a window.
Editing while rolling is allowed and lands in the song; the schedule already
handed to the sink is not rewritten, so what you hear is the song as it was
when Play was pressed.

The menus and the palette are generated from ``core/edit.py``'s registry:
``menu_sections()`` and ``palette()`` are the two front doors, and
``PROMPTS`` says which commands need a parameter and what to ask, as data,
so the window builds one generic dialog for all of them.

The velocity lane is editable, as PLAN.md §8 asks - "an editable one is an
invitation". A left press in the lane starts a ``velocity`` gesture: every
column the pointer crosses takes the velocity its y means at that moment,
so a diagonal sweep draws a ramp, and Shift holds the press's value so a
sweep draws a flat line. The change lands on release as one
``SetVelocities``, one undo for the whole sweep; the bars preview it
meanwhile. Only the current track's notes *starting* in a column are
touched, and all of them - a chord's notes get one value, because that is
what the bar stands for. With a selection, Ctrl+Up/Down nudges by 5 and
Ctrl+Shift+Up/Down by 1.

The clipboard is a tuple of plain ``(pitch, offset, duration, velocity)``
tuples, offsets from the earliest start - not ``Note`` objects, so a paste
mints fresh ids and nothing can alias the original. Paste lands at the
playhead and selects what it added; Duplicate lands right after the
selection's own end; Cut is Copy then Delete. Each paste is one
``AddNotes``, one undo, auditioned as the phrase it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audio.sink_null import NullSink
from ..core import edit
from ..core.edit import REGISTRY, Category, Command, NoteSpec
from ..core.history import History
from ..core.model import DEFAULT_VELOCITY, Note, parse_pitch
from ..core.transport import Sink, Transport
from .grid import GridWidget
from .paint import Primitive, Rect, lane_bar, tick_rect
from ..ui_common import (  # noqa: F401 - re-exported: these were this module's names until the lift
    ACTIONS, GESTURE_ONLY, NOTE_NAMES, PROMPTS, VELOCITY_STEP, MenuEntry, _count, _entry, all_entries,
    bars_around, build_command, edit_menu, key_text, loop_text, menu_sections, palette, parse_key,
    pitch_class, velocity_change,
)

LEFT, RIGHT = 0, 1
EDGE_PX = 5                      # the resize grab zone at a note's right edge


@dataclass(frozen=True)
class TransportState:
    """What the transport bar shows. A snapshot, so the window can compare
    two and touch its widgets only when something changed."""

    rolling: bool
    play_label: str          # "Play" or "Stop"
    position: str            # bar:beat.sub, e.g. "9:2.1"
    tempo: str               # the tempo at the playhead, e.g. "96 bpm"
    audition: bool


def bpm_text(bpm: float) -> str:
    """``120.0`` -> ``"120 bpm"``, ``99.5`` -> ``"99.5 bpm"``. MIDI stores
    microseconds per beat, so a "120" file can come back 120.00002."""
    text = f"{bpm:.1f}".rstrip("0").rstrip(".")
    return f"{text} bpm"


# --------------------------------------------------------------------------
# Gestures
# --------------------------------------------------------------------------

@dataclass
class Gesture:
    kind: str                    # insert | move | resize | marquee | erase | velocity
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
    # The lane: the velocity at the press, the latest one, and what every
    # column crossed so far will be set to.
    vel0: int = 0
    vel: int = 0
    values: dict[int, int] = field(default_factory=dict)


class Editor:
    def __init__(self, widget: GridWidget, sink: Sink | None = None) -> None:
        self.widget = widget
        self.song = widget.song
        self.history = History(self.song)
        self.sink: Sink = sink or NullSink()
        self.transport = Transport(self.song, self.sink)
        self.selection = widget.selection          # one set, shared with the picture
        self.clipboard: tuple[NoteSpec, ...] = ()
        self.gesture: Gesture | None = None
        self.key_root, self.key_scale = 0, "major"   # for diatonic moves; C major until told
        self.scale_filter = False                    # K: shade the rows outside the key
        self.saved_depth = 0
        self.message = ""
        self.preview_dirty = False
        self.playhead_dirty = True
        self._playhead_shown: int | None = None
        self._follow_anchor: int | None = None    # first_cell as _follow() last left it
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

    def set_sink(self, sink: Sink) -> None:
        """Swap where the sound goes. Whatever is rolling carries on from
        where it is on the new sink: the old one is stopped first (it owns
        its notes' releases), then the new one is given the schedule from
        there. Switching to silent and back mid-song costs a lead, not the
        song."""
        t = self.transport
        was_rolling = t.rolling
        at = t.position()
        t.stop()
        self.sink = sink
        t.sink = sink
        if was_rolling:
            t.play(at)

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
        self.transport.resync()
        if audition:
            self.transport.audition(command.auditions())
        self.message = f"{command.label}: {_count(len(command.touched))}"
        return command

    def undo(self) -> str:
        c = self.history.undo()
        if c:
            self.transport.resync()
        self.message = f"Undid {c.label}" if c else "Nothing to undo"
        return self.message

    def redo(self) -> str:
        c = self.history.redo()
        if c:
            self.transport.resync()
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

    def _lane_raw(self, x: float, y: float) -> tuple[int, int]:
        """``(cell, velocity)`` for a lane drag, wherever the pointer is."""
        w = self.widget
        rows = w.plan.rows if w.plan is not None else 1
        return w.layout.lane_raw(x, y, w.first_cell, rows)

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

    def ruler_hit(self, x: float, y: float) -> int | None:
        """The tick of the cell under a point in the ruler, or None."""
        w = self.widget
        if w.plan is None:
            return None
        cell = w.layout.ruler_cell(x, y, w.first_cell, w.width)
        return None if cell is None else w.tick_at_col(cell)

    # -- the transport ----------------------------------------------------------

    def seek(self, tick: int) -> None:
        """Put the playhead at ``tick``; if rolling, carry on from there."""
        tick = max(0, tick)
        t = self.transport
        self._arm_follow()
        if t.rolling:
            t.play(tick)
        else:
            t.position()            # settles a schedule that ran out, which would overwrite us
            t.playhead = tick
        self.playhead_dirty = True

    def frame(self) -> int:
        """Once a frame, from the window. Reads the clock, turns the page
        when the playhead runs off the right edge, and notes whether its
        line needs repainting. Returns the playhead's tick."""
        t = self.transport
        if t.cycle() and not self.widget.tick_visible(t.loop[0]):
            self.widget.go_to_bar(self.widget.view.bars.bar_containing(t.loop[0]).number)
            self._arm_follow()
        tick = t.position()
        if t.rolling:
            self._follow(tick)
        if tick != self._playhead_shown:
            self._playhead_shown = tick
            self.playhead_dirty = True
        return tick

    def _follow(self, tick: int) -> None:
        """Turn the page for a rolling playhead - unless you scrolled away
        from it: a view moved by hand while rolling is yours until the
        playhead is back in it. Not during a drag either, or the drag's cells
        would shift under the pointer."""
        w = self.widget
        if w.first_cell != self._follow_anchor and not w.tick_visible(tick):
            return
        if self.gesture is None:
            w.follow(tick)
        self._follow_anchor = w.first_cell

    def _arm_follow(self) -> None:
        """Play, seek and rewind: wherever the view is now is where you want
        the playhead followed from."""
        self._follow_anchor = self.widget.first_cell

    def playhead_items(self) -> list[Primitive]:
        self.playhead_dirty = False
        tick = self.transport.position()
        self._playhead_shown = tick
        return self.widget.loop_items(self.transport.loop) + self.widget.playhead_items(tick)

    def transport_state(self) -> TransportState:
        t = self.transport
        tick = t.position()
        return TransportState(
            rolling=t.rolling,
            play_label="Stop" if t.rolling else "Play",
            position=self.widget.view.bars.position(tick),
            tempo=bpm_text(self.song.tempo_at(tick).bpm),
            audition=t.audition_enabled,
        )

    # -- the pointer ------------------------------------------------------------

    def press(self, x: float, y: float, button: int, *, shift: bool = False,
              ctrl: bool = False) -> None:
        if self.gesture is not None:
            return
        if button == LEFT:
            tick = self.ruler_hit(x, y)
            if tick is not None:
                self.seek(tick)
                return
            lane = self.widget.lane_hit(x, y)
            if lane is not None:
                # The lane. Ctrl means nothing here - velocity has no grid
                # to ignore. Shift holds the press's value: a flat line.
                col, vel = lane
                g = Gesture("velocity", button, x, y, 0, col, shift=shift,
                            x=x, y=y, col=col, vel0=vel, vel=vel)
                g.values[col] = vel
                self.gesture = g
                self.preview_dirty = True
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
        if g.kind == "velocity":
            col, vel = self._lane_raw(x, y)
            self._sweep(g, col, vel)
            g.x, g.y, g.col, g.vel = x, y, col, vel
            self.preview_dirty = True
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

    @staticmethod
    def _sweep(g: Gesture, col: int, vel: int) -> None:
        """Give every column between the last point and this one its
        velocity. Mouse events arrive a few pixels apart, so a quick sweep
        skips columns; the pointer still passed through them, and a ramp
        with holes in it is not a ramp, so the skipped ones take the value
        the straight line between the two points gives them. Shift makes
        every column the press's value instead."""
        c0, v0 = g.col, g.vel
        span = col - c0
        for c in range(min(c0, col), max(c0, col) + 1):
            if g.shift:
                v = g.vel0
            elif span == 0:
                v = vel
            else:
                v = round(v0 + (vel - v0) * (c - c0) / span)
            g.values[c] = v

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

        elif g.kind == "velocity":
            values = self._lane_values(g)
            if values:
                self.run(edit.SetVelocities(values))

    def _lane_values(self, g: Gesture) -> dict[int, int]:
        """The sweep as note ids: every note of the current track starting
        in a crossed column, a chord's notes alike, leaving out the ones
        already at that value so a click that changes nothing is no edit."""
        values: dict[int, int] = {}
        for col, v in g.values.items():
            for n in self.widget.notes_starting_in(col):
                if n.velocity != v:
                    values[n.nid] = v
        return values

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
            if self.transport.rolling:
                self.transport.stop()
                self.playhead_dirty = True
            if self.gesture is not None:
                self.cancel()
            elif self.selection:
                self.deselect()
            return True
        if name == "space":
            if shift:
                self.play_from_left_edge()
            else:
                self.toggle_play()
            self.playhead_dirty = True
            return True
        if name == "home":
            self.seek(0)
            self.widget.go_to_bar(1)
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
        if ctrl and name == "x":
            self.cut()
            return True
        if ctrl and name == "c":
            self.copy()
            return True
        if ctrl and name == "v":
            self.paste()
            return True
        if ctrl and name == "d":
            self.duplicate()
            return True
        if self.selection and ctrl and name in ("up", "down"):
            step = 1 if shift else VELOCITY_STEP
            self.nudge_velocity(step if name == "up" else -step)
            return True
        if name == "a" and not ctrl:
            self.toggle_audition()
            return True
        if name == "l" and not ctrl:
            self.toggle_loop()
            return True
        if name == "k" and not ctrl:
            self.toggle_scale_filter()
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

    def play_from_left_edge(self) -> bool:
        """Shift+Space: play what you are looking at."""
        w = self.widget
        self._arm_follow()
        return self.transport.play(w.view.cell_to_tick(w.first_cell))

    def toggle_play(self) -> bool:
        """Space and the Play button. Returns whether the transport rolls
        afterwards, and says so when there was nothing to play from here."""
        t = self.transport
        self._arm_follow()
        if t.rolling:
            t.stop()
            return False
        if not t.play():
            self.rewind()               # nothing ahead: from the top, as |< would (Matthew's call, 2026-10-19)
            if not t.play():
                self.message = "Nothing to play"
                return False
            self.message = "From the top"
        return True

    def set_key(self, text: str) -> str:
        """Key...: the key signature the diatonic moves, the snap and the
        scale filter use."""
        try:
            self.key_root, self.key_scale = parse_key(text)
        except ValueError as exc:
            self.message = f"Key: {exc}"
            return self.message
        self._apply_scale()
        self.message = f"Key {key_text(self.key_root, self.key_scale)}"
        return self.message

    def toggle_scale_filter(self) -> str:
        """K: shade the rows outside the key, or stop."""
        self.scale_filter = not self.scale_filter
        self._apply_scale()
        self.message = (f"Scale filter: {key_text(self.key_root, self.key_scale)}"
                        if self.scale_filter else "Scale filter off")
        return self.message

    def _apply_scale(self) -> None:
        self.widget.set_scale((self.key_root, self.key_scale) if self.scale_filter else None)

    def toggle_loop(self) -> str:
        """L: a loop over the selection's bars, else the playhead's bar; L
        again turns it off."""
        t = self.transport
        if t.loop is not None:
            t.set_loop(None)
            self.message = "Loop off"
        else:
            notes = self._selected_notes()
            lo = min(n.start for n in notes) if notes else t.position()
            hi = max(n.end for n in notes) if notes else lo
            t.set_loop(bars_around(self.widget.view.bars, lo, hi))
            self.message = f"Loop {loop_text(self.widget.view.bars, t.loop)}"
        self.playhead_dirty = True
        return self.message

    def rewind(self) -> None:
        """The |< button: bar 1, rolling or not, and the view goes with it."""
        self.widget.go_to_bar(1)
        self.seek(0)

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

    def nudge_velocity(self, delta: int) -> None:
        if self.selection:
            self.run(edit.SetVelocity(set(self.selection), delta=delta))

    # -- the clipboard ----------------------------------------------------------

    def _selected_notes(self) -> list[Note]:
        return sorted((n for t in self.song.tracks for n in t.notes if n.nid in self.selection),
                      key=lambda n: (n.start, n.pitch))

    @staticmethod
    def _specs(notes: list[Note]) -> tuple[NoteSpec, ...]:
        """Notes as clipboard entries: offsets from the earliest start,
        pitches absolute, no ids."""
        if not notes:
            return ()
        origin = min(n.start for n in notes)
        return tuple((n.pitch, n.start - origin, n.duration, n.velocity) for n in notes)

    def _place(self, specs: tuple[NoteSpec, ...], at: int) -> Command:
        """Add ``specs`` to the current track with their origin at ``at``,
        and make the new notes the selection - so a paste can be nudged,
        transposed or pasted again straight away."""
        command = self.run(edit.AddNotes(
            self.track, [(pitch, at + offset, duration, velocity)
                         for pitch, offset, duration, velocity in specs]))
        self.selection.clear()
        self.selection.update(command.touched)
        self._changed()
        return command

    def copy(self) -> str:
        notes = self._selected_notes()
        if not notes:
            self.message = "Copy: nothing selected"
            return self.message
        self.clipboard = self._specs(notes)
        self.message = f"Copied {_count(len(notes))}"
        return self.message

    def cut(self) -> str:
        notes = self._selected_notes()
        if not notes:
            self.message = "Cut: nothing selected"
            return self.message
        self.clipboard = self._specs(notes)
        self.run(edit.DeleteNotes({n.nid for n in notes}), audition=False)
        self.message = f"Cut {_count(len(notes))}"
        return self.message

    def paste(self) -> str:
        """At the playhead - the one position that is always on screen and
        always yours to set (a click in the ruler), rolling or not."""
        if not self.clipboard:
            self.message = "Paste: clipboard is empty"
            return self.message
        at = self.transport.position()
        self._place(self.clipboard, at)
        self.message = f"Pasted {_count(len(self.clipboard))} at {self.widget.view.bars.position(at)}"
        return self.message

    def duplicate(self) -> str:
        """The selection again, starting where it ends. The clipboard is
        left alone: duplicating a bar shouldn't lose what you copied."""
        notes = self._selected_notes()
        if not notes:
            self.message = "Duplicate: nothing selected"
            return self.message
        at = max(n.end for n in notes)
        self._place(self._specs(notes), at)
        self.message = f"Duplicated {_count(len(notes))} at {self.widget.view.bars.position(at)}"
        return self.message

    # -- the registry's door ------------------------------------------------------

    def build(self, key: str, raw: dict[str, str] | None = None) -> Command:
        """Make the command for a registry key from the prompt's raw strings,
        remembering a key signature the prompt set. Raises ``ValueError``
        with a sentence when a value doesn't parse."""
        command, (self.key_root, self.key_scale) = build_command(
            key, raw or {}, self.selection, tpc=self._tpc,
            middle_c_octave=self.widget.middle_c_octave,
            key_root=self.key_root, key_scale=self.key_scale)
        self._apply_scale()
        return command

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
        if key == "view.key":
            return self.set_key((raw or {}).get("key", ""))
        if key == "view.scale_filter":
            return self.toggle_scale_filter()
        if key == "edit.cut":
            return self.cut()
        if key == "edit.copy":
            return self.copy()
        if key == "edit.paste":
            return self.paste()
        if key == "edit.duplicate":
            return self.duplicate()
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
        elif g.kind == "velocity":
            # A bar where the sweep will put one; nothing over a column with
            # no note to set, because nothing will happen there.
            for col, v in sorted(g.values.items()):
                if w.notes_starting_in(col):
                    items.append(lane_bar(w.layout, w.first_cell, w.plan.rows, col, v, "lane_preview"))
        return items

    def status_tail(self) -> str:
        parts = []
        if self.selection:
            parts.append(f"sel {len(self.selection)}")
        if not self.transport.audition_enabled:
            parts.append("audition off")
        if self.transport.loop is not None:
            parts.append(f"loop {loop_text(self.widget.view.bars, self.transport.loop)}")
        if self.scale_filter:
            parts.append(f"scale {key_text(self.key_root, self.key_scale)}")
        if self.history.can_undo:
            parts.append(f"undo: {self.history.undo_label}")
        if self.message:
            parts.append(self.message)
        return "  ".join(parts)

    def status_line(self) -> str:
        """The whole status line: ``> 9:2.1`` first while rolling, so it
        reads as playing, then the widget's hover and the editor's tail."""
        line = self.widget.status()
        tail = self.status_tail()
        if tail:
            line = f"{line}  {tail}"
        t = self.transport
        if t.rolling:
            line = f"> {self.widget.view.bars.position(t.position())}  {line}"
        return line


__all__ = ["Editor", "Gesture", "MenuEntry", "TransportState", "PROMPTS", "ACTIONS",
           "GESTURE_ONLY", "menu_sections", "edit_menu", "all_entries", "palette",
           "pitch_class", "parse_key", "velocity_change", "bpm_text",
           "LEFT", "RIGHT", "EDGE_PX", "VELOCITY_STEP"]
