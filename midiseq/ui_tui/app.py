"""The terminal: Textual over the toolkit-free layer, and nothing else.

``seq tui song.mid``. The pane paints the ``Frame`` the view builds - the
same glyph grid ``seq cat`` prints, the ruler, the velocity lane and the
cursor - by mapping the frame's roles to colours; the status line sits
under it, and the ``:`` palette is a match list and a prompt line above
the status line while it is open (``?`` shows the key table in the same
place). Every key reaches ``Editor.key`` by its Textual name, and a timer
calls ``Editor.frame`` twenty times a second so
a rolling playhead moves and the page turns. Sound goes through the same
sink the window opens, chosen the same way (``--midi-out``, the GS
Wavetable by default, silent with the reason in the status line).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

from ..audio.ports import open_sink
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
from .editor import KEY_HELP, Editor
from .view import Frame, TextView

MIDDLE_C = {"c3": MIDDLE_C_YAMAHA, "c4": MIDDLE_C_SCIENTIFIC, "c5": MIDDLE_C_FL}

#: Frame roles to colours. The glyphs already carry the meaning (the cursor
#: is ``▼`` and ``>`` in the text, a loud note is ``X``), so a terminal
#: without colour still reads; this is the shading on top.
STYLES = {
    "accidental": Style(bgcolor="grey15"),
    "ghost": Style(color="grey46"),
    "note": Style(color="white", bold=True),
    "selected": Style(color="black", bgcolor="yellow3", bold=True),
    "playhead": Style(bgcolor="dark_red"),
    "cursor": Style(reverse=True),
}
FRAME_RATE = 20          # how often the playhead is read, per second
PALETTE_ROWS = 8         # matches shown above the ``:`` line


def frame_text(frame: Frame) -> Text:
    """The frame as one Rich ``Text``: the lines joined, each span's role
    laid on as a style, later spans over earlier ones."""
    text = Text("\n".join(frame.lines), no_wrap=True, overflow="crop", end="")
    stride = frame.width + 1
    for span in frame.spans:
        style = STYLES.get(span.role)
        if style is not None:
            text.stylize(style, span.y * stride + span.x0, span.y * stride + span.x1)
    return text


class GridPane(Widget, can_focus=True):
    """The grid. Always focused: every key comes through here."""

    DEFAULT_CSS = "GridPane { height: 1fr; width: 1fr; }"

    def __init__(self, editor: Editor) -> None:
        super().__init__()
        self.editor = editor

    def on_resize(self, event: events.Resize) -> None:
        self.editor.grid.resize(event.size.width, event.size.height)
        self.app.repaint()

    def on_key(self, event: events.Key) -> None:
        if self.editor.key(event.key, event.character):
            event.stop()
            event.prevent_default()
        self.app.after_key()

    def render(self) -> Text:
        return frame_text(self.editor.picture())


class SeqApp(App):
    ENABLE_COMMAND_PALETTE = False      # ours is ``:``
    CSS = """
    #bottom { dock: bottom; height: auto; }
    #palette_list { height: auto; display: none; color: $text; background: $panel; }
    #palette_line { height: 1; display: none; background: $accent; color: $text; }
    #status { height: 1; background: $primary-background; color: $text; }
    """
    #: Textual's own Ctrl+C would quit; here it copies, like the window.
    BINDINGS = [
        Binding("ctrl+c", "route('ctrl+c')", "Copy", show=False, priority=True),
        Binding("ctrl+q", "route('ctrl+q')", "Quit", show=False, priority=True),
    ]

    def __init__(self, editor: Editor) -> None:
        super().__init__()
        self.editor = editor

    def compose(self) -> ComposeResult:
        yield GridPane(self.editor)
        with Vertical(id="bottom"):
            yield Static(id="palette_list")
            yield Static(id="palette_line")
            yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one(GridPane).focus()
        self.set_interval(1 / FRAME_RATE, self._frame)
        self.repaint()

    def action_route(self, key: str) -> None:
        self.editor.key(key, None)
        self.after_key()

    def after_key(self) -> None:
        if self.editor.want_quit:
            self.exit()
            return
        self.repaint()

    def _frame(self) -> None:
        if self.editor.frame():
            self.repaint()

    def repaint(self) -> None:
        ed = self.editor
        self.query_one(GridPane).refresh()
        self.query_one("#status", Static).update(ed.status_line())
        p = ed.palette
        line = self.query_one("#palette_line", Static)
        matches = self.query_one("#palette_list", Static)
        line.display = p.is_open
        matches.display = ed.help_open or (p.searching and bool(p.matches))
        if p.is_open:
            line.update(Text(p.line + "▏", no_wrap=True, overflow="crop", end=""))
        if ed.help_open:
            matches.update(Text("\n".join(KEY_HELP), no_wrap=True, overflow="crop", end=""))
        elif matches.display:
            rows = []
            for entry, highlighted in p.shown(PALETTE_ROWS):
                shortcut = f"   {entry.shortcut}" if entry.shortcut else ""
                rows.append(("> " if highlighted else "  ") + entry.label + shortcut)
            matches.update("\n".join(rows))
        self.title = ed.title()


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
           midi_out: str | None = None) -> int:
    """Open the terminal editor. Shared by ``seq tui`` and ``python -m
    midiseq.ui_tui``. ``midi_out`` names the output port the way the window
    takes it; None picks the default, and no port at all is silent, with
    the reason in the status line."""
    if path is None:
        song, file = empty_song(), None
    else:
        file = Path(path)
        if not file.exists():
            print(f"seq tui: no such file: {file}", file=sys.stderr)
            return 2
        try:
            song = io_midi.load(file)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(f"seq tui: could not read {file}: {exc}", file=sys.stderr)
            return 1
    if track is not None and not 0 <= track < len(song.tracks):
        print(f"seq tui: no track {track} (file has {len(song.tracks)})", file=sys.stderr)
        return 2
    try:
        grid = TextView(song, track, zoom, ghosts=ghosts, middle_c_octave=middle_c_octave)
    except ValueError as exc:
        print(f"seq tui: {exc}", file=sys.stderr)
        return 1
    if bar > 1:
        grid.go_to_bar(bar)
    sink, note = open_sink(midi_out)
    editor = Editor(grid, sink, path=file, sink_note=note)
    editor.message = note
    try:
        SeqApp(editor).run()
    finally:
        editor.transport.stop()
        close = getattr(sink, "close", None)
        if callable(close):
            close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m midiseq.ui_tui",
                                 description="The grid in the terminal.")
    ap.add_argument("file", nargs="?", help="a .mid to open (default: an empty song)")
    ap.add_argument("-t", "--track", type=int, default=None)
    ap.add_argument("-z", "--zoom", type=int, default=16, choices=Zoom.SUPPORTED)
    ap.add_argument("--middle-c", choices=sorted(MIDDLE_C), default="c4")
    ap.add_argument("--no-ghosts", action="store_true")
    ap.add_argument("-b", "--bar", type=int, default=1, help="start with this bar at the left edge")
    ap.add_argument("--midi-out", metavar="NAME", default=None,
                    help="the MIDI output to play through (a substring of its name)")
    args = ap.parse_args(argv)
    return launch(args.file, track=args.track, zoom=Zoom(args.zoom),
                  middle_c_octave=MIDDLE_C[args.middle_c], ghosts=not args.no_ghosts,
                  bar=args.bar, midi_out=args.midi_out)


__all__ = ["SeqApp", "GridPane", "frame_text", "launch", "main", "empty_song", "STYLES"]
