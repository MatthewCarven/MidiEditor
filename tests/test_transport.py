"""The clock and the schedule - the only place that thinks in seconds."""

from __future__ import annotations

import mido
import pytest

from midiseq.audio.ports import choose_port, open_sink
from midiseq.audio.sink_null import NullSink
from midiseq.core.model import OpaqueEvent, Song, TempoChange, Track
from midiseq.core.transport import (
    NOTE_OFF, NOTE_ON, ORDER_OFF, ORDER_OFF_AFTER_ON, ORDER_ON, ORDER_OTHER,
    Audition, AuditionNote, TempoClock, Transport, schedule,
)

PPQ = 480
BEAT = PPQ
BAR = 4 * BEAT


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def two_track_song() -> Song:
    """Channel 0 piano, channel 1 pad, 120 bpm, a program change and a
    controller sweep in the passthrough, a zero-length note for the rule."""
    song = Song(ppq=PPQ)
    song.tempo_map = [TempoChange(0, 500_000)]
    piano = Track(name="piano", channel=0)
    piano.passthrough.append(OpaqueEvent(0, mido.Message("program_change", channel=0, program=4), 0))
    piano.notes.append(song.make_note(60, 0, BEAT, 100))
    piano.notes.append(song.make_note(60, BEAT, BEAT, 90))         # re-struck on the tick the first ends
    piano.notes.append(song.make_note(72, 2 * BEAT, 0, 80))        # zero-length
    pad = Track(name="pad", channel=1)
    pad.passthrough.append(OpaqueEvent(0, mido.Message("program_change", channel=1, program=89), 0))
    pad.passthrough.append(OpaqueEvent(BEAT, mido.Message("control_change", channel=1, control=7, value=40), 1))
    pad.passthrough.append(OpaqueEvent(3 * BEAT, mido.Message("control_change", channel=1, control=7, value=100), 2))
    pad.notes.append(song.make_note(48, 0, 4 * BEAT, 70))
    song.tracks.extend([piano, pad])
    song.normalise()
    return song


class TestSchedule:
    def test_notes_become_bytes_at_the_right_second(self):
        s = schedule(two_track_song())
        ons = [e for e in s.events if e.is_note_on]
        assert (ons[0].seconds, ons[0].message) == (0.0, (NOTE_ON | 1, 48, 70)) or \
               (ons[0].seconds, ons[0].message) == (0.0, (NOTE_ON | 0, 60, 100))
        c4s = [e for e in ons if e.message == (NOTE_ON, 60, 100)]
        assert c4s[0].seconds == pytest.approx(0.0)
        second = [e for e in ons if e.message == (NOTE_ON, 60, 90)]
        assert second[0].seconds == pytest.approx(0.5)              # 120 bpm: a beat is half a second

    def test_release_comes_before_the_restrike_on_the_same_tick(self):
        s = schedule(two_track_song())
        at_beat = [e for e in s.events if e.tick == BEAT and e.message[1:2] == (60,)]
        assert [e.status for e in at_beat] == [NOTE_OFF, NOTE_ON]

    def test_setup_messages_precede_the_notes_they_are_for(self):
        s = schedule(two_track_song())
        first_three = [e.status for e in s.events[:4]]
        assert 0xC0 in first_three[:2]                                # a program change is sent first
        assert s.events[0].seconds == 0.0 and s.events[0].order == ORDER_OTHER

    def test_a_zero_length_note_is_released_after_its_own_attack(self):
        s = schedule(two_track_song())
        c6 = [e for e in s.events if e.message[1] == 72]
        assert [e.status for e in c6] == [NOTE_ON, NOTE_OFF]
        assert c6[0].order == ORDER_ON and c6[1].order == ORDER_OFF_AFTER_ON

    def test_sorted_by_second_then_order(self):
        s = schedule(two_track_song())
        keys = [e.sort_key() for e in s.events]
        assert keys == sorted(keys)

    def test_meta_events_are_not_sent(self):
        song = two_track_song()
        song.tracks[0].passthrough.append(OpaqueEvent(0, mido.MetaMessage("track_name", name="x"), 9))
        s = schedule(song)
        assert all(e.message[0] >= 0x80 for e in s.events)

    def test_playing_from_the_middle_skips_earlier_notes_but_keeps_the_setup(self):
        s = schedule(two_track_song(), start_tick=2 * BEAT)
        assert s.start_tick == 2 * BEAT
        pitches_on = [e.message[1] for e in s.events if e.is_note_on]
        assert pitches_on == [72]                                   # the pad started earlier: not restarted
        prelude = [e for e in s.events if e.seconds == 0.0 and e.order == ORDER_OTHER]
        raw = {e.message for e in prelude}
        assert (0xC0, 4) in raw and (0xC1, 89) in raw               # both programs
        assert (0xB1, 7, 40) in raw and (0xB1, 7, 100) not in raw   # the controller's LAST value before the start
        later = [e for e in s.events if e.message == (0xB1, 7, 100)]
        assert later and later[0].seconds == pytest.approx(0.5)     # and the one after it, in time

    def test_times_are_relative_to_the_start(self):
        whole = schedule(two_track_song())
        part = schedule(two_track_song(), start_tick=2 * BEAT)
        assert whole.seconds == pytest.approx(2.0)                  # the pad's four beats
        c6 = next(e for e in part.events if e.message[1] == 72 and e.is_note_on)
        assert c6.seconds == pytest.approx(0.0)

    def test_empty_song_empty_schedule(self):
        song = Song(ppq=PPQ)
        song.tracks.append(Track())
        song.normalise()
        assert schedule(song).is_empty

    def test_channel_comes_from_the_note_when_it_has_one(self):
        song = Song(ppq=PPQ)
        t = Track(channel=3)
        t.notes.append(song.make_note(60, 0, BEAT, 80))
        t.notes.append(song.make_note(62, 0, BEAT, 80, channel=9))
        song.tracks.append(t)
        song.normalise()
        ons = sorted(e.message for e in schedule(song).events if e.is_note_on)
        assert ons == [(NOTE_ON | 3, 60, 80), (NOTE_ON | 9, 62, 80)]


class TestTempoClock:
    def test_default_tempo_when_the_file_declares_none(self):
        song = Song(ppq=PPQ)
        song.tracks.append(Track())
        song.tempo_map = []
        assert TempoClock(song).seconds_at(BEAT) == pytest.approx(0.5)   # 120 bpm

    def test_a_late_first_tempo_plays_the_default_until_it(self):
        song = Song(ppq=PPQ)
        song.tempo_map = [TempoChange(BEAT, 1_000_000)]                   # 60 bpm from beat 2
        clock = TempoClock(song)
        assert clock.seconds_at(BEAT) == pytest.approx(0.5)
        assert clock.seconds_at(2 * BEAT) == pytest.approx(1.5)

    def test_tick_at_inverts_seconds_at_across_a_tempo_change(self):
        song = Song(ppq=PPQ)
        song.tempo_map = [TempoChange(0, 500_000), TempoChange(2 * BEAT, 1_000_000)]
        clock = TempoClock(song)
        for tick in (0, 1, 100, BEAT, 2 * BEAT - 1, 2 * BEAT, 2 * BEAT + 1, 5 * BEAT, 37 * BEAT + 11):
            assert clock.tick_at(clock.seconds_at(tick)) == tick
        assert clock.tick_at(-1.0) == 0

    def test_a_restated_tempo_on_the_same_tick_takes_the_last_word(self):
        song = Song(ppq=PPQ)
        song.tempo_map = [TempoChange(0, 500_000), TempoChange(0, 1_000_000)]
        assert TempoClock(song).seconds_at(BEAT) == pytest.approx(1.0)


class TestTransportPlayback:
    def test_play_rolls_from_the_playhead_and_the_position_follows_the_clock(self):
        song = two_track_song()
        clock = FakeClock()
        sink = NullSink(clock=clock)
        t = Transport(song, sink)
        t.playhead = BEAT
        assert t.play()
        assert t.rolling and sink.schedule.start_tick == BEAT
        clock.advance(0.25)                                         # half a beat at 120
        assert t.position() == BEAT + BEAT // 2
        t.stop()
        assert not t.rolling and t.playhead == BEAT + BEAT // 2
        assert t.position() == t.playhead

    def test_running_out_leaves_the_playhead_at_the_end(self):
        song = two_track_song()
        clock = FakeClock()
        t = Transport(song, NullSink(clock=clock))
        t.play(0)
        clock.advance(10.0)
        assert not t.rolling
        assert t.position() == 4 * BEAT                              # the pad's release

    def test_toggle_is_space(self):
        song = two_track_song()
        t = Transport(song, NullSink(clock=FakeClock()))
        assert t.toggle() is True and t.rolling
        assert t.toggle() is False and not t.rolling

    def test_nothing_to_play_is_not_rolling(self):
        song = Song(ppq=PPQ)
        song.tracks.append(Track())
        song.normalise()
        t = Transport(song, NullSink(clock=FakeClock()))
        assert t.play() is False and not t.rolling

    def test_audition_is_refused_while_rolling_and_allowed_after(self):
        song = two_track_song()
        clock = FakeClock()
        sink = NullSink(clock=clock)
        t = Transport(song, sink)
        t.play(0)
        assert not t.audition(song.tracks[0].notes[:1])
        t.stop()
        assert t.audition(song.tracks[0].notes[:1])


class TestAuditionEvents:
    def test_a_chord_is_three_ons_at_zero_then_three_offs(self):
        a = Audition((AuditionNote(60, 80, 0, 0, BEAT), AuditionNote(64, 80, 0, 0, BEAT),
                      AuditionNote(67, 80, 0, 0, BEAT)), PPQ, 500_000)
        ev = a.events()
        assert [e.seconds for e in ev[:3]] == [0.0, 0.0, 0.0] and all(e.is_note_on for e in ev[:3])
        assert all(e.seconds == pytest.approx(0.5) and e.is_note_off for e in ev[3:])

    def test_a_zero_length_note_still_sounds(self):
        a = Audition((AuditionNote(60, 80, 0, 0, 0),), PPQ, 500_000)
        on, off = a.events()
        assert off.seconds > on.seconds

    def test_relative_timing_survives(self):
        a = Audition((AuditionNote(60, 80, 0, 0, BEAT), AuditionNote(64, 80, 0, BEAT, BEAT)), PPQ, 500_000)
        ons = [e for e in a.events() if e.is_note_on]
        assert [e.seconds for e in ons] == pytest.approx([0.0, 0.5])


class TestPorts:
    def test_choose_port_prefers_the_gs_synth_then_the_first(self):
        ports = ["Some USB thing", "Microsoft GS Wavetable Synth 0"]
        assert choose_port(None, ports) == "Microsoft GS Wavetable Synth 0"
        assert choose_port(None, ["Only one"]) == "Only one"
        assert choose_port("usb", ports) == "Some USB thing"

    def test_a_wrong_name_does_not_become_some_other_device(self):
        assert choose_port("zzz", ["A", "B"]) is None
        assert choose_port(None, []) is None

    def test_open_sink_with_a_wrong_name_is_the_null_sink_with_a_reason(self):
        sink, why = open_sink("no such port, surely")
        assert isinstance(sink, NullSink)
        assert "no MIDI output" in why
