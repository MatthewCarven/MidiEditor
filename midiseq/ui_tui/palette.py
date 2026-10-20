"""The ``:`` line - the terminal's front door onto the command registry.

PLAN.md §12: Ctrl+P in the GUI, ``:`` in the TUI, type ``inv``, get Invert,
the same registry behind both. The entries, the prompts a command asks
before it runs and the parsers for what you type are shared with the
window (``ui_common.py``, which sees no toolkit), so the two front doors
cannot drift apart. This module is the state machine of a one-line
prompt, driven by key names so it is tested with no terminal: search, then
one prompt per field the command needs (blank means the default), then a
``(key, values)`` pair for the editor to run.
"""

from __future__ import annotations

from typing import Callable

from ..ui_common import PROMPTS, MenuEntry, all_entries

#: A command's prompts: ``(field, caption, default)`` each.
Fields = list[tuple[str, str, str]]

#: The terminal's own entries, after the window's.
TUI_ACTIONS = [
    ("file.new", "New", None),
    ("file.open", "Open...", None),
    ("file.save", "Save", "Ctrl+S"),
    ("file.save_as", "Save as…", None),
    ("view.goto_bar", "Go to bar…", None),
    ("view.track", "Track…", "Tab"),
    ("transport.play", "Play / Stop", "Space"),
    ("transport.rewind", "Rewind", "Home"),
    ("audio.output", "MIDI output...", None),
    ("app.help", "Keys", "?"),
    ("app.quit", "Quit", "Ctrl+Q"),
]

TUI_PROMPTS: dict[str, Fields] = {
    "file.open": [("path", "Open (.mid)", "")],
    "file.save_as": [("path", "Save as (.mid)", "")],
    "view.goto_bar": [("bar", "Bar", "1")],
    "view.track": [("track", "Track index", "0")],
    "audio.output": [("port", "MIDI output (a port name, or silent)", "")],
}


def tui_entries() -> list[MenuEntry]:
    """The window's entries - its Edit menu and the Notes menu's two
    groups - then the terminal's own."""
    return all_entries() + [MenuEntry(key, label, shortcut, key in TUI_PROMPTS)
                            for key, label, shortcut in TUI_ACTIONS]


def prompts_for(key: str) -> Fields | None:
    fields = PROMPTS.get(key) or TUI_PROMPTS.get(key)
    return list(fields) if fields else None


def match(query: str, entries: list[MenuEntry]) -> list[MenuEntry]:
    """Substring matches first, then entries whose letters appear in order,
    so ``tsd`` still finds "Transpose · scale degrees"."""
    q = query.strip().lower()
    if not q:
        return list(entries)

    def subsequence(needle: str, hay: str) -> bool:
        it = iter(hay)
        return all(ch in it for ch in needle)

    direct = [e for e in entries if q in e.label.lower()]
    loose = [e for e in entries if e not in direct and subsequence(q, e.label.lower())]
    return direct + loose


class Palette:
    """Closed, searching, or prompting for a field."""

    def __init__(self, entries: Callable[[], list[MenuEntry]] = tui_entries,
                 prompts: Callable[[str], Fields | None] = prompts_for) -> None:
        self._entries = entries
        self._prompts = prompts
        self.mode = "closed"
        self.text = ""
        self.highlight = 0
        self.matches: list[MenuEntry] = []
        self.chosen: MenuEntry | None = None
        self.fields: Fields = []
        self.field_index = 0
        self.raw: dict[str, str] = {}

    # -- state ---------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.mode != "closed"

    @property
    def searching(self) -> bool:
        return self.mode == "search"

    @property
    def prompting(self) -> bool:
        return self.mode == "prompt"

    def open(self) -> None:
        self.mode = "search"
        self.text = ""
        self.highlight = 0
        self.chosen = None
        self.matches = match("", self._entries())

    def close(self) -> None:
        self.mode = "closed"
        self.text = ""
        self.matches = []
        self.chosen = None
        self.fields = []

    def prompt(self, entry: MenuEntry) -> bool:
        """Skip the search and ask an entry's questions - what Ctrl+Shift+S
        does for Save as. False when the entry has none to ask."""
        fields = self._prompts(entry.key)
        if not fields:
            return False
        self.mode = "prompt"
        self.chosen = entry
        self.fields = fields
        self.field_index = 0
        self.raw = {}
        self.text = ""
        return True

    # -- keys ----------------------------------------------------------------

    def type(self, text: str) -> None:
        self.text += text
        if self.searching:
            self.matches = match(self.text, self._entries())
            self.highlight = 0

    def backspace(self) -> None:
        self.text = self.text[:-1]
        if self.searching:
            self.matches = match(self.text, self._entries())
            self.highlight = 0

    def move(self, delta: int) -> None:
        if self.matches:
            self.highlight = max(0, min(len(self.matches) - 1, self.highlight + delta))

    def submit(self) -> tuple[str, dict[str, str]] | None:
        """Enter. The chosen entry's key and its answers once there is
        something to run; None while the palette is still asking (or
        nothing matched, in which case it stays open)."""
        if self.searching:
            if not self.matches:
                return None
            entry = self.matches[self.highlight]
            if self.prompt(entry):
                return None
            self.close()
            return entry.key, {}
        if self.prompting and self.chosen is not None:
            name, _, default = self.fields[self.field_index]
            self.raw[name] = self.text if self.text.strip() else default
            self.field_index += 1
            self.text = ""
            if self.field_index < len(self.fields):
                return None
            key, raw = self.chosen.key, dict(self.raw)
            self.close()
            return key, raw
        return None

    # -- what to draw -----------------------------------------------------------

    @property
    def caption(self) -> str:
        if self.prompting and self.chosen is not None:
            _, caption, default = self.fields[self.field_index]
            hint = f" [{default}]" if default else ""
            return f"{self.chosen.label.rstrip('.…')} - {caption}{hint}: "
        return ":"

    @property
    def line(self) -> str:
        return self.caption + self.text

    def shown(self, rows: int) -> list[tuple[MenuEntry, bool]]:
        """A window of the matches ``rows`` long that keeps the highlight in
        it, each with whether it is the highlighted one."""
        if not self.searching or rows <= 0:
            return []
        start = 0 if self.highlight < rows else self.highlight - rows + 1
        return [(e, i == self.highlight)
                for i, e in enumerate(self.matches) if start <= i < start + rows]


__all__ = ["Palette", "match", "tui_entries", "prompts_for", "TUI_ACTIONS", "TUI_PROMPTS", "Fields"]
