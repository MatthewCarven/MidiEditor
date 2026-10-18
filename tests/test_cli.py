"""``seq play``, and the diff that ``seq check`` is built on.

No test here opens a MIDI port. ``seq.py`` imports ``open_sink`` by name so
it can be swapped for a fake that hands back a port-shaped sink, and the
command is judged on what it did to that sink: started it where it was
asked, stopped it on every way out, closed it. The null sink itself is what
the command refuses, so the fake has to be a different class with the same
behaviour.
"""

from __future__ import annotations

import random
import struct
from collections import Counter
from pathlib import Path

import pytest

from midiseq.audio.ports import choose_port
from midiseq.audio.sink_null import NullSink
from midiseq.cli import seq
from midiseq.cli.seq import check_one, main, multiset_diff
from midiseq.core import io_midi
from midiseq.core.grid import GridView

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"


class FakePort:
    """The null sink's behaviour behind a class that is not ``NullSink``,
    plus a record of what the command did to it."""

    def __init__(self, clock=None) -> None:
        self._inner = NullSink(keep_log=False, **({"clock": clock} if clock else {}))
        self.stopped = False
        self.closed = False

    @property
    def rolling(self) -> bool:
        return self._inner.rolling

    @property
    def started(self):
        return self._inner.started

    def play(self, audition) -> None:
        self._inner.play(audition)

    def start(self, schedule) -> None:
        self._inner.start(schedule)

    def stop(self) -> None:
        self.stopped = True
        self._inner.stop()

    def elapsed(self) -> float:
        return self._inner.elapsed()

    def close(self) -> None:
        self.closed = True


class FakeStack:
    """A MIDI stack with one output, "fake port", chosen through the real
    ``choose_port`` so a wrong ``--port`` falls back to the null sink exactly
    as it would against a real device list. Remembers every port opened."""

    def __init__(self) -> None:
        self.opened: list[FakePort] = []
        self.clock = None

    def open_sink(self, name=None):
        chosen = choose_port(name, ["fake port"])
        if chosen is None:
            return NullSink(keep_log=False), f"no MIDI output matching {name!r} (silent)"
        port = FakePort(clock=self.clock)
        self.opened.append(port)
        return port, chosen


@pytest.fixture
def stack(monkeypatch):
    fake = FakeStack()
    monkeypatch.setattr(seq, "open_sink", fake.open_sink)
    return fake


def smpte_file(path: Path) -> Path:
    """A header declaring SMPTE time division: frames, not beats."""
    head = b"MThd" + struct.pack(">IHHh", 6, 0, 1, -6136)
    body = b"\x00\x90\x3c\x40" + b"\x40\x80\x3c\x00" + b"\x00\xff\x2f\x00"
    path.write_bytes(head + b"MTrk" + struct.pack(">I", len(body)) + body)
    return path


# --------------------------------------------------------------------------
# seq play
# --------------------------------------------------------------------------

class TestPlay:
    def test_plays_on_the_port_it_names_and_leaves_it_quiet(self, stack, capsys):
        assert main(["play", str(BLUESY), "--seconds", "0.3"]) == 0
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "fake port"
        assert "1:1.1" in out
        (port,) = stack.opened
        assert port.stopped and port.closed

    def test_a_wrong_port_is_a_failure_not_a_silent_success(self, stack, capsys):
        assert main(["play", str(BLUESY), "--port", "zzz", "--seconds", "0.1"]) == 1
        assert "no MIDI output matching 'zzz'" in capsys.readouterr().out
        assert stack.opened == []

    def test_starts_from_the_bar_asked_for(self, stack, capsys):
        assert main(["play", str(BLUESY), "-b", "9", "--seconds", "0.05"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1] == "9:1.1"
        song = io_midi.load(BLUESY)
        (port,) = stack.opened
        assert port.started[0].start_tick == GridView(song).bars.bar(9).start_tick

    def test_ctrl_c_releases_every_sounding_note(self, stack, monkeypatch, capsys):
        """The one invariant that matters at the console: a stray note-on with
        no note-off hangs on the synth, and the program exiting is not
        something a synth hears."""
        def interrupt(_seconds):
            raise KeyboardInterrupt
        monkeypatch.setattr(seq.time, "sleep", interrupt)
        assert main(["play", str(BLUESY)]) == 0
        (port,) = stack.opened
        assert port.stopped and port.closed
        assert capsys.readouterr().out.splitlines()[-1].startswith("stopped at ")

    def test_the_song_ending_ends_the_command(self, stack, capsys):
        """Without --seconds, the command returns when the sink stops rolling
        by itself, and reports where that was: the end of the song."""
        now = [0.0]

        def clock():
            now[0] += 1e6
            return now[0]
        stack.clock = clock
        assert main(["play", str(BLUESY)]) == 0
        (port,) = stack.opened
        assert port.stopped and port.closed
        song = io_midi.load(BLUESY)
        end = GridView(song).bars.position(port.started[0].end_tick)
        assert capsys.readouterr().out.splitlines()[-1] == f"stopped at {end}"

    def test_a_smpte_file_is_refused_before_any_port_is_opened(self, stack, tmp_path, capsys):
        src = smpte_file(tmp_path / "smpte.mid")
        assert main(["play", str(src)]) == 1
        assert "SMPTE" in capsys.readouterr().err
        assert stack.opened == []

    def test_a_bar_before_the_first_is_a_usage_error(self, stack, capsys):
        assert main(["play", str(BLUESY), "-b", "0"]) == 2
        assert stack.opened == []


# --------------------------------------------------------------------------
# The round-trip diff
# --------------------------------------------------------------------------

A, B, C = (0, 60, 0, 480, 64, 0), (0, 64, 0, 480, 64, 0), (1, 60, 960, 1440, 90, 0)


class TestMultisetDiff:
    def test_a_doubled_item_that_comes_back_single_is_lost_once(self):
        assert multiset_diff([A, A, B], [A, B]) == ([A], [])
        assert multiset_diff([A, B], [A, A, B]) == ([], [A])

    def test_each_side_keeps_its_own_order(self):
        lost, invented = multiset_diff([C, A, B, A], [B, C, C])
        assert lost == [A, A]
        assert invented == [C]

    def test_identical_content_is_no_difference(self):
        assert multiset_diff([B, A, A], [A, B, A]) == ([], [])
        assert multiset_diff([], []) == ([], [])

    @pytest.mark.parametrize("seed", range(6))
    def test_agrees_with_the_old_expression_wherever_that_was_right(self, seed):
        """The quadratic ``[x for x in before if x not in after]`` it replaced
        was right about every item wholly absent from the other side, and
        blind to one that merely came back fewer times. Dropping the latter
        from the new answer must give the old one back exactly, repeats and
        order included, and the latter must be exactly the multiplicity
        mismatches."""
        r = random.Random(seed)

        def some():
            return [(r.randint(0, 3), r.randint(0, 2)) for _ in range(r.randint(0, 12))]
        before, after = some(), some()
        old_lost = [x for x in before if x not in after]
        old_invented = [x for x in after if x not in before]
        lost, invented = multiset_diff(before, after)
        assert [x for x in lost if x not in after] == old_lost
        assert [x for x in invented if x not in before] == old_invented
        assert Counter(lost) == Counter(before) - Counter(after)
        assert Counter(invented) == Counter(after) - Counter(before)
        for x in set(lost) - set(old_lost):
            assert 0 < after.count(x) < before.count(x)

    def test_check_one_still_passes_the_demo_file(self):
        record = check_one(str(BLUESY))
        assert record["status"] == "ok" and record["problems"] == []
