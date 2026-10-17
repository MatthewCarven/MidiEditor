"""Undo and redo.

Right-click deletes with no confirmation, which is only acceptable because this
exists and is reliable. The whole mouse model leans on it.

Because commands are objects, this file is about thirty lines and every UI gets
undo for free — including the TUI, the CLI and anything scripted.
"""

from __future__ import annotations

from typing import Callable

from .edit import Command
from .model import Song


class History:
    def __init__(self, song: Song, limit: int = 500) -> None:
        self.song = song
        self.limit = limit
        self._done: list[Command] = []
        self._undone: list[Command] = []
        self.on_change: list[Callable[[Command | None], None]] = []

    # -- running commands -------------------------------------------------

    def run(self, command: Command) -> Command:
        command.do(self.song)
        self._done.append(command)
        # A new edit after undoing discards the redo branch, as everywhere else.
        self._undone.clear()
        if len(self._done) > self.limit:
            self._done.pop(0)
        self._notify(command)
        return command

    def undo(self) -> Command | None:
        if not self._done:
            return None
        command = self._done.pop()
        command.undo(self.song)
        self._undone.append(command)
        self._notify(command)
        return command

    def redo(self) -> Command | None:
        if not self._undone:
            return None
        command = self._undone.pop()
        command.redo(self.song)
        self._done.append(command)
        self._notify(command)
        return command

    # -- state ------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        return bool(self._undone)

    @property
    def undo_label(self) -> str | None:
        return self._done[-1].label if self._done else None

    @property
    def redo_label(self) -> str | None:
        return self._undone[-1].label if self._undone else None

    @property
    def depth(self) -> int:
        return len(self._done)

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()
        self._notify(None)

    def _notify(self, command: Command | None) -> None:
        for listener in self.on_change:
            listener(command)


__all__ = ["History"]
