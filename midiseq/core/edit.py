"""Every edit is a command object.

Built for undo — but that turns out to mean **the command registry *is* the
function set**. One new class buys undo, a menu entry, a palette entry, a
keyboard shortcut, TUI access and scriptability, with nothing to wire. The GUI
(DearPyGui, since 2026-10-18) generates one menu item, one key handler and one
palette entry per spec from this registry; it has no ``QAction``, so the
registry is the single source instead.

Commands declare a ``category``, and the Notes menu is laid out from it:

* ``KEEPS_SHAPE`` — every note moves together, relationships survive intact.
* ``INDEPENDENT`` — notes move separately, relationships are collateral damage.
  Sometimes exactly what you want, but a different kind of act, and it shouldn't
  sit in the same unlabelled list.

The separator does the teaching. Someone browsing learns there are two kinds of
operation before running either one.

One trap named explicitly: **"transpose up a third" is two different
operations.** Chromatic transpose adds fixed semitones — intervals identical,
may leave the key. Diatonic transpose moves by scale degrees — stays in key, but
some notes move 3 semitones and some 4, so the intervals change. Both are
legitimate. Calling both of them "Transpose" is not. Two classes, two names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Sequence

from .model import DEFAULT_VELOCITY, Note, Song


class Category(Enum):
    EDIT = "edit"                      # File/Edit menu: add, delete, move, resize
    KEEPS_SHAPE = "keeps the shape"    # transpose, invert, retrograde, augment
    INDEPENDENT = "moves notes independently"   # quantise, snap to scale, humanise


@dataclass
class TrackDiff:
    """What one command did to one track, so undo is a straight swap."""

    track: int
    removed: tuple[Note, ...] = ()
    added: tuple[Note, ...] = ()


class Command:
    """Base class. Subclasses implement :meth:`plan`; everything else is free."""

    label: str = "command"
    category: Category = Category.EDIT
    shortcut: str | None = None

    def __init__(self) -> None:
        self._diffs: list[TrackDiff] = []
        self._applied = False

    # -- subclass hook ----------------------------------------------------

    def plan(self, song: Song) -> list[TrackDiff]:
        raise NotImplementedError

    # -- the machinery ----------------------------------------------------

    def do(self, song: Song) -> None:
        self._diffs = self.plan(song)
        for diff in self._diffs:
            self._apply(song, diff)
        self._applied = True

    def undo(self, song: Song) -> None:
        if not self._applied:
            raise RuntimeError(f"{self.label}: undo before do")
        for diff in reversed(self._diffs):
            self._apply(song, TrackDiff(diff.track, diff.added, diff.removed))
        self._applied = False

    def redo(self, song: Song) -> None:
        for diff in self._diffs:
            self._apply(song, diff)
        self._applied = True

    @staticmethod
    def _apply(song: Song, diff: TrackDiff) -> None:
        track = song.tracks[diff.track]
        gone = {n.nid for n in diff.removed}
        if gone:
            track.notes = [n for n in track.notes if n.nid not in gone]
        track.notes.extend(diff.added)
        track.notes.sort(key=lambda n: (n.start, n.pitch))
        # The one place notes change. The note index (core/index.py) keys its
        # cache on this, so nothing else has to remember to invalidate it.
        track.version += 1

    # -- audition ---------------------------------------------------------

    def auditions(self) -> list[Note]:
        """Notes to play back when this command lands. Dropping a dragged chord
        should sound like a chord, which is why the sink takes a *set* with
        relative timing."""
        return list(self._diffs[0].added) if self._diffs else []

    @property
    def touched(self) -> set[int]:
        """Note IDs after the edit — the selection follows the notes, so
        transposing doesn't lose it."""
        return {n.nid for d in self._diffs for n in d.added}

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.label!r}>"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _resolve(song: Song, nids: Iterable[int]) -> dict[int, list[Note]]:
    """Group selected note IDs by track."""
    out: dict[int, list[Note]] = {}
    for nid in nids:
        found = song.note_by_id(nid)
        if found is not None:
            ti, note = found
            out.setdefault(ti, []).append(note)
    return out


def _span(notes: Sequence[Note]) -> tuple[int, int]:
    return min(n.start for n in notes), max(n.end for n in notes)


# --------------------------------------------------------------------------
# Basic edits
# --------------------------------------------------------------------------

class AddNote(Command):
    label = "Add note"

    def __init__(self, track: int, pitch: int, start: int, duration: int,
                 velocity: int = DEFAULT_VELOCITY) -> None:
        super().__init__()
        self.track, self.pitch = track, pitch
        self.start, self.duration, self.velocity = start, duration, velocity

    def plan(self, song: Song) -> list[TrackDiff]:
        note = song.make_note(self.pitch, self.start, self.duration, self.velocity)
        return [TrackDiff(self.track, added=(note,))]


#: What the clipboard holds: ``(pitch, start, duration, velocity)``. Plain
#: tuples rather than ``Note`` objects, so a paste mints fresh ids and the
#: copy can never collide with - or alias - the note it came from.
NoteSpec = tuple[int, int, int, int]


class AddNotes(Command):
    """Several notes in one diff: a paste or a duplicate is one undo, and
    auditions as the chord or phrase it is rather than note by note."""

    label = "Add notes"

    def __init__(self, track: int, specs: Iterable[NoteSpec]) -> None:
        super().__init__()
        self.track = track
        self.specs = tuple(specs)

    def plan(self, song: Song) -> list[TrackDiff]:
        added = tuple(song.make_note(pitch, start, duration, velocity)
                      for pitch, start, duration, velocity in self.specs)
        return [TrackDiff(self.track, added=added)]


class DeleteNotes(Command):
    label = "Delete"
    shortcut = "Del"

    def __init__(self, nids: Iterable[int]) -> None:
        super().__init__()
        self.nids = set(nids)

    def plan(self, song: Song) -> list[TrackDiff]:
        return [TrackDiff(ti, removed=tuple(notes))
                for ti, notes in _resolve(song, self.nids).items()]

    def auditions(self) -> list[Note]:
        return []   # deleting shouldn't make a sound


class MoveNotes(Command):
    """Drag. Plain drag moves a note; Shift+drag marquees instead."""

    label = "Move"

    def __init__(self, nids: Iterable[int], d_pitch: int = 0, d_ticks: int = 0) -> None:
        super().__init__()
        self.nids = set(nids)
        self.d_pitch, self.d_ticks = d_pitch, d_ticks

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            moved = tuple(n.moved(pitch=n.pitch + self.d_pitch,
                                  start=n.start + self.d_ticks) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=moved))
        return diffs


class ResizeNotes(Command):
    label = "Resize"

    def __init__(self, nids: Iterable[int], duration: int | None = None,
                 d_ticks: int = 0) -> None:
        super().__init__()
        self.nids = set(nids)
        self.duration, self.d_ticks = duration, d_ticks

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            out = tuple(
                n.resized(self.duration if self.duration is not None
                          else n.duration + self.d_ticks)
                for n in notes
            )
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


class SetVelocity(Command):
    """The velocity lane is editable, not a readout. This is what it fires."""

    label = "Set velocity"
    category = Category.INDEPENDENT

    def __init__(self, nids: Iterable[int], velocity: int | None = None,
                 delta: int = 0, scale: float | None = None) -> None:
        super().__init__()
        self.nids = set(nids)
        self.velocity, self.delta, self.scale = velocity, delta, scale

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            out = []
            for n in notes:
                if self.velocity is not None:
                    v = self.velocity
                elif self.scale is not None:
                    v = round(n.velocity * self.scale)
                else:
                    v = n.velocity + self.delta
                out.append(n.with_velocity(v))
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=tuple(out)))
        return diffs


class SetVelocities(Command):
    """A different value per note, in one command: what a drag across the
    lane fires on release. A sweep that draws a ramp over twenty columns
    is one gesture, so it is one undo; ``SetVelocity`` gives every note the
    same treatment and would need twenty of them."""

    label = "Set velocities"
    category = Category.INDEPENDENT

    def __init__(self, values: dict[int, int]) -> None:
        super().__init__()
        self.values = dict(values)

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.values).items():
            out = tuple(n.with_velocity(self.values[n.nid]) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


# --------------------------------------------------------------------------
# Keeps the shape
# --------------------------------------------------------------------------

class TransposeChromatic(Command):
    """Fixed semitones. Intervals identical, but it may leave the key."""

    label = "Transpose · semitones…"
    category = Category.KEEPS_SHAPE
    shortcut = "Up/Down"

    def __init__(self, nids: Iterable[int], semitones: int) -> None:
        super().__init__()
        self.nids, self.semitones = set(nids), semitones

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            out = tuple(n.moved(pitch=n.pitch + self.semitones) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


MAJOR = (0, 2, 4, 5, 7, 9, 11)
NATURAL_MINOR = (0, 2, 3, 5, 7, 8, 10)
HARMONIC_MINOR = (0, 2, 3, 5, 7, 8, 11)
SCALES = {"major": MAJOR, "minor": NATURAL_MINOR, "harmonic minor": HARMONIC_MINOR}


class TransposeDiatonic(Command):
    """Scale degrees. Stays in key, but the intervals change — some notes move
    three semitones and some four. That is the whole difference from the
    chromatic version, and it is why they are two menu entries with two names.

    Notes outside the scale snap to the nearest scale tone first, which is a
    lossy step and worth knowing about.
    """

    label = "Transpose · scale degrees…"
    category = Category.KEEPS_SHAPE
    shortcut = "Shift+Up/Down"

    def __init__(self, nids: Iterable[int], degrees: int,
                 root: int = 0, scale: str = "major") -> None:
        super().__init__()
        self.nids, self.degrees = set(nids), degrees
        self.root, self.scale = root % 12, SCALES.get(scale, MAJOR)

    def _shift(self, pitch: int) -> int:
        rel = (pitch - self.root) % 12
        octave = (pitch - self.root) // 12
        # Nearest scale tone at or below, so out-of-key notes have somewhere to go.
        degree = max(i for i, s in enumerate(self.scale) if s <= rel)
        target = degree + self.degrees
        octave += target // len(self.scale)
        return self.root + octave * 12 + self.scale[target % len(self.scale)]

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            out = tuple(n.moved(pitch=self._shift(n.pitch)) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


class Invert(Command):
    """Mirror around an axis pitch. Interval sizes survive, directions flip —
    on the grid the shape mirrors vertically, which is the proof."""

    label = "Invert…"
    category = Category.KEEPS_SHAPE

    def __init__(self, nids: Iterable[int], axis: int | None = None) -> None:
        super().__init__()
        self.nids, self.axis = set(nids), axis

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            axis = self.axis
            if axis is None:
                axis = (min(n.pitch for n in notes) + max(n.pitch for n in notes)) // 2
            out = tuple(n.moved(pitch=2 * axis - n.pitch) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


class Retrograde(Command):
    """Reverse in time within the selection's own span. Mirrors horizontally."""

    label = "Retrograde"
    category = Category.KEEPS_SHAPE

    def __init__(self, nids: Iterable[int]) -> None:
        super().__init__()
        self.nids = set(nids)

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            lo, hi = _span(notes)
            out = tuple(n.moved(start=lo + hi - n.end) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


class Scale(Command):
    """Augment (×2) or diminish (÷2). Rhythmic ratios survive; on the grid the
    ties stretch or shrink and the shape is recognisably the same."""

    category = Category.KEEPS_SHAPE

    def __init__(self, nids: Iterable[int], factor: float = 2.0) -> None:
        super().__init__()
        self.nids, self.factor = set(nids), factor
        self.label = f"Augment ×{factor:g}" if factor >= 1 else f"Diminish ÷{1/factor:g}"

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            lo, _ = _span(notes)
            out = tuple(
                n.moved(start=lo + round((n.start - lo) * self.factor))
                 .resized(max(1, round(n.duration * self.factor)))
                for n in notes
            )
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


class Augment(Scale):
    """``Scale`` by two - its own registry entry so the Notes menu can be
    generated from the registry with no special cases."""

    label = "Augment x2"

    def __init__(self, nids: Iterable[int]) -> None:
        super().__init__(nids, 2.0)


class Diminish(Scale):
    label = "Diminish :2"

    def __init__(self, nids: Iterable[int]) -> None:
        super().__init__(nids, 0.5)


# --------------------------------------------------------------------------
# Moves notes independently
# --------------------------------------------------------------------------

class Quantise(Command):
    """Snap starts (and optionally ends) to the grid.

    This is the honest counterpart to the ``~`` off-grid flag: the grid tells you
    when it's drawing an approximation, and this is the command that makes the
    approximation true. Every note moves separately, so relative timing — the
    feel — does not survive. That's the point of the menu separator.
    """

    label = "Quantise…"
    category = Category.INDEPENDENT
    shortcut = "Q"

    def __init__(self, nids: Iterable[int], grid_ticks: int,
                 strength: float = 1.0, ends: bool = False) -> None:
        super().__init__()
        self.nids, self.grid = set(nids), max(1, grid_ticks)
        self.strength, self.ends = max(0.0, min(1.0, strength)), ends

    def _snap(self, tick: int) -> int:
        target = round(tick / self.grid) * self.grid
        return round(tick + (target - tick) * self.strength)

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            out = []
            for n in notes:
                start = self._snap(n.start)
                dur = self._snap(n.end) - start if self.ends else n.duration
                out.append(n.moved(start=start).resized(max(1, dur)))
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=tuple(out)))
        return diffs


class SnapToScale(Command):
    """Pull out-of-key notes to the nearest scale tone. Intervals change."""

    label = "Snap to scale…"
    category = Category.INDEPENDENT

    def __init__(self, nids: Iterable[int], root: int = 0, scale: str = "major") -> None:
        super().__init__()
        self.nids, self.root = set(nids), root % 12
        self.scale = SCALES.get(scale, MAJOR)

    def _snap(self, pitch: int) -> int:
        rel = (pitch - self.root) % 12
        best = min(self.scale, key=lambda s: (abs(s - rel), s))
        return pitch + (best - rel)

    def plan(self, song: Song) -> list[TrackDiff]:
        diffs = []
        for ti, notes in _resolve(song, self.nids).items():
            out = tuple(n.moved(pitch=self._snap(n.pitch)) for n in notes)
            diffs.append(TrackDiff(ti, removed=tuple(notes), added=out))
        return diffs


# --------------------------------------------------------------------------
# The registry — what the menu bar and the palette both read
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandSpec:
    key: str
    label: str
    category: Category
    factory: type
    shortcut: str | None = None


REGISTRY: dict[str, CommandSpec] = {}


def register(key: str, cls: type) -> None:
    REGISTRY[key] = CommandSpec(key, cls.label, cls.category, cls, cls.shortcut)


for _key, _cls in [
    ("note.add", AddNote), ("note.add_notes", AddNotes), ("note.delete", DeleteNotes),
    ("note.move", MoveNotes), ("note.resize", ResizeNotes),
    ("note.velocity", SetVelocity), ("note.velocities", SetVelocities),
    ("xform.transpose.chromatic", TransposeChromatic),
    ("xform.transpose.diatonic", TransposeDiatonic),
    ("xform.invert", Invert), ("xform.retrograde", Retrograde),
    ("xform.augment", Augment), ("xform.diminish", Diminish),
    ("xform.quantise", Quantise), ("xform.snap_to_scale", SnapToScale),
]:
    register(_key, _cls)


def menu_model(category: Category) -> list[CommandSpec]:
    """What the menu bar renders. The GUI turns each spec into one menu item plus
    one key handler; the palette reads the same list. Two front doors, one
    registry."""
    return [s for s in REGISTRY.values() if s.category is category]


def notes_menu() -> list[tuple[str, list[CommandSpec]]]:
    """The Notes menu, grouped so the layout teaches the taxonomy."""
    return [
        (Category.KEEPS_SHAPE.value, menu_model(Category.KEEPS_SHAPE)),
        (Category.INDEPENDENT.value, menu_model(Category.INDEPENDENT)),
    ]


__all__ = [
    "Command", "Category", "TrackDiff", "CommandSpec", "REGISTRY", "NoteSpec",
    "AddNote", "AddNotes", "DeleteNotes", "MoveNotes", "ResizeNotes",
    "SetVelocity", "SetVelocities",
    "TransposeChromatic", "TransposeDiatonic", "Invert", "Retrograde", "Scale",
    "Augment", "Diminish",
    "Quantise", "SnapToScale", "SCALES", "MAJOR", "NATURAL_MINOR",
    "menu_model", "notes_menu", "register",
]
