"""The real sink: python-rtmidi driving an output port.

Everything the transport hands over is already bytes and seconds, so there is
no mido here - ``rtmidi.MidiOut.send_message`` takes a list of ints and that
is the whole protocol. What this module adds is *time*: one scheduler thread,
one heap of ``(due, sequence, bytes)``, and a condition variable it waits on
until the next due second or until something new is pushed. Due times are
absolute clock readings, so a late wake-up is paid once and never accumulates.

Two small decisions worth knowing about:

- **A lead of a few milliseconds before the first event.** Pushing a chord's
  note-ons "due now" would send the first one before the rest were even on
  the heap; with every event due a few ms out, the whole chord lands in one
  batch and the notes strike together.

- **Windows' timer period is raised to 1 ms while the thread lives.** By
  default a timed wait on Windows wakes up to 15 ms late, which is audible as
  a drunk drummer. ``timeBeginPeriod(1)`` is what every sequencer and media
  player does; measured here it brings the wake-up error from ~13 ms to under
  1.5 ms.

The sink also remembers which notes it has started and not yet ended, so a
stop can release exactly those. The null sink and the tests' fake port share
the sink's shape; the tests drive the scheduler by hand through ``pump``.
"""

from __future__ import annotations

import collections
import heapq
import sys
import threading
import time
import weakref
from typing import Callable

import rtmidi

from ..core.transport import (
    ALL_NOTES_OFF, ALL_SOUND_OFF, CONTROL_CHANGE, NOTE_OFF, NOTE_ON, Audition, Schedule,
)

Message = tuple[int, ...]

#: Seconds between "now" and the first event of anything scheduled.
LEAD = 0.005
IDLE_WAIT = 0.5     # the scheduler thread's longest nap with nothing queued


def match_port(name: str, ports: list[str]) -> int | None:
    """Index of ``name`` in ``ports``: exact, else the first case-insensitive
    substring hit. A wrong name is None rather than some other device."""
    if name in ports:
        return ports.index(name)
    wanted = name.lower()
    for i, p in enumerate(ports):
        if wanted in p.lower():
            return i
    return None


def _open_port(name: str):
    out = rtmidi.MidiOut()
    ports = out.get_ports()
    index = match_port(name, ports)
    if index is None:
        raise LookupError(f"no MIDI output named {name!r}; available: {', '.join(ports) or 'none'}")
    out.open_port(index)
    return out


class _TimerPeriod:
    """Hold the Windows multimedia timer at 1 ms; a no-op elsewhere. Balanced
    per instance, so two sinks and one close leave the period raised."""

    def __init__(self) -> None:
        self._winmm = None
        if sys.platform == "win32":
            try:
                import ctypes
                self._winmm = ctypes.WinDLL("winmm")
                self._winmm.timeBeginPeriod(1)
            except Exception:  # noqa: BLE001 - no winmm, no fine timer, still a sink
                self._winmm = None

    def release(self) -> None:
        if self._winmm is not None:
            try:
                self._winmm.timeEndPeriod(1)
            finally:
                self._winmm = None


class RtMidiSink:
    """``Sink`` over ``rtmidi.MidiOut``.

    All sends happen under the sink's lock. In normal use only the scheduler
    thread sends; ``pump`` and ``close`` send from the caller's thread, which
    is safe because they hold the same lock, and is what makes the sink
    testable without a thread or a clock.
    """

    def __init__(self, port_name: str, *, port=None, clock: Callable[[], float] = time.perf_counter,
                 start_thread: bool = True, lead: float = LEAD) -> None:
        self.port_name = port_name
        self.lead = lead
        self.last_error: Exception | None = None
        self._clock = clock
        self._cv = threading.Condition()
        self._heap: list[tuple[float, int, Message]] = []
        self._urgent: collections.deque[Message] = collections.deque()
        self._seq = 0
        self._sounding: collections.Counter[tuple[int, int]] = collections.Counter()
        self._t0 = 0.0
        self._rolling = False
        self._last_seq: int | None = None
        self._closed = False
        self._timer: _TimerPeriod | None = None
        self._thread: threading.Thread | None = None
        self._port = None
        self._port = port if port is not None else _open_port(port_name)
        if start_thread:
            self._timer = _TimerPeriod()
            self._thread = threading.Thread(target=_run, args=(weakref.ref(self), self._cv),
                                            name=f"midiseq rtmidi {port_name}", daemon=True)
            self._thread.start()

    # -- Sink ----------------------------------------------------------------

    @property
    def rolling(self) -> bool:
        with self._cv:
            return self._rolling

    def elapsed(self) -> float:
        """Seconds since the schedule's first event was due. The lead would
        make this negative for a few ms after ``start``; the playhead has no
        use for a negative, so it is clamped."""
        with self._cv:
            return max(0.0, self._clock() - self._t0) if self._rolling else 0.0

    def play(self, audition: Audition) -> None:
        with self._cv:
            if self._closed:
                return
            t0 = self._clock() + self.lead
            for e in audition.events():
                self._push(t0 + e.seconds, e.message)
            self._cv.notify()

    def start(self, schedule: Schedule) -> None:
        """Replace whatever is playing: the old schedule's notes are released
        first (as ``stop`` would), and the new one begins a lead from now."""
        with self._cv:
            if self._closed:
                return
            self._halt()
            pending = [(e.seconds, self._seq + i, e.message) for i, e in enumerate(schedule.events)]
            self._t0 = self._clock() + self.lead    # taken after the build: a long song must not eat its own lead
            self._heap.extend((self._t0 + s, q, m) for s, q, m in pending)
            heapq.heapify(self._heap)               # O(n)
            self._seq += len(schedule.events)
            self._rolling = bool(schedule.events)
            self._last_seq = self._seq - 1 if schedule.events else None
            self._cv.notify()

    def stop(self) -> None:
        """Drop everything pending and release what is sounding.

        Each note gets its own note-off rather than one CC 123 because the GS
        synth (and most synths) honour a per-note release with the patch's
        release envelope - a stopped piano chord rings down instead of being
        chopped. CC 123 is the blunt instrument, kept for ``panic``.
        """
        with self._cv:
            self._halt()
            self._cv.notify()

    # -- more than the protocol ----------------------------------------------

    def panic(self) -> None:
        """Everything off, everywhere: pending dropped, All Notes Off then All
        Sound Off on all sixteen channels. For when a note is stuck and
        nobody knows whose it is."""
        with self._cv:
            if self._closed:
                return
            self._heap.clear()
            self._rolling = False
            self._last_seq = None
            self._sounding.clear()
            for ch in range(16):
                self._urgent.append((CONTROL_CHANGE | ch, ALL_NOTES_OFF, 0))
                self._urgent.append((CONTROL_CHANGE | ch, ALL_SOUND_OFF, 0))
            self._cv.notify()

    def pump(self, now: float | None = None) -> int:
        """Send everything due at ``now`` (default: the clock) and return how
        many messages went. The scheduler thread's one step, exposed so a test
        can turn the crank by hand."""
        with self._cv:
            return self._pump(self._clock() if now is None else now)

    def close(self) -> None:
        """Stop, release the sounding notes, close the port. Idempotent, and
        safe to reach from ``__del__``: nothing here needs the thread, which
        notices ``_closed`` and leaves on its own."""
        with self._cv:
            if self._closed:
                return
            self._closed = True
            self._halt()
            self._pump(float("inf"))
            port, self._port = self._port, None
            timer, self._timer = self._timer, None
            self._cv.notify_all()
        if timer is not None:
            timer.release()
        if port is not None:
            for method in ("close_port", "close"):
                release = getattr(port, method, None)
                if release is not None:
                    try:
                        release()
                    except Exception:  # noqa: BLE001 - a device already gone
                        pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - interpreter teardown
            pass

    # -- under the lock ------------------------------------------------------

    def _push(self, due: float, message: Message) -> None:
        heapq.heappush(self._heap, (due, self._seq, message))
        self._seq += 1

    def _halt(self) -> None:
        """Drop the heap, queue a release for every sounding note, stop
        rolling. The urgent queue survives, so a second stop (or a start hot
        on the heels of a stop) cannot drop releases that have not gone yet."""
        self._heap.clear()
        self._rolling = False
        self._last_seq = None
        # One release per outstanding start: a key can be stacked (two notes
        # overlapping on one pitch, or an audition over the song), and the
        # first release must not make the sink forget the second.
        for (ch, pitch), count in sorted(self._sounding.items()):
            self._urgent.extend([(NOTE_OFF | ch, pitch, 0)] * count)
        self._sounding.clear()

    def _seconds_until_due(self) -> float | None:
        """None when nothing is queued; otherwise how long to wait, where
        zero or less means now."""
        if self._urgent:
            return 0.0
        if self._heap:
            return self._heap[0][0] - self._clock()
        return None

    def _pump(self, now: float) -> int:
        if self._port is None:
            return 0
        sent = 0
        while self._urgent:
            self._send(self._urgent.popleft())
            sent += 1
        while self._heap and self._heap[0][0] <= now:
            _, seq, message = heapq.heappop(self._heap)
            self._send(message)
            sent += 1
            if seq == self._last_seq:
                self._rolling = False
                self._last_seq = None
        return sent

    def _send(self, message: Message) -> None:
        try:
            self._port.send_message(list(message))
        except Exception as exc:  # noqa: BLE001 - a device unplugged mid-song must not kill the thread
            self.last_error = exc
            return
        status, channel = message[0] & 0xF0, message[0] & 0x0F
        if status == NOTE_ON and len(message) > 2 and message[2] > 0:
            self._sounding[(channel, message[1])] += 1
        elif status in (NOTE_OFF, NOTE_ON) and len(message) > 1:
            key = (channel, message[1])
            if self._sounding[key] > 1:
                self._sounding[key] -= 1
            else:
                self._sounding.pop(key, None)


def _run(ref: "weakref.ref[RtMidiSink]", cv: threading.Condition) -> None:
    """The scheduler thread. It holds the sink only while working, never
    across a wait, so dropping the last reference to a sink is enough to end
    its thread."""
    while True:
        with cv:
            sink = ref()
            if sink is None or sink._closed:
                return
            wait = sink._seconds_until_due()
            if wait is not None and wait <= 0:
                sink._pump(sink._clock())
                continue
            del sink
            # Bounded even when idle: if that ``del`` was the last reference,
            # ``__del__`` ran here and nobody is left to notify this thread.
            cv.wait(IDLE_WAIT if wait is None else wait)


__all__ = ["RtMidiSink", "match_port", "LEAD"]
