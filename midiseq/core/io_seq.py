"""Reading the ``.seq`` view back — and turning an edit into the smallest change.

The text can't carry everything the ``.mid`` does. Three velocity bands, not 127
levels. Cell boundaries, not ticks. Only the rows and bars that were printed. Read
naively, a round trip through a text editor would flatten every velocity, quantise
every off-grid note, and silently delete every bar you didn't happen to print.

So this module does not read a ``.seq`` as *the truth*. It reads it as a
**statement about a picture**, renders the same picture from the source ``.mid``,
and asks what differs. Nothing that matches is touched — the notes you didn't type
over keep their exact velocity and their exact off-grid timing, because they are
never rewritten at all.

    .seq  ─┐
           ├─►  diff cells  ─►  AddNote / DeleteNotes / ResizeNotes / SetVelocity
    .mid  ─┘                     (the same command objects the GUI uses, so this
                                  gets undo and a changelog for free)

That's the passthrough rule one storey up: **only what you actually changed
changes.** Without a source file there's nothing to diff against, so the text
*is* taken as the whole truth and its limits apply — which is the honest deal for
composing from a blank file in vim.

**To delete a note, blank the cell — don't delete the line.** A row that isn't in
the text was never claimed about, and unclaimed is left alone. That's the same
rule that keeps unprinted bars safe, and it has to be the same rule or printing a
two-bar window would wipe the rest of the song.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .edit import AddNote, Command, DeleteNotes, ResizeNotes, SetVelocity
from .glyphs import ASCII, UNICODE, GlyphSet
from .grid import Zoom
from .io_text import DEFAULT_BANDS
from .model import (
    DEFAULT_MIDDLE_C_OCTAVE,
    DEFAULT_VELOCITY,
    Song,
    TempoChange,
    TimeSigChange,
    Track,
    parse_pitch,
)
from .render import GM_DRUMS, Event, Matrix, Tie, render

_SEPARATORS = "│║|H"
#: One header segment: an optional "bar", the number, an optional meter. The word
#: is optional because a narrow bar drops it to keep the number — see glyphs.
#: bar_header, where truncating the number instead would aim an edit at the wrong
#: bar.
_BAR_IN_HEADER = re.compile(r"(?:bar\s+)?(\d+)(?:\s*\((\d+)/(\d+)\))?")
_HEADER_CORNERS = re.compile(r"[┌┬┐+]")
_DRUM_PITCH = {name.lower(): pitch for pitch, name in GM_DRUMS.items()}


class SeqParseError(ValueError):
    """The text isn't a ``.seq``, or is one we can't line up with a grid."""


# --------------------------------------------------------------------------
# What both sides of the diff look like
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CellNote:
    """A note as the *grid* sees it — which is all the text can express.

    Deliberately not a ``Note``: it has no tick and no velocity, only a column
    range and a band, because that is exactly the resolution at which the text
    and the render can be honestly compared.
    """

    pitch: int
    first_col: int
    last_col: int
    band: str                   # soft | medium | loud
    open_ended: bool            # still sounding when the printed window ran out
    nid: int | None = None      # set on the reference side only

    @property
    def key(self) -> tuple[int, int]:
        return (self.pitch, self.first_col)


@dataclass
class Block:
    """One printed chunk of grid: a bar range and the rows that were shown."""

    first_bar: int
    last_bar: int
    rows: dict[int, list[str]]          # pitch -> two-character cells
    labels: dict[int, str]
    #: Every bar named in the header, so the render can be checked against what
    #: the text claims rather than only against its width.
    bar_numbers: list[int] = field(default_factory=list)
    #: ``bar -> (numerator, denominator)``. The bar header prints the meter
    #: wherever it changes, so a meter change *is* in the text and a text-only
    #: build can honour it instead of flattening the song to one time signature.
    meters: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return max((len(cells) for cells in self.rows.values()), default=0)


@dataclass
class ParsedSeq:
    header: dict
    blocks: list[Block]
    glyphs: GlyphSet

    @property
    def zoom(self) -> Zoom:
        raw = str(self.header.get("zoom", "1/16"))
        try:
            return Zoom(int(raw.split("/")[-1]))
        except ValueError as exc:
            raise SeqParseError(f"bad zoom {raw!r}: {exc}") from exc

    @property
    def track_index(self) -> int:
        raw = self.header.get("track", 0)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise SeqParseError(f"bad track number {raw!r}") from exc

    @property
    def middle_c_octave(self) -> int:
        name = str(self.header.get("middle-c", "C4"))
        try:
            return int(name[1:])
        except ValueError:
            return DEFAULT_MIDDLE_C_OCTAVE

    @property
    def bands(self) -> dict[str, int]:
        raw = self.header.get("bands")
        if not raw:
            return dict(DEFAULT_BANDS)
        out = dict(DEFAULT_BANDS)
        for pair in str(raw).split():
            if "=" in pair:
                glyph, value = pair.split("=", 1)
                try:
                    out[glyph] = max(1, min(127, int(value)))
                except ValueError:
                    pass
        return out


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse(text: str) -> ParsedSeq:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("%seq"):
        raise SeqParseError("not a .seq file — expected a '%seq' line first")

    glyphs = ASCII if _looks_ascii(text) else UNICODE
    header = _parse_header(lines)
    blocks = _parse_blocks(lines, glyphs, header)
    if not blocks:
        raise SeqParseError("no grid found — a .seq needs at least one bar header row")
    return ParsedSeq(header=header, blocks=blocks, glyphs=glyphs)


def _looks_ascii(text: str) -> bool:
    """The ascii glyph set uses ``H`` for bar lines, which unicode never does."""
    return "║" not in text and "H" in text


def _parse_header(lines: list[str]) -> dict:
    """``song title "X" ppq 480 …`` — keyword/value pairs, quoted values allowed."""
    header: dict = {}
    token = re.compile(r'([a-z][a-z0-9-]*)\s+("(?:[^"]*)"|\S+)')
    for line in lines:
        if not line or line[0].isspace() or line.startswith("%"):
            continue
        word = line.split(None, 1)[0]
        if word not in ("song", "track", "zoom"):
            break                      # header ends at the first non-header line
        rest = line[len(word):].strip()
        if word == "track":
            index, _, rest = rest.partition(" ")
            header["track"] = index.strip()
        elif word == "zoom":
            value, _, rest = rest.partition(" ")
            header["zoom"] = value.strip()
        for key, value in token.findall(rest):
            header[key] = value.strip('"')
        if word == "zoom" and "bands" in header:
            # bands is several pairs, not one keyword/value — re-read it raw.
            tail = rest.split("bands", 1)[1] if "bands" in rest else ""
            header["bands"] = tail.strip()
    return header


def _parse_blocks(lines: list[str], g: GlyphSet, header: dict) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    gutter_width: int | None = None
    drums = str(header.get("drums", "")).lower() in ("yes", "true", "1")

    for line in lines:
        stripped = line.strip()

        bars = _bar_numbers(stripped)
        if bars:
            current = Block(
                first_bar=bars[0][0], last_bar=bars[-1][0],
                bar_numbers=[n for n, _ in bars], rows={}, labels={},
                meters={n: m for n, m in bars if m},
            )
            gutter_width = None
            blocks.append(current)
            continue

        if current is None:
            continue
        if not stripped:
            current = gutter_width = None       # blank line closes a block
            continue
        if stripped[0] in "─-" and set(stripped) <= set("─-┼╫┴╨╢│║|H+ "):
            current = gutter_width = None       # the rule: velocity lane follows
            continue

        if gutter_width is None:
            # The first row after the bar header is the ruler, whose gutter is
            # pure spaces. That measures the gutter exactly — safer than hunting
            # for a separator character, because the ascii bar line is "H" and
            # twelve GM drum names contain one (HH-c, TomHi, BongoH, ...).
            width = len(line) - len(line.lstrip(" "))
            if width and width < len(line):
                gutter_width = width
            continue

        gutter, body = line[:gutter_width], line[gutter_width:]
        label = _clean_label(gutter, g)
        if not label:
            continue
        pitch = _pitch_of(label, drums, header)
        if pitch is None:
            continue                            # velocity lane rows (f, mf, p, pp)
        if pitch in current.rows:
            raise SeqParseError(
                f"bars {current.first_bar}-{current.last_bar} name {label!r} "
                f"twice. Two rows for one pitch is ambiguous, and the second "
                f"would silently win — so any edit you made to the first would "
                f"vanish without a word.")
        current.rows[pitch] = _cells(body)
        current.labels[pitch] = label

    return [b for b in blocks if b.rows]


def _bar_numbers(line: str) -> list[tuple[int, tuple[int, int] | None]]:
    """``(bar number, meter or None)`` for each bar named in a header line."""
    if not line or line[0] not in "┌+@":
        return []
    if line[0] == "@":
        head = line.split("│")[0].split("|")[0]
        found = re.findall(r"\d+", head)
        return [(int(found[0]), None)] if found else []
    out: list[tuple[int, tuple[int, int] | None]] = []
    for segment in _HEADER_CORNERS.split(line):
        found = _BAR_IN_HEADER.search(segment)
        if found:
            number, num, den = found.groups()
            out.append((int(number),
                        (int(num), int(den)) if num else None))
    return out


def _first_separator(line: str) -> int | None:
    for i, ch in enumerate(line):
        if ch in _SEPARATORS:
            return i
    return None


def _clean_label(gutter: str, g: GlyphSet) -> str:
    body = gutter
    if body[:1] in (g.shade, ">", " ", "▒", "#"):
        body = body[1:]
    return body.strip().rstrip(g.off_grid_flag).strip()


def _pitch_of(label: str, drums: bool, header: dict) -> int | None:
    if drums:
        if label.lower() in _DRUM_PITCH:
            return _DRUM_PITCH[label.lower()]
        if label.startswith("#") and label[1:].isdigit():
            return int(label[1:])
        return None
    octave = DEFAULT_MIDDLE_C_OCTAVE
    name = str(header.get("middle-c", "C4"))
    try:
        octave = int(name[1:])
    except ValueError:
        pass
    try:
        return parse_pitch(label, octave)
    except ValueError:
        return None


def _cells(body: str) -> list[str]:
    plain = "".join(ch for ch in body if ch not in _SEPARATORS)
    return [plain[i:i + 2] for i in range(0, len(plain) - 1, 2)]


# --------------------------------------------------------------------------
# Both sides of the diff, in the same shape
# --------------------------------------------------------------------------

def notes_from_cells(rows: dict[int, list[str]], g: GlyphSet,
                     width: int) -> list[CellNote]:
    """Read the two-character grammar back off the page.

    Char 1 says what happens, char 2 says whether sound carries into the next
    cell — so a note runs from its head until the first cell that releases. Ghost
    glyphs belong to another track and are read-only, so they're skipped.
    """
    heads = {g.head_soft: "soft", g.head_medium: "medium", g.head_loud: "loud"}
    out: list[CellNote] = []
    for pitch, cells in rows.items():
        col = 0
        while col < len(cells):
            cell = cells[col]
            if len(cell) < 2 or cell[0] not in heads:
                col += 1
                continue
            band = heads[cell[0]]
            last = col
            while last < len(cells) - 1 and cells[last][1] in (g.carry, g.sustain):
                last += 1
            open_ended = last == width - 1 and cells[last][1] in (g.carry, g.sustain)
            out.append(CellNote(pitch, col, last, band, open_ended))
            col = last + 1
    return out


def notes_from_matrix(matrix: Matrix) -> list[CellNote]:
    """The same reading, taken from the render rather than from characters — so
    the two sides of the diff can't disagree about how to read the grammar."""
    out: list[CellNote] = []
    width = matrix.width
    for row in matrix.rows:
        col = 0
        while col < width:
            cell = row.cells[col]
            if cell.event is not Event.HEAD:
                col += 1
                continue
            last = col
            while last < width - 1 and row.cells[last].tie is Tie.CARRY:
                last += 1
            out.append(CellNote(
                pitch=row.pitch, first_col=col, last_col=last, band=cell.band,
                open_ended=last == width - 1 and row.cells[last].tie is Tie.CARRY,
                nid=cell.nid,
            ))
            col = last + 1
    return out


# --------------------------------------------------------------------------
# The diff
# --------------------------------------------------------------------------

@dataclass
class Change:
    added: int = 0
    removed: int = 0
    resized: int = 0
    revoiced: int = 0
    untouched: int = 0
    skipped_rows: int = 0

    @property
    def total(self) -> int:
        return self.added + self.removed + self.resized + self.revoiced

    def __str__(self) -> str:
        if not self.total:
            return f"no change ({self.untouched} notes untouched)"
        bits = [f"{self.added} added", f"{self.removed} removed",
                f"{self.resized} resized", f"{self.revoiced} revoiced"]
        shown = ", ".join(b for b in bits if not b.startswith("0 "))
        return f"{shown}, {self.untouched} untouched"


def diff(song: Song, parsed: ParsedSeq) -> tuple[list[Command], Change]:
    """What would have to happen to the song for it to render as this text."""
    track_index = parsed.track_index
    if not 0 <= track_index < len(song.tracks):
        raise SeqParseError(
            f"the file names track {track_index}, but the .mid has "
            f"{len(song.tracks)}")

    zoom = parsed.zoom
    bands = parsed.bands
    band_velocity = {"soft": bands["o"], "medium": bands["x"], "loud": bands["X"]}
    commands: list[Command] = []
    change = Change()

    for block in parsed.blocks:
        matrix = render(
            song, track_index, zoom=zoom,
            first_bar=block.first_bar, last_bar=block.last_bar,
            ghosts=False, occupied_rows_only=False,
            middle_c_octave=parsed.middle_c_octave,
        )
        if matrix.width != block.width:
            raise SeqParseError(
                f"bars {block.first_bar}-{block.last_bar} have {matrix.width} "
                f"cells at zoom {zoom}, but the text has {block.width}. "
                f"Was it edited at a different zoom?")
        rendered = [bar.number for bar in matrix.bars]
        if block.bar_numbers and block.bar_numbers != rendered:
            # Two bars of the same width would slip past the check above, so an
            # edit could land in the wrong bar without a word said.
            raise SeqParseError(
                f"the header names bars {block.bar_numbers}, but that range "
                f"renders as {rendered}. Refusing rather than editing the wrong "
                f"bar.")

        ticks_per_cell = zoom.ticks_per_cell(song.ppq)
        text_notes = notes_from_cells(block.rows, parsed.glyphs, block.width)
        shown = set(block.rows)
        ref_notes = [n for n in notes_from_matrix(matrix) if n.pitch in shown]
        change.skipped_rows += sum(
            1 for row in matrix.rows if row.pitch not in shown
            and any(c.event is Event.HEAD for c in row.cells))

        by_key = {n.key: n for n in ref_notes}
        seen: set[tuple[int, int]] = set()

        for note in text_notes:
            seen.add(note.key)
            reference = by_key.get(note.key)
            if reference is None:
                commands.append(AddNote(
                    track_index, note.pitch,
                    matrix.columns[note.first_col].tick,
                    (note.last_col - note.first_col + 1) * ticks_per_cell,
                    band_velocity[note.band],
                ))
                change.added += 1
                continue

            edited = False
            # An open-ended note ran off the end of the printed window; its real
            # length is unknowable from the text, so a difference there isn't a
            # claim and mustn't become a resize.
            if not (note.open_ended or reference.open_ended) \
                    and note.last_col != reference.last_col:
                commands.append(ResizeNotes(
                    [reference.nid],
                    duration=(note.last_col - note.first_col + 1) * ticks_per_cell))
                change.resized += 1
                edited = True
            if note.band != reference.band:
                commands.append(SetVelocity(
                    [reference.nid], velocity=band_velocity[note.band]))
                change.revoiced += 1
                edited = True
            if not edited:
                change.untouched += 1

        gone = [n.nid for n in ref_notes if n.key not in seen and n.nid is not None]
        if gone:
            commands.append(DeleteNotes(gone))
            change.removed += len(gone)

    return commands, change


# --------------------------------------------------------------------------
# Building from text alone
# --------------------------------------------------------------------------

def to_song(parsed: ParsedSeq) -> Song:
    """No source ``.mid`` to diff against, so the text is all there is.

    Everything the grammar can't say is lost here by definition: velocities land
    on the three band values, notes land on cell boundaries, and only what was
    printed exists. That's the honest deal, and it's why ``-i`` is the default.
    """
    header = parsed.header
    song = Song(
        ppq=int(header.get("ppq", 480)),
        title=str(header.get("title", "")),
        midi_format=1,
    )
    song.clear_maps()
    bpm = float(header.get("tempo", 120))
    song.tempo_map.append(TempoChange(0, int(round(60_000_000.0 / max(1e-6, bpm)))))
    num, _, den = str(header.get("time", "4/4")).partition("/")
    song.timesig_map.append(TimeSigChange(0, int(num or 4), int(den or 4)))

    track = Track(
        name=str(header.get("name", "")),
        channel=max(0, int(header.get("chan", 1)) - 1),
        program=int(header["program"]) if "program" in header else None,
    )
    song.tracks.append(track)
    song.normalise()

    # Meter changes *are* in the text — the bar header prints the signature
    # wherever it changes — so honour them rather than flattening the song to one
    # time signature. Bar start ticks depend on the map being built, so each
    # change is placed against the map as it stands and the walk restarts.
    from .grid import BarMap
    declared: dict[int, tuple[int, int]] = {}
    for block in parsed.blocks:
        declared.update(block.meters)
    for number in sorted(declared):
        num, den = declared[number]
        bar = BarMap(song).bar(number)
        at = song.timesig_at(bar.start_tick)
        if (at.numerator, at.denominator) != (num, den):
            song.timesig_map.append(TimeSigChange(bar.start_tick, num, den))
            song.normalise()

    zoom = parsed.zoom
    bands = parsed.bands
    band_velocity = {"soft": bands["o"], "medium": bands["x"], "loud": bands["X"]}
    ticks_per_cell = zoom.ticks_per_cell(song.ppq)
    bar_map = BarMap(song)

    for block in parsed.blocks:
        base = bar_map.bar(block.first_bar).start_tick
        for note in notes_from_cells(block.rows, parsed.glyphs, block.width):
            song.tracks[0].notes.append(song.make_note(
                note.pitch,
                base + note.first_col * ticks_per_cell,
                (note.last_col - note.first_col + 1) * ticks_per_cell,
                band_velocity[note.band],
            ))
    song.tracks[0].notes.sort(key=lambda n: (n.start, n.pitch))
    if track.program is not None:
        import mido
        from .model import OpaqueEvent
        track.passthrough.append(OpaqueEvent(0, mido.Message(
            "program_change", channel=track.channel, program=track.program), 0))
    return song


__all__ = [
    "parse", "diff", "to_song", "ParsedSeq", "Block", "CellNote", "Change",
    "SeqParseError", "notes_from_cells", "notes_from_matrix",
]
