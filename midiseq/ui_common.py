"""What both editors share, and neither toolkit sees.

The registry's menu and palette entries, the prompts a command asks, the
key-signature and velocity parsers, the command builder, and two small text
helpers. ``ui_gui`` (DearPyGui) and ``ui_tui`` (Textual) both import from
here; ``ui_gui.editor`` and ``ui_gui.grid`` re-export the names they had
before the lift (2026-10-20), so nothing that imported them had to move.
The TUI used to import these from the window's package, which was the
wrong way round: the terminal is not a kind of window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core import edit, io_midi
from .core.edit import Category, Command
from .core.model import Song, Track, parse_pitch


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


VELOCITY_STEP = 5                # Ctrl+Up/Down; Ctrl+Shift+Up/Down moves by one

#: Registry entries that only make sense as gestures (or as the back half of
#: an editor action), never as menu items.
GESTURE_ONLY = {"note.add", "note.add_notes", "note.move", "note.resize", "note.velocities"}

#: Editor-level actions that live beside the registry in the palette and the
#: Edit menu. They aren't registry commands - some have nothing to undo, the
#: clipboard ones run one with the editor's own state (the clipboard, the
#: playhead) filled in - so the editor runs them by key like everything else.
ACTIONS = [
    ("edit.undo", "Undo", "Ctrl+Z"),
    ("edit.redo", "Redo", "Ctrl+Y"),
    ("edit.cut", "Cut", "Ctrl+X"),
    ("edit.copy", "Copy", "Ctrl+C"),
    ("edit.paste", "Paste", "Ctrl+V"),
    ("edit.duplicate", "Duplicate", "Ctrl+D"),
    ("edit.select_all", "Select all", "Ctrl+A"),
    ("edit.deselect", "Deselect", "Esc"),
    ("edit.audition", "Audition on edit (toggle)", "A"),
]

#: The View menu's entries that both editors share (the rest of that menu
#: is the window's own: zooms, ghosts, the track, middle C).
VIEW_ACTIONS = [
    ("view.key", "Key...", None),
    ("view.scale_filter", "Scale filter (toggle)", "K"),
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
    "xform.humanise": [("timing", "Timing, ticks either way (0: none)", "20"),
                       ("velocity", "Velocity either way (0: none)", "8")],
    "xform.snap_to_scale": [("key", "Key (C major, A minor, D harmonic minor)", "C major")],
    "note.velocity": [("velocity", "Velocity: 96, +10, -10 or x0.8", "+10")],
    "view.key": [("key", "Key (C major, A minor, D harmonic minor)", "C major")],
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






def _count(n: int, word: str = "note") -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


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
    view = [_entry(k, label, sc) for k, label, sc in VIEW_ACTIONS]
    for e in edit_menu() + [e for _, group in menu_sections() for e in group] + view:
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
    if key == "xform.humanise":
        return edit.Humanise(sel, int(raw.get("timing", "20")), int(raw.get("velocity", "8"))), keysig
    if key == "xform.snap_to_scale":
        if raw.get("key", "").strip():
            keysig = parse_key(raw["key"])
        return edit.SnapToScale(sel, root=keysig[0], scale=keysig[1]), keysig
    raise KeyError(key)


def autosave_path(path: Path | None) -> Path:
    """Where unsaved work goes when a window is closed from the outside (the
    X button, which DearPyGui cannot make ask first): beside the song as
    ``name.autosave.mid``, or ``untitled.autosave.mid`` in the working
    directory for a song that was never saved."""
    if path is None:
        return Path.cwd() / "untitled.autosave.mid"
    return path.with_name(f"{path.stem}.autosave{path.suffix or '.mid'}")


def autosave(song: Song, path: Path | None) -> Path:
    """Write ``song`` to its autosave path and return that path."""
    target = autosave_path(path)
    io_midi.save(song, target)
    return target


def empty_song() -> Song:
    """File > New, in either editor: one empty track at 480 ppq."""
    song = Song(ppq=480)
    song.tracks.append(Track(name="Track 1"))
    song.normalise()
    return song


def bars_around(bars, lo: int, hi: int) -> tuple[int, int]:
    """The whole bars covering ticks ``lo`` to ``hi`` (exclusive), or the one
    bar at ``lo`` when ``hi`` is not past it: a loop range."""
    first = bars.bar_containing(lo)
    last = bars.bar_containing(max(lo, hi - 1))
    return first.start_tick, last.end_tick


def loop_text(bars, loop: tuple[int, int] | None) -> str:
    """``bar 9`` or ``bars 9-10``, for a status line."""
    if loop is None:
        return ""
    a = bars.bar_containing(loop[0]).number
    b = bars.bar_containing(loop[1] - 1).number
    return f"bar {a}" if a == b else f"bars {a}-{b}"


PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def key_text(root: int, scale: str) -> str:
    """``A minor``, for a status line."""
    return f"{PITCH_CLASS_NAMES[root % 12]} {scale}"


def entry_label(key: str) -> str:
    """A prompt's title: the registry's label, or an action's, without the dots."""
    if key in edit.REGISTRY:
        return edit.REGISTRY[key].label.rstrip(".…")
    for k, label, _ in ACTIONS + VIEW_ACTIONS:
        if k == key:
            return label.rstrip(".…")
    return key
