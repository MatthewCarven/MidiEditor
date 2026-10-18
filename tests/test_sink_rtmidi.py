"""The rtmidi sink's scheduler, driven by hand with a fake clock and a fake
port. One test lets the real thread run against the real clock; nothing here
opens a real MIDI port."""

from __future__ import annotations

import time

import pytest

rtmidi = pytest.importorskip("rtmidi")

from midiseq.audio.sink_rtmidi import LEAD, RtMidiSink, match_port  # noqa: E402
from midiseq.core.transport import (  # noqa: E402
    ALL_NOTES_OFF, ALL_SOUND_OFF, CONTROL_CHANGE, NOTE_OFF, NOTE_ON, ORDER_OFF, ORDER_ON,
    Audition, AuditionNote, Schedule, ScheduledEvent,
)

PPQ = 480


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePort:
    """Records ``(send_time, bytes)`` by whatever clock it is given."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.sent: list[tuple[float, tuple[int, ...]]] = []
        self.closed = 0

    def send_message(self, message) -> None:
        assert isinstance(message, list) and all(isinstance(b, int) for b in message)
        self.sent.append((self.clock(), tuple(message)))

    def close_port(self) -> None:
        self.closed += 1

    @property
    def messages(self) -> list[tuple[int, ...]]:
        return [m for _, m in self.sent]


def on(pitch: int, ch: int = 0, vel: int = 100) -> tuple[int, ...]:
    return (NOTE_ON | ch, pitch, vel)


def off(pitch: int, ch: int = 0) -> tuple[int, ...]:
    return (NOTE_OFF | ch, pitch, 0)


def note(pitch: int, start: float, end: float, ch: int = 0) -> list[ScheduledEvent]:
    return [ScheduledEvent(start, on(pitch, ch), 0, ORDER_ON),
            ScheduledEvent(end, off(pitch, ch), 0, ORDER_OFF)]


def make_schedule(*notes: list[ScheduledEvent]) -> Schedule:
    events = sorted((e for n in notes for e in n), key=ScheduledEvent.sort_key)
    return Schedule(events, 0, 0, max(e.seconds for e in events))


def make_sink(clock: FakeClock) -> tuple[RtMidiSink, FakePort]:
    port = FakePort(clock)
    return RtMidiSink("fake", port=port, clock=clock, start_thread=False), port


class TestSchedulerByHand:
    def test_events_go_out_in_schedule_order_at_their_seconds(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        t0 = clock.now + LEAD
        sink.start(make_schedule(note(60, 0.0, 1.0), note(64, 0.5, 1.0), note(67, 0.5, 0.75)))

        assert sink.pump() == 0                          # the lead: nothing is due at the instant of start
        clock.now = t0
        assert sink.pump() == 1
        assert port.sent == [(t0, on(60))]
        clock.now = t0 + 0.6
        assert sink.pump() == 2
        clock.now = t0 + 2.0
        assert sink.pump() == 3
        assert port.messages == [on(60), on(64), on(67), off(67), off(60), off(64)]
        assert sink.pump() == 0

    def test_a_chord_lands_in_one_batch_because_of_the_lead(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        chord = Audition((AuditionNote(60, 80, 0, 0, PPQ), AuditionNote(64, 80, 0, 0, PPQ),
                          AuditionNote(67, 80, 0, 0, PPQ)), PPQ, 500_000)
        sink.play(chord)
        assert sink.pump() == 0
        clock.advance(LEAD)
        assert sink.pump() == 3
        assert {m for _, m in port.sent} == {on(60, vel=80), on(64, vel=80), on(67, vel=80)}
        assert not sink.rolling                          # an audition is not the transport

    def test_an_audition_during_a_rolling_schedule_is_merged_and_both_play(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        t0 = clock.now + LEAD
        sink.start(make_schedule(note(48, 0.0, 2.0), note(50, 1.0, 2.0)))
        clock.now = t0
        sink.pump()
        clock.now = t0 + 0.4
        sink.play(Audition((AuditionNote(72, 90, 1, 0, PPQ // 2),), PPQ, 500_000))   # on at +0.4, off at +0.65
        assert sink.rolling
        clock.now = t0 + 0.5
        sink.pump()
        clock.now = t0 + 1.0
        sink.pump()
        assert sink.rolling
        clock.now = t0 + 3.0
        sink.pump()
        assert port.messages == [on(48), on(72, 1, 90), off(72, 1), on(50), off(48), off(50)]
        times = [t - t0 for t, _ in port.sent]
        assert times == pytest.approx([0.0, 0.5, 1.0, 1.0, 3.0, 3.0])   # the audition's off was due at +0.655
        assert not sink.rolling

    def test_nothing_is_sent_after_stop(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        t0 = clock.now + LEAD
        sink.start(make_schedule(note(60, 0.0, 1.0), note(62, 0.5, 1.0), note(64, 2.0, 3.0)))
        clock.now = t0
        sink.pump()
        sink.stop()
        assert not sink.rolling and sink.elapsed() == 0.0
        assert sink.pump() == 1                          # the one release stop owes
        clock.now = t0 + 10.0
        assert sink.pump() == 0
        assert port.messages == [on(60), off(60)]

    def test_stop_releases_exactly_the_sounding_notes_once_each(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        t0 = clock.now + LEAD
        sink.start(make_schedule(note(60, 0.0, 1.0), note(62, 0.0, 0.2), note(64, 0.5, 1.0),
                                 note(60, 0.0, 1.0, ch=9)))
        clock.now = t0 + 0.3
        sink.pump()
        assert port.messages[-1] == off(62)
        sink.stop()
        sink.stop()                                      # a second stop has nothing left to release
        sink.pump()
        releases = port.messages[4:]
        assert sorted(releases) == sorted([off(60), off(60, 9)])
        assert port.messages.count(off(62)) == 1 and off(64) not in port.messages

    def test_rolling_ends_by_itself_after_the_last_event(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        t0 = clock.now + LEAD
        sink.start(make_schedule(note(60, 0.0, 0.5), note(64, 0.25, 1.0)))
        assert sink.rolling and sink.elapsed() == 0.0    # clamped: the lead has not passed
        clock.now = t0 + 0.7
        sink.pump()
        assert sink.rolling and sink.elapsed() == pytest.approx(0.7)
        clock.now = t0 + 1.0
        sink.pump()
        assert not sink.rolling and sink.elapsed() == 0.0
        assert port.messages[-1] == off(64)
        sink.stop()
        assert sink.pump() == 0                          # nothing was sounding: no releases

    def test_start_replaces_a_rolling_schedule_and_releases_its_notes_first(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        sink.start(make_schedule(note(60, 0.0, 5.0)))
        clock.advance(1.0)
        sink.pump()
        sink.start(make_schedule(note(72, 0.0, 0.1)))
        clock.advance(1.0)
        sink.pump()
        assert port.messages == [on(60), off(60), on(72), off(72)]
        assert not sink.rolling

    def test_panic_sends_all_notes_off_and_all_sound_off_on_every_channel(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        sink.start(make_schedule(note(60, 0.0, 1.0), note(61, 0.5, 1.0)))
        clock.advance(0.1)
        sink.pump()
        sink.panic()
        sink.pump()
        sent = port.messages[1:]
        assert len(sent) == 32 and len(set(sent)) == 32
        for ch in range(16):
            assert (CONTROL_CHANGE | ch, ALL_NOTES_OFF, 0) in sent
            assert (CONTROL_CHANGE | ch, ALL_SOUND_OFF, 0) in sent
        assert not sink.rolling
        sink.stop()
        clock.advance(5.0)
        assert sink.pump() == 0                          # sounding cleared, pending dropped

    def test_a_note_on_with_zero_velocity_counts_as_a_release(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        sched = Schedule([ScheduledEvent(0.0, on(60), 0, ORDER_ON),
                          ScheduledEvent(0.5, (NOTE_ON, 60, 0), 0, ORDER_OFF)], 0, 0, 0.5)
        sink.start(sched)
        clock.advance(1.0)
        sink.pump()
        sink.stop()
        assert sink.pump() == 0


class TestPortAndLifecycle:
    def test_the_port_is_matched_exactly_then_by_substring(self):
        ports = ["Microsoft GS Wavetable Synth 0", "loopMIDI Port", "Synth"]
        assert match_port("Synth", ports) == 2
        assert match_port("gs wavetable", ports) == 0
        assert match_port("LOOPMIDI", ports) == 1
        assert match_port("nothing like it", ports) is None

    def test_a_wrong_port_name_raises(self):
        with pytest.raises(Exception):
            RtMidiSink("no such port, surely", start_thread=False)

    def test_opening_goes_through_rtmidi_by_index(self, monkeypatch):
        opened = []

        class FakeMidiOut:
            def get_ports(self):
                return ["A", "Bee 1"]

            def open_port(self, index):
                opened.append(index)

            def send_message(self, message):
                pass

            def close_port(self):
                opened.append("closed")

        monkeypatch.setattr(rtmidi, "MidiOut", FakeMidiOut)
        sink = RtMidiSink("bee", start_thread=False)
        assert sink.port_name == "bee" and opened == [1]
        sink.close()
        assert opened == [1, "closed"]

    def test_close_is_idempotent_and_releases_before_the_port_goes(self):
        clock = FakeClock()
        sink, port = make_sink(clock)
        sink.start(make_schedule(note(60, 0.0, 1.0)))
        clock.advance(0.5)
        sink.pump()
        sink.close()
        sink.close()
        assert port.closed == 1
        assert port.messages == [on(60), off(60)]
        sink.start(make_schedule(note(62, 0.0, 1.0)))
        sink.play(Audition((AuditionNote(60, 80, 0, 0, PPQ),), PPQ, 500_000))
        clock.advance(5.0)
        assert sink.pump() == 0 and not sink.rolling
        sink.stop()
        sink.panic()
        del sink                                         # __del__ reaches close() again, quietly


class TestTheThread:
    def test_the_thread_sends_a_short_schedule_in_real_time(self):
        port = FakePort(time.perf_counter)
        sink = RtMidiSink("fake", port=port, start_thread=True)
        try:
            started = time.perf_counter()
            sink.start(make_schedule(note(60, 0.0, 0.04), note(64, 0.02, 0.06)))
            assert sink.rolling
            deadline = started + 0.3
            while len(port.sent) < 4 and time.perf_counter() < deadline:
                time.sleep(0.005)
            assert port.messages == [on(60), on(64), off(60), off(64)]
            due = [started + LEAD + s for s in (0.0, 0.02, 0.04, 0.06)]
            for (sent_at, _), expected in zip(port.sent, due):
                assert -0.001 < sent_at - expected < 0.05
            deadline = time.perf_counter() + 0.1
            while sink.rolling and time.perf_counter() < deadline:
                time.sleep(0.005)
            assert not sink.rolling and sink.elapsed() == 0.0
        finally:
            sink.close()
        assert port.closed == 1
