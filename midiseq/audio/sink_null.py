"""A sink that swallows everything.

Step 4 wired the audition gesture up to this and the editor worked silently.
Step 5 swapped in the rtmidi sink and every click you'd been doing mutely
starts making noise. Nothing above this line changes.

It keeps a log because tests need to assert that the right notes *would* have
sounded, and because "did that fire?" is otherwise unanswerable without ears.
It also keeps time - with whatever clock it is given, so a test can advance
the transport by hand - and reports ``rolling`` the way a real sink would:
true from ``start()`` until the schedule's last second or ``stop()``.
"""

from __future__ import annotations

import time
from typing import Callable

from ..core.transport import Audition, Schedule


class NullSink:
    def __init__(self, keep_log: bool = True, clock: Callable[[], float] = time.perf_counter) -> None:
        self.log: list[Audition] = []
        self.keep_log = keep_log
        self.started: list[Schedule] = []
        self.schedule: Schedule | None = None
        self._clock = clock
        self._t0 = 0.0
        self._rolling = False
        self._forced: bool | None = None

    # -- Sink ----------------------------------------------------------------

    @property
    def rolling(self) -> bool:
        if self._forced is not None:
            return self._forced
        if self._rolling and self.schedule is not None and self.elapsed() >= self.schedule.seconds:
            self._rolling = False
        return self._rolling

    def play(self, audition: Audition) -> None:
        if self.keep_log:
            self.log.append(audition)

    def start(self, schedule: Schedule) -> None:
        self.schedule = schedule
        self.started.append(schedule)
        self._t0 = self._clock()
        self._rolling = True

    def stop(self) -> None:
        self._rolling = False

    def elapsed(self) -> float:
        return (self._clock() - self._t0) if self._rolling else 0.0

    # -- test helpers -----------------------------------------------------

    def set_rolling(self, value: bool | None) -> None:
        """Pretend the transport is rolling (or not), regardless of time.
        ``None`` stops pretending."""
        self._forced = value

    @property
    def last(self) -> Audition | None:
        return self.log[-1] if self.log else None

    def clear(self) -> None:
        self.log.clear()


__all__ = ["NullSink"]
