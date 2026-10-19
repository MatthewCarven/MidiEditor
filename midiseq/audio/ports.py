"""Where the sound goes: which MIDI outputs exist, and opening one.

python-rtmidi is an optional extra (``pip install -e ".[audio]"``). Without
it, or without any port, the app still runs - the null sink swallows
everything and the status line says why there is no sound, which beats a
traceback and beats silence with no explanation.

On Windows the built-in ``Microsoft GS Wavetable Synth`` is always there, so
it is the default when nothing else is asked for.
"""

from __future__ import annotations

from ..core.transport import Sink
from .sink_null import NullSink

PREFERRED = ("GS Wavetable",)


def list_ports() -> list[str]:
    try:
        import mido
        return [str(p) for p in mido.get_output_names()]
    except Exception:  # noqa: BLE001 - no mido, no rtmidi backend, no MIDI stack
        return []


def choose_port(name: str | None, ports: list[str], preferred=PREFERRED) -> str | None:
    """``name`` exactly (case-insensitive), else as a substring; else the first port
    matching a preference; else the first port; None when ``name`` was given
    and matches nothing - a wrong name should not quietly become some other
    device."""
    if not ports:
        return None
    if name:
        wanted = name.lower()
        for p in ports:
            if wanted == p.lower():     # the exact name first: the combo hands these over
                return p
        for p in ports:
            if wanted in p.lower():
                return p
        return None
    for pref in preferred:
        for p in ports:
            if pref.lower() in p.lower():
                return p
    return ports[0]


def _rtmidi_missing() -> str | None:
    """The ImportError's text when python-rtmidi is not installed, else None."""
    try:
        import rtmidi  # noqa: F401
    except ImportError as exc:
        return str(exc)
    return None


def open_sink(name: str | None = None) -> tuple[Sink, str]:
    """A sink and a one-line description of it: the rtmidi sink on the chosen
    port, or the null sink and the reason."""
    missing = _rtmidi_missing()
    if missing is not None:
        return NullSink(keep_log=False), f'no python-rtmidi ({missing}); silent - pip install -e ".[audio]"'
    ports = list_ports()
    if not ports:
        return NullSink(keep_log=False), "no MIDI output ports (silent)"
    chosen = choose_port(name, ports)
    if chosen is None:
        return NullSink(keep_log=False), (f"no MIDI output matching {name!r} (silent); "
                                          f"available: {', '.join(ports)}")
    from .sink_rtmidi import RtMidiSink
    try:
        return RtMidiSink(chosen), chosen
    except Exception as exc:  # noqa: BLE001 - the host MIDI stack
        return NullSink(keep_log=False), f"could not open {chosen}: {exc} (silent)"


__all__ = ["list_ports", "choose_port", "open_sink", "PREFERRED"]
