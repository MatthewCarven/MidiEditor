"""The keyboard model: key names in, command objects out, no toolkit.

The window's mouse model (``ui_gui/editor.py``) at a cursor. What a click
does there, Enter does here, and it depends only on what is under the
cursor - no tool modes. The whole table is ``KEY_HELP`` below, which ``?``
shows in the terminal and the README quotes.

Everything that acts on notes acts on **the selection, or else the note
under the cursor**: Del, the transposes and nudges, Q, the velocity
nudges, ``+`` and ``-``, and every registry command the ``:`` palette
runs. So a single note never needs selecting first, and a selection
(Enter on notes, S to add, Ctrl+A for the track) makes the same keys act
on many. Esc deselects, or stops the transport if it is rolling.

The arrows always move the cursor - a keyboard UI has nothing else to move
it with - so the window's selection-moving arrows wear Shift here, and
the cursor follows a note it was on when the note moves.

**A chord the terminal can't deliver gets a plain spelling.** Windows
Terminal sends Shift+Enter as Enter and Shift+Space as Space (Matthew,
2026-10-19), Ctrl+Shift+S as Ctrl+S and Ctrl+Shift+Z as Ctrl+Z, and keeps
Ctrl+V and Ctrl+Shift+Up/Down for its own paste and scrolling. So S adds
to the set, Shift+P plays from the cursor, X / C / V / D are the
clipboard, ``[`` and ``]`` move a scale degree, Ctrl+Y is redo, and Save
as lives in the palette. The chords still work as aliases where a
terminal does deliver them.

Every edit is a command object run through ``History``; audition goes
through ``Transport.audition`` so its guards apply - the toggle, and never
while rolling. ``frame()`` is what the terminal calls on a timer to learn
where the playhead is and turn the page when it runs off the right edge,
the window's ``frame()`` without the pixels.
"""

from __future__ import annotations

from pathlib import Path

from ..audio.sink_null import NullSink
from ..core import edit, io_midi
from ..core.edit import REGISTRY, Command, NoteSpec
from ..core.history import History
from ..core.model import DEFAULT_VELOCITY, Note, parse_pitch
from ..core.transport import Sink, Transport
from ..ui_gui.editor import parse_key, velocity_change
from .palette import Palette, prompts_for, tui_entries
from .view import TextView

VELOCITY_STEP = 5                # Ctrl+Up/Down

#: What ``?`` shows. Plain letters where the window has a chord, because a
#: terminal only promises to deliver characters: see the module docstring.
KEY_HELP = [
    "    on a note                          on empty space",
    "  Enter   select + play                Enter   insert one cell + play",
    "  S       add to / drop from the set",
    "  Del     delete",
    "",
    "arrows            cursor a cell / a pitch      Shift+Left/Right  nudge a cell",
    "Ctrl+Left/Right   cursor a bar                 Shift+Up/Down     transpose a semitone",
    "PageUp/PageDown   cursor an octave             [ / ]             down / up a scale degree",
    "Home / End        bar 1 (and rewind) / last    Ctrl+Up/Down      velocity by five",
    "Tab / Shift+Tab   next / previous track        + / -             a cell longer / shorter",
    ". / ,             finer / coarser grid         Q                 quantise to the grid",
    "G / F             ghosts on/off / fit pitches  Ctrl+A / Esc      select the track / deselect",
    "Space             play / stop                  Ctrl+Z / Ctrl+Y   undo / redo",
    "Shift+P           play from the cursor         X / C / V / D     cut, copy, paste at the cursor, duplicate",
    "P                 playhead to the cursor       Ctrl+S            save",
    "A                 audition on/off              Ctrl+Q            quit (twice, with unsaved changes)",
    ":                 the command palette          ?                 this table",
]

_MODIFIER_ORDER = {"ctrl": 0, "alt": 1, "shift": 2}


def normalise_key(key: str, character: str | None = None) -> str:
    """Textual's names, made uniform: modifiers in ``ctrl+shift+`` order, a
    shifted letter (which arrives as its capital) as ``shift+<letter>``."""
    if len(key) == 1 and key.isalpha() and key.isupper():
        return "shift+" + key.lower()
    parts = key.split("+")
    if len(parts) > 1 and all(parts[:-1]):
        mods = sorted(parts[:-1], key=lambda m: _MODIFIER_ORDER.get(m, 9))
        return "+".join(mods + [parts[-1]])
    return key


def _count(n: int, word: str = "note") -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def build_command(key: str, raw: dict[str, str], nids: set[int], *, tpc: int,
                  middle_c_octave: int, key_root: int = 0, key_scale: str = "major",
                  ) -> tuple[Command, tuple[int, str]]:
    """The window's ``Editor.build``, as a function: a registry key and the
    prompt's raw strings to a command, plus the key signature to remember
    (the diatonic moves and the snap may set it). Raises ``ValueError``
    with a sentence when a value doesn't parse."""
    sel = set(nids)
    keysig = (key_root, key_scale)
    if key == "note.delete":
        return edit.DeleteNotes(sel), keysig
    if key == "note.velocity":
        return edit.SetVelocity(sel, **velocity_change(raw.get("velocity", "+10"))), keysig
    if key == "xform.transpose.chromatic":
        return edit.TransposeChromatic(sel, int(raw.get("semitones", "1"))), keysig
    if key == "xform.transpose.diatonic":
        if raw.get("key", "").strip():
            keysig = parse_key(raw["key"])
        return edit.TransposeDiatonic(sel, int(raw.get("degrees", "1")),
                                      root=keysig[0], scale=keysig[1]), keysig
    if key == "xform.invert":
        axis_text = raw.get("axis", "").strip()
        axis = parse_pitch(axis_text, middle_c_octave) if axis_text else None
        return edit.Invert(sel, axis), keysig
    if key == "xform.retrograde":
        return edit.Retrograde(sel), keysig
    if key == "xform.augment":
        return edit.Augment(sel), keysig
    if key == "xform.diminish":
        return edit.Diminish(sel), keysig
    if key == "xform.quantise":
        strength = float(raw.get("strength", "100")) / 100.0
        ends = raw.get("ends", "n").strip().lower().startswith("y")
        return edit.Quantise(sel, tpc, strength=strength, ends=ends), keysig
    if key == "xform.snap_to_scale":
        if raw.get("key", "").strip():
            keysig = parse_key(raw["key"])
        return edit.SnapToScale(sel, root=keysig[0], scale=keysig[1]), keysig
    raise KeyError(key)


class Editor:
    def __init__(self, grid: TextView, sink: Sink | None = None, *,
                 path: Path | None = None, sink_note: str = "") -> None:
        self.grid = grid
        self.song = grid.song
        self.history = History(self.song)
        self.sink: Sink = sink or NullSink()
        self.sink_note = sink_note
        self.transport = Transport(self.song, self.sink)
        self.selection = grid.selection          # one set, shared with the picture
        self.clipboard: tuple[NoteSpec, ...] = ()
        self.palette = Palette(tui_entries, prompts_for)
        self.path = path
        self.key_root, self.key_scale = 0, "major"   # for diatonic moves; C major until told
        self.saved_depth = 0
        self.message = ""
        self.quit_armed = False
        self.want_quit = False
        self.help_open = False
        self._playhead_shown: int | None = None
        self._follow_anchor: int | None = None    # first_cell as _follow() last left it
        self.history.on_change.append(self._after_history)
        self.grid.playhead = 0

    # -- state ------------------------------------------------------------------

    @property
    def dirty_since_save(self) -> bool:
        return self.history.depth != self.saved_depth

    def mark_saved(self) -> None:
        self.saved_depth = self.history.depth

    @property
    def track(self) -> int:
        return self.grid.track

    @property
    def silent(self) -> bool:
        return isinstance(self.sink, NullSink)

    def _after_history(self, command: Command | None) -> None:
        if self.selection:
            alive = {n.nid for t in self.song.tracks for n in t.notes}
            self.selection &= alive
        self.grid.dirty = True

    # -- running commands -------------------------------------------------------

    def run(self, command: Command, *, audition: bool = True) -> Command:
        self.history.run(command)
        if audition:
            self.transport.audition(command.auditions())
        self.message = f"{command.label}: {_count(len(command.touched))}"
        return command

    def undo(self) -> str:
        c = self.history.undo()
        self.message = f"Undid {c.label}" if c else "Nothing to undo"
        return self.message

    def redo(self) -> str:
        c = self.history.redo()
        self.message = f"Redid {c.label}" if c else "Nothing to redo"
        return self.message

    def targets(self) -> set[int]:
        """What a note command acts on: the selection, else the note under
        the cursor, else nothing."""
        if self.selection:
            return set(self.selection)
        note = self.grid.note_at_cursor()
        return {note.nid} if note is not None else set()

    def _run_on(self, command: Command, nids: set[int], *, audition: bool = True) -> Command:
        """Run a command over ``nids``; the cursor follows the note it was
        on, so a transpose can be pressed again and land on the same note."""
        anchor = self.grid.note_at_cursor()
        self.run(command, audition=audition)
        if anchor is not None and anchor.nid in nids:
            found = self.song.note_by_id(anchor.nid)
            if found is not None:
                note = found[1]
                self.grid.put_cursor(note.pitch, self.grid.view.tick_to_cell(note.start))
        return command

    @property
    def _tpc(self) -> int:
        return self.grid.view.ticks_per_cell

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
        self.grid.dirty = True

    def frame(self) -> bool:
        """On a timer, from the terminal. Reads the clock, turns the page
        when the playhead runs off the right edge, and says whether the
        picture needs repainting."""
        t = self.transport
        tick = t.position()
        if t.rolling:
            self._follow(tick)
        if tick != self._playhead_shown:
            self._playhead_shown = tick
            self.grid.playhead = tick
            self.grid.dirty = True
        return self.grid.dirty

    def _follow(self, tick: int) -> None:
        """Turn the page for a rolling playhead - unless you scrolled away
        from it: a view moved by hand while rolling is yours until the
        playhead is back in it."""
        g = self.grid
        if g.first_cell != self._follow_anchor and not g.tick_visible(tick):
            return
        g.follow(tick)
        self._follow_anchor = g.first_cell

    def _arm_follow(self) -> None:
        self._follow_anchor = self.grid.first_cell

    def toggle_play(self) -> bool:
        """Space. Returns whether the transport rolls afterwards, and says
        so when there was nothing to play from here."""
        t = self.transport
        self._arm_follow()
        if t.rolling:
            t.stop()
            self.grid.dirty = True
            return False
        if not t.play():
            self.message = "Nothing to play from here - Home for the start"
            return False
        return True

    def play_from_cursor(self) -> bool:
        """Shift+Space: play what you are looking at, from the cursor."""
        self._arm_follow()
        if not self.transport.play(self.grid.cursor_tick):
            self.message = "Nothing to play from the cursor"
            self.grid.dirty = True
            return False
        return True

    def rewind(self) -> None:
        """Home: bar 1, rolling or not, and the view and cursor go with it."""
        self.grid.go_to_bar(1)
        self.seek(0)

    def picture(self):
        """The frame to paint, built if anything changed - the playhead
        read first, so a seek shows without waiting for the timer."""
        self.frame()
        if self.grid.dirty or self.grid.frame is None:
            self.grid.build()
        return self.grid.frame

    # -- keys -------------------------------------------------------------------

    def key(self, name: str, character: str | None = None) -> bool:
        """A key by its Textual name. Returns whether it was handled."""
        name = normalise_key(name, character)
        if self.palette.is_open:
            return self._palette_key(name, character)
        if self.help_open:                          # any key puts the table away
            self.help_open = False
            return True
        if name != "ctrl+q":
            self.quit_armed = False
        g = self.grid

        # -- the palette, the key table, files, quitting
        if name in ("colon", "ctrl+p"):
            self.palette.open()
            return True
        if name == "question_mark":
            self.help_open = True
            return True
        if name == "ctrl+s":
            self.save()
            return True
        if name == "ctrl+shift+s":                  # arrives as Ctrl+S in Windows Terminal
            self.open_prompt("file.save_as")
            return True
        if name == "ctrl+q":
            self.request_quit()
            return True

        # -- the transport
        if name == "escape":
            if self.transport.rolling:
                self.transport.stop()
                g.dirty = True
            elif self.selection:
                self.deselect()
            return True
        if name == "space":
            self.toggle_play()
            return True
        if name in ("shift+p", "shift+space"):      # Windows Terminal sends Shift+Space as Space
            self.play_from_cursor()
            return True
        if name == "p":
            self.seek(g.cursor_tick)
            return True
        if name == "a":
            self.toggle_audition()
            return True

        # -- editing at the cursor
        if name == "enter":
            self.enter()
            return True
        if name in ("s", "shift+enter"):            # Windows Terminal sends Shift+Enter as Enter
            self.add_cursor_note()
            return True
        if name in ("delete", "backspace"):
            self.delete()
            return True
        if name in ("ctrl+z", "ctrl+y", "ctrl+shift+z"):   # Ctrl+Shift+Z arrives as Ctrl+Z there
            self.undo() if name == "ctrl+z" else self.redo()
            return True
        if name == "ctrl+a":
            self.select_all()
            return True
        if name in ("x", "ctrl+x"):
            self.cut()
            return True
        if name in ("c", "ctrl+c"):
            self.copy()
            return True
        if name in ("v", "ctrl+v"):                 # Windows Terminal keeps Ctrl+V for its own paste
            self.paste()
            return True
        if name in ("d", "ctrl+d"):
            self.duplicate()
            return True
        if name == "q":
            self.quantise_to_grid()
            return True
        if name in ("shift+up", "shift+down"):
            self.transpose(1 if name == "shift+up" else -1)
            return True
        if name in ("right_square_bracket", "left_square_bracket",
                    "ctrl+shift+up", "ctrl+shift+down"):   # the chords scroll Windows Terminal
            self.transpose_diatonic(1 if name in ("right_square_bracket", "ctrl+shift+up") else -1)
            return True
        if name in ("shift+left", "shift+right"):
            self.nudge(1 if name == "shift+right" else -1)
            return True
        if name in ("ctrl+up", "ctrl+down"):
            self.nudge_velocity(VELOCITY_STEP if name == "ctrl+up" else -VELOCITY_STEP)
            return True
        if name in ("plus", "equals_sign", "minus"):
            self.resize(-1 if name == "minus" else 1)
            return True

        # -- the cursor and the view
        if name in ("up", "down"):
            g.move_cursor(d_pitch=1 if name == "up" else -1)
            return True
        if name in ("left", "right"):
            g.move_cursor(d_cells=1 if name == "right" else -1)
            return True
        if name in ("ctrl+left", "ctrl+right"):
            g.move_cursor_bars(1 if name == "ctrl+right" else -1)
            return True
        if name in ("pageup", "pagedown"):
            g.move_cursor(d_pitch=12 if name == "pageup" else -12)
            return True
        if name == "home":
            self.rewind()
            return True
        if name == "end":
            g.go_to_bar(g.last_bar())
            return True
        if name in ("tab", "shift+tab"):
            n = len(self.song.tracks)
            self.select_track((self.track + (-1 if name == "shift+tab" else 1)) % n)
            return True
        if name == "full_stop":
            g.zoom_finer()
            return True
        if name == "comma":
            g.zoom_coarser()
            return True
        if name == "g":
            g.set_ghosts(not g.ghosts)
            return True
        if name == "f":
            g.fit_pitch_range()
            return True
        return False

    def _palette_key(self, name: str, character: str | None) -> bool:
        """While the ``:`` line is open it takes every key."""
        p = self.palette
        if name == "escape":
            p.close()
        elif name == "enter":
            outcome = p.submit()
            if outcome is not None:
                key, raw = outcome
                self.run_key(key, raw)
        elif name == "up":
            p.move(-1)
        elif name == "down":
            p.move(1)
        elif name == "backspace":
            p.backspace()
        elif character and len(character) == 1 and character.isprintable() \
                and not name.startswith(("ctrl+", "alt+")):
            p.type(character)
        return True

    def open_prompt(self, key: str) -> None:
        """Straight to a command's questions, as Ctrl+Shift+S does for Save as."""
        entry = next((e for e in tui_entries() if e.key == key), None)
        if entry is None or not self.palette.prompt(entry):
            self.run_key(key)

    # -- actions at the cursor ------------------------------------------------------

    def enter(self) -> None:
        """Enter is a click: select and play the note under the cursor, or
        insert one cell's worth of note there and play it."""
        g = self.grid
        note = g.note_at_cursor()
        if note is not None:
            self.selection.clear()
            self.selection.add(note.nid)
            self.transport.audition([note])
            self.message = ""
            g.dirty = True
            return
        self.run(edit.AddNote(self.track, g.cursor_pitch, g.cursor_tick, self._tpc, DEFAULT_VELOCITY))

    def add_cursor_note(self) -> None:
        """Shift+Enter is Shift+click: toggle the note under the cursor in the set."""
        note = self.grid.note_at_cursor()
        if note is None:
            return
        if note.nid in self.selection:
            self.selection.discard(note.nid)
        else:
            self.selection.add(note.nid)
        self.transport.audition([note])
        self.message = f"sel {len(self.selection)}"
        self.grid.dirty = True

    def delete(self) -> None:
        nids = self.targets()
        if nids:
            self.selection -= nids
            self.run(edit.DeleteNotes(nids), audition=False)

    def select_all(self) -> None:
        self.selection.clear()
        self.selection.update(n.nid for n in self.song.tracks[self.track].notes)
        self.message = f"Selected {len(self.selection)} notes"
        self.grid.dirty = True

    def deselect(self) -> None:
        self.selection.clear()
        self.message = ""
        self.grid.dirty = True

    def select_track(self, index: int) -> None:
        self.grid.set_track(index)
        t = self.song.tracks[self.grid.track]
        self.message = f'trk {self.grid.track} "{t.name}"' if t.name else f"trk {self.grid.track}"

    def toggle_audition(self) -> bool:
        self.transport.audition_enabled = not self.transport.audition_enabled
        self.message = f"Audition {'on' if self.transport.audition_enabled else 'off'}"
        return self.transport.audition_enabled

    def transpose(self, semitones: int) -> None:
        nids = self.targets()
        if nids:
            self._run_on(edit.TransposeChromatic(nids, semitones), nids)

    def transpose_diatonic(self, degrees: int) -> None:
        nids = self.targets()
        if nids:
            self._run_on(edit.TransposeDiatonic(nids, degrees, root=self.key_root,
                                                scale=self.key_scale), nids)

    def nudge(self, cells: int) -> None:
        nids = self.targets()
        if nids:
            self._run_on(edit.MoveNotes(nids, 0, cells * self._tpc), nids)

    def resize(self, cells: int) -> None:
        """``+`` and ``-``: a cell longer or shorter, never shorter than one
        cell - a note you can't see is a note you can't grab."""
        nids = self.targets()
        if not nids:
            return
        if cells < 0:
            notes = [self.song.note_by_id(nid) for nid in nids]
            if any(found is not None and found[1].duration <= self._tpc for found in notes):
                self.message = "Already one cell long"
                return
        self._run_on(edit.ResizeNotes(nids, d_ticks=cells * self._tpc), nids)

    def quantise_to_grid(self) -> None:
        nids = self.targets()
        if nids:
            self._run_on(edit.Quantise(nids, self._tpc), nids)

    def nudge_velocity(self, delta: int) -> None:
        nids = self.targets()
        if nids:
            self._run_on(edit.SetVelocity(nids, delta=delta), nids)

    # -- the clipboard ----------------------------------------------------------

    def _selected_notes(self) -> list[Note]:
        nids = self.targets()
        return sorted((n for t in self.song.tracks for n in t.notes if n.nid in nids),
                      key=lambda n: (n.start, n.pitch))

    @staticmethod
    def _specs(notes: list[Note]) -> tuple[NoteSpec, ...]:
        if not notes:
            return ()
        origin = min(n.start for n in notes)
        return tuple((n.pitch, n.start - origin, n.duration, n.velocity) for n in notes)

    def _place(self, specs: tuple[NoteSpec, ...], at: int) -> Command:
        """Add ``specs`` to the current track with their origin at ``at``,
        and make the new notes the selection."""
        command = self.run(edit.AddNotes(
            self.track, [(pitch, at + offset, duration, velocity)
                         for pitch, offset, duration, velocity in specs]))
        self.selection.clear()
        self.selection.update(command.touched)
        self.grid.dirty = True
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
        self.selection -= {n.nid for n in notes}
        self.run(edit.DeleteNotes({n.nid for n in notes}), audition=False)
        self.message = f"Cut {_count(len(notes))}"
        return self.message

    def paste(self) -> str:
        """At the cursor - the one position a keyboard always has."""
        if not self.clipboard:
            self.message = "Paste: clipboard is empty"
            return self.message
        at = self.grid.cursor_tick
        self._place(self.clipboard, at)
        self.message = f"Pasted {_count(len(self.clipboard))} at {self.grid.view.bars.position(at)}"
        return self.message

    def duplicate(self) -> str:
        """The selection again, starting where it ends."""
        notes = self._selected_notes()
        if not notes:
            self.message = "Duplicate: nothing selected"
            return self.message
        at = max(n.end for n in notes)
        self._place(self._specs(notes), at)
        self.message = f"Duplicated {_count(len(notes))} at {self.grid.view.bars.position(at)}"
        return self.message

    # -- files and quitting ------------------------------------------------------

    def save(self) -> str:
        if self.path is None:
            self.message = "No file name - Save as (Ctrl+Shift+S)"
            return self.message
        try:
            io_midi.save(self.song, self.path)
        except Exception as exc:  # noqa: BLE001 - UI boundary
            self.message = f"could not save {self.path.name}: {exc}"
            return self.message
        self.mark_saved()
        self.message = f"saved {self.path.name}"
        return self.message

    def save_as(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            self.message = "Save as: no file name"
            return self.message
        path = Path(text).expanduser()
        if path.suffix.lower() not in (".mid", ".midi"):
            path = path.with_suffix(".mid")
        self.path = path
        return self.save()

    def request_quit(self) -> bool:
        """Ctrl+Q. True when the terminal should close now. With unsaved
        changes the first press only warns; the second discards them."""
        if self.dirty_since_save and not self.quit_armed:
            self.quit_armed = True
            self.message = "Unsaved changes - Ctrl+Q again to discard them, Ctrl+S to save"
            return False
        self.want_quit = True
        return True

    # -- the registry's door ------------------------------------------------------

    def run_key(self, key: str, raw: dict[str, str] | None = None) -> str:
        """Run a palette entry by its key. Returns the status message."""
        raw = raw or {}
        if key == "file.save":
            return self.save()
        if key == "file.save_as":
            return self.save_as(raw.get("path", ""))
        if key == "view.goto_bar":
            try:
                self.grid.go_to_bar(int(raw.get("bar", "1")))
            except ValueError as exc:
                self.message = f"Go to bar: {exc}"
                return self.message
            self.message = ""
            return self.message
        if key == "view.track":
            try:
                index = int(raw.get("track", "0"))
            except ValueError as exc:
                self.message = f"Track: {exc}"
                return self.message
            if not 0 <= index < len(self.song.tracks):
                self.message = f"Track: no track {index} (song has {len(self.song.tracks)})"
                return self.message
            self.select_track(index)
            return self.message
        if key == "transport.play":
            self.toggle_play()
            return self.message
        if key == "transport.rewind":
            self.rewind()
            return self.message
        if key == "app.quit":
            self.request_quit()
            return self.message
        if key == "app.help":
            self.help_open = True
            return self.message
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
        label = REGISTRY[key].label.rstrip(".…")
        nids = self.targets()
        if not nids:
            self.message = f"{label}: nothing selected, and no note under the cursor"
            return self.message
        try:
            command, (self.key_root, self.key_scale) = build_command(
                key, raw, nids, tpc=self._tpc, middle_c_octave=self.grid.middle_c_octave,
                key_root=self.key_root, key_scale=self.key_scale)
        except ValueError as exc:
            self.message = f"{label}: {exc}"
            return self.message
        self._run_on(command, nids, audition=not isinstance(command, edit.DeleteNotes))
        return self.message

    # -- the status line ----------------------------------------------------------

    def status_tail(self) -> str:
        parts = []
        if self.selection:
            parts.append(f"sel {len(self.selection)}")
        if not self.transport.audition_enabled:
            parts.append("audition off")
        if self.silent and self.sink_note:
            parts.append(self.sink_note)
        if self.history.can_undo:
            parts.append(f"undo: {self.history.undo_label}")
        if self.message:
            parts.append(self.message)
        return "  ".join(parts)

    def status_line(self) -> str:
        """``> 9:2.1`` first while rolling, so it reads as playing, then the
        cursor's square and the editor's tail."""
        line = self.grid.status()
        tail = self.status_tail()
        if tail:
            line = f"{line}  {tail}"
        t = self.transport
        if t.rolling:
            line = f"> {self.grid.view.bars.position(t.position())}  {line}"
        return line

    def title(self) -> str:
        name = self.path.name if self.path is not None else "untitled"
        return f"midiseq - {name}{'*' if self.dirty_since_save else ''}"


__all__ = ["Editor", "build_command", "normalise_key", "KEY_HELP", "VELOCITY_STEP"]
