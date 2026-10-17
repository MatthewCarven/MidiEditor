"""A sink that swallows everything.

Step 4 wires the audition gesture up to this and the editor works silently. Step 5
swaps in the rtmidi sink and every click you'd been doing mutely for a week starts
making noise. Nothing above this line changes.

It keeps a log because tests need to assert that the right notes *would* have
sounded, and because "did that fire?" is otherwise unanswerable without ears.
"""

from __future__ import annotations

from ..core.transport import Audition


class NullSink:
    def __init__(self, keep_log: bool = True) -> None:
        self.log: list[Audition] = []
        self.keep_log = keep_log
        self._rolling = False

    @property
    def rolling(self) -> bool:
        return self._rolling

    def play(self, audition: Audition) -> None:
        if self.keep_log:
            self.log.append(audition)

    def stop(self) -> None:
        self._rolling = False

    # -- test helpers -----------------------------------------------------

    def set_rolling(self, value: bool) -> None:
        self._rolling = value

    @property
    def last(self) -> Audition | None:
        return self.log[-1] if self.log else None

    def clear(self) -> None:
        self.log.clear()


__all__ = ["NullSink"]
