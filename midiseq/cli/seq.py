"""``seq`` — the console front end.

    seq cat song.mid                    print the grid
    seq cat song.mid -t 1 -z 1/8 -b 9-12
    seq stat song.mid                   what's in the file

    seq cat song.mid -t 2 -b 9-11 > edit.seq
    vim edit.seq
    seq build edit.seq -i song.mid -o out.mid    patch: only what you changed
    seq build newtune.seq -o new.mid             no source: build from the text

    seq play song.mid -b 9 --port "GS"           hear it, from bar 9
"""

from __future__ import annotations

import argparse
import glob as _glob
import sys
import time
from collections import Counter
from pathlib import Path

#: What counts as a MIDI file when walking a folder.
MIDI_SUFFIXES = {".mid", ".midi", ".smf", ".rmi"}


def expand(patterns: list[str]) -> list[Path]:
    r"""Turn what the user typed into a list of files.

    Unix shells expand ``*.mid`` before the program ever sees it. **Windows does
    not** — cmd and PowerShell hand the literal string over and expect the program
    to cope. So a tool that only ever ran on Linux would tell a Windows user
    "no such file: .\midi\*.mid" and look broken while working perfectly.

    A directory means "everything under here", recursively, because that is what
    someone with a folder of old MIDI files actually wants.
    """
    out: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(path)

    for pattern in patterns:
        path = Path(pattern)
        if path.is_dir():
            for found in sorted(path.rglob("*")):
                if found.is_file() and found.suffix.lower() in MIDI_SUFFIXES:
                    add(found)
        elif any(ch in pattern for ch in "*?["):
            for name in sorted(_glob.glob(pattern, recursive=True)):
                found = Path(name)
                if found.is_dir():
                    for deeper in sorted(found.rglob("*")):
                        if deeper.is_file() and deeper.suffix.lower() in MIDI_SUFFIXES:
                            add(deeper)
                elif found.is_file():
                    add(found)
        else:
            add(path)
    return out

#: Imported by name so a test can swap the port for a fake without touching
#: the real MIDI stack.
from ..audio.ports import open_sink
from ..audio.sink_null import NullSink
from ..core import io_midi, io_seq, io_text
from ..core.history import History
from ..core.glyphs import ASCII, UNICODE
from ..core.grid import GridView, Zoom
from ..core.transport import Transport
from ..core.model import (
    MIDDLE_C_FL,
    MIDDLE_C_SCIENTIFIC,
    MIDDLE_C_YAMAHA,
    parse_pitch,
)

MIDDLE_C = {
    "c3": MIDDLE_C_YAMAHA,
    "c4": MIDDLE_C_SCIENTIFIC,
    "c5": MIDDLE_C_FL,
}


def _zoom(text: str) -> Zoom:
    return Zoom(int(text.split("/")[-1]))


def _bars(text: str) -> tuple[int, int | None]:
    if "-" in text:
        first, last = text.split("-", 1)
        return int(first), (int(last) if last.strip() else None)
    n = int(text)
    return n, n


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seq",
        description="View MIDI files as a step-sequencer grid.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    cat = sub.add_parser("cat", help="print a track as a grid")
    cat.add_argument("file")
    cat.add_argument("-t", "--track", type=int, default=None,
                     help="track index (default: the first one with notes)")
    cat.add_argument("-z", "--zoom", type=_zoom, default=Zoom(16),
                     help="1/4, 1/8, 1/16, 1/32, 1/64  (default 1/16)")
    cat.add_argument("-b", "--bars", type=_bars, default=(1, None),
                     help="e.g. 9-12, or 9- for 'to the end'")
    cat.add_argument("-g", "--ghosts", action="store_true",
                     help="show other tracks faintly behind this one")
    cat.add_argument("--full", action="store_true",
                     help="every chromatic row, not just the occupied ones")
    cat.add_argument("--no-lane", action="store_true", help="hide the velocity lane")
    cat.add_argument("--ascii", action="store_true",
                     help="plain ASCII for terminals that mangle box drawing")
    cat.add_argument("--middle-c", choices=sorted(MIDDLE_C), default="c4",
                     help="octave labelling: c4 scientific (default), c3 Yamaha, c5 FL")
    cat.add_argument("-w", "--wrap", type=int, default=2,
                     help="bars per block (default 2)")

    stat = sub.add_parser("stat", help="what's in the file")
    stat.add_argument("file")

    check = sub.add_parser(
        "check", help="does this file survive a load/save round trip?",
        description="Loads the file, writes it straight back out and compares "
                    "what the two say — notes, controllers, SysEx, tempo, meter. "
                    "Not a byte comparison: mido re-encodes, and note_on with "
                    "velocity 0 comes back as note_off. Those aren't changes. "
                    "Anything this reports is.")
    check.add_argument("file", nargs="+",
                       help="files, folders, or wildcards — folders are walked "
                            "recursively, and wildcards are expanded here so "
                            "they work on Windows too")
    check.add_argument("-v", "--verbose", action="store_true",
                       help="list what differs, not just the count")
    check.add_argument("-q", "--quiet", action="store_true",
                       help="only mention files with problems")
    check.add_argument("-g", "--group", action="store_true",
                       help="summarise by problem kind instead of by file "
                            "(automatic past 200 files)")
    check.add_argument("--report", metavar="FILE.json",
                       help="write the full grouped report as JSON — this is "
                            "the thing to send someone, not the console output")
    check.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                       help="check N files at once (worth it past a few "
                            "thousand)")

    build = sub.add_parser(
        "build", help="turn a .seq back into MIDI",
        description="With -i, only the cells you actually changed are edited — "
                    "untouched notes keep their exact velocity and off-grid "
                    "timing, and unprinted bars, unprinted tracks and every "
                    "unmodelled event are left alone. Without -i the text is all "
                    "there is, so velocities land on the header's band values "
                    "and notes land on cell boundaries.")
    build.add_argument("file", help="the .seq to read ('-' for stdin)")
    build.add_argument("-i", "--source", help="the .mid it came from — patch that")
    build.add_argument("-o", "--out", help="where to write (default: in place of -i)")
    build.add_argument("-n", "--dry-run", action="store_true",
                       help="say what would change, write nothing")

    gui = sub.add_parser(
        "gui", help="open the grid window",
        description="The same grid as `seq cat`, in a window you can scroll and "
                    "zoom. Step 3: look, don't touch - editing is step 4. Needs "
                    "DearPyGui (pip install -e \".[gui]\").")
    gui.add_argument("file", nargs="?", help="a .mid to open (default: an empty song)")
    gui.add_argument("-t", "--track", type=int, default=None,
                     help="track index (default: the first one with notes)")
    gui.add_argument("-z", "--zoom", type=_zoom, default=Zoom(16),
                     help="1/4, 1/8, 1/16, 1/32, 1/64  (default 1/16)")
    gui.add_argument("-b", "--bar", type=int, default=1,
                     help="start with this bar at the left edge")
    gui.add_argument("--no-ghosts", action="store_true",
                     help="don't show other tracks behind this one")
    gui.add_argument("--middle-c", choices=sorted(MIDDLE_C), default="c4",
                     help="octave labelling: c4 scientific (default), c3 Yamaha, c5 FL")
    gui.add_argument("--midi-out", metavar="NAME", default=None,
                     help="MIDI output port, matched as a substring (default: the GS "
                          "Wavetable if present, else the first port)")

    play = sub.add_parser(
        "play", help="play the file to a MIDI output",
        description="Rolls from a bar to the end, printing the playhead once a "
                    "second. Ctrl+C stops it and releases every sounding note. "
                    "The point is sound, so no usable output port is a failure, "
                    "not a silent success. Needs python-rtmidi "
                    "(pip install -e \".[audio]\").")
    play.add_argument("file")
    play.add_argument("-b", "--bar", type=int, default=1,
                      help="start from this bar (default 1)")
    play.add_argument("--port", metavar="NAME",
                      help="output port, matched as a case-insensitive substring "
                           "(default: the GS Wavetable synth, else the first port)")
    play.add_argument("--seconds", type=float, default=None, metavar="N",
                      help="stop after N seconds (default: play to the end)")

    return p


def _default_track(song) -> int:
    for i, t in enumerate(song.tracks):
        if t.notes:
            return i
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "build":
        return _build(args)
    if args.command == "check":
        return _check(args)
    if args.command == "gui":
        try:
            from ..ui_gui.app import launch
        except ImportError as exc:
            print(f"seq gui: {exc}\nThe window needs DearPyGui: "
                  f"pip install -e \".[gui]\"", file=sys.stderr)
            return 1
        return launch(args.file, track=args.track, zoom=args.zoom,
                      middle_c_octave=MIDDLE_C[args.middle_c],
                      ghosts=not args.no_ghosts, bar=args.bar, midi_out=args.midi_out)

    path = Path(args.file)
    if not path.exists():
        print(f"seq: no such file: {path}", file=sys.stderr)
        return 2

    try:
        song = io_midi.load(path)
    except Exception as exc:                      # noqa: BLE001 - CLI boundary
        print(f"seq: could not read {path}: {exc}", file=sys.stderr)
        return 1

    if args.command == "stat":
        print(io_midi.summary(song))
        return 0
    if args.command == "play":
        return _play(song, args)

    track = args.track if args.track is not None else _default_track(song)
    if not 0 <= track < len(song.tracks):
        print(f"seq: no track {track} (file has {len(song.tracks)})", file=sys.stderr)
        return 2

    first, last = args.bars
    text = io_text.dump(
        song, track,
        zoom=args.zoom, first_bar=first, last_bar=last,
        ghosts=args.ghosts, full=args.full, lane=not args.no_lane,
        glyphs=ASCII if args.ascii else UNICODE,
        middle_c_octave=MIDDLE_C[args.middle_c],
        bars_per_block=max(1, args.wrap),
    )
    sys.stdout.write(text)
    return 0


#: How often the playhead is printed while rolling, and how often the loop
#: looks up from its nap to notice the song ended or --seconds ran out.
REPORT_EVERY = 1.0
_NAP = 0.05


def _play(song, args) -> int:
    """Roll until the song ends, ``--seconds`` runs out, or Ctrl+C.

    The sink is always stopped on the way out, whatever ended the run: a
    MIDI note-on with no note-off hangs on the synth until something else
    silences it, and "the program exited" is not something a synth hears.
    """
    try:
        view = GridView(song)
    except ValueError as exc:                    # SMPTE: no bars to start from
        print(f"seq: {exc}", file=sys.stderr)
        return 1
    try:
        start = view.bars.bar(args.bar).start_tick
    except ValueError as exc:
        print(f"seq: {exc}", file=sys.stderr)
        return 2

    sink, description = open_sink(args.port)
    print(description)
    if isinstance(sink, NullSink):
        return 1

    transport = Transport(song, sink)
    try:
        if not transport.play(start):
            print(f"nothing to play from bar {args.bar}")
            return 0
        deadline = None if args.seconds is None else time.monotonic() + args.seconds
        report_at = time.monotonic()
        while transport.rolling:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if now >= report_at:
                print(view.bars.position(transport.position()), flush=True)
                report_at = now + REPORT_EVERY
            nap = min(_NAP, report_at - now)
            if deadline is not None:
                nap = min(nap, deadline - now)
            time.sleep(max(0.0, nap))
    except KeyboardInterrupt:
        pass
    finally:
        # Read the playhead before stopping: stop() forgets the schedule, and
        # it is the schedule that knows a run that ended by itself ended at
        # the end rather than where it started.
        where = transport.position()
        transport.stop()
        close = getattr(sink, "close", None)
        if callable(close):
            close()
    print(f"stopped at {view.bars.position(where)}")
    return 0


#: What ``musical_content`` records, and the noun to report a difference with.
_FIELDS = (("notes", "note"), ("other", "unmodelled event"),
           ("meta", "tempo/meter event"))
#: Report at most this many example files per problem kind. A library of twenty
#: thousand files will hit the same handful of bugs thousands of times; the
#: hundredth example teaches nothing and the file just gets too big to read.
EXEMPLARS = 6


def check_one(name: str) -> dict:
    """Round-trip one file and describe what happened. Never raises.

    Returns a record rather than printing, so the same function serves the
    console, the JSON report, and a worker process.
    """
    import tempfile

    from ..core import io_midi

    record: dict = {"path": name, "status": "ok", "problems": []}
    path = Path(name)
    if not path.exists():
        record["status"], record["error"] = "missing", "no such file"
        return record

    try:
        before = io_midi.musical_content(path)
        song = io_midi.load(path)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roundtrip.mid"
            io_midi.save(song, out)
            after = io_midi.musical_content(out)
    except io_midi.Unsupported as exc:
        record["status"] = "declined"
        record["error"] = str(exc).split(".")[0]
        return record
    except Exception as exc:                      # noqa: BLE001 - CLI boundary
        record["status"] = "unreadable"
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["survey"] = _survey(song, before)

    for key, noun in _FIELDS:
        lost, invented = multiset_diff(before[key], after[key])
        for direction, missing in (("lost", lost), ("invented", invented)):
            for item in missing:
                # Group unmodelled events by message type — "sysex lost" and
                # "control_change lost" are different bugs wearing one label.
                what = item[1] if key == "other" else noun
                record["problems"].append(f"{what} {direction}")
    for key in ("ppq", "format"):
        if before[key] != after[key]:
            record["problems"].append(f"{key} changed")
    if record["problems"]:
        record["status"] = "problems"
    return record


def multiset_diff(before: list, after: list) -> tuple[list, list]:
    """What ``before`` has that ``after`` lacks, and the reverse, counting
    repeats: a doubled note that comes back single is a lost note, which a
    set-style ``x not in after`` could never see. Each side keeps its own
    source order.

    Linear. The ``x not in <list>`` it replaces was quadratic in notes per
    file - a second for a few thousand, and a library run that was heading
    for two hours.
    """
    return (_in_order(before, Counter(before) - Counter(after)),
            _in_order(after, Counter(after) - Counter(before)))


def _in_order(items: list, counts: Counter) -> list:
    """``counts`` worth of ``items``, in the order ``items`` has them."""
    out = []
    for item in items:
        if counts[item] > 0:
            counts[item] -= 1
            out.append(item)
    return out


def _survey(song, content: dict) -> dict:
    """What this file *is*, so a big library can describe the wild for us."""
    return {
        "format": content["format"],
        "ppq": song.ppq,
        "smpte": song.is_smpte,
        "tracks": len(song.tracks),
        "notes": len(content["notes"]),
        "tempo_changes": max(0, len(song.tempo_map) - 1),
        "meter_changes": max(0, len(song.timesig_map) - 1),
        "meters": sorted({str(c) for c in song.timesig_map}),
        "event_types": sorted({e[1] for e in content["other"]}),
        "channels": sorted({t.channel for t in song.tracks}),
    }


def _check(args) -> int:
    """The step-2 test point, as a command. Throw a whole library at it."""
    import json

    paths = expand(args.file)
    if not paths:
        print(f"seq: nothing matched {' '.join(args.file)}", file=sys.stderr)
        return 2

    grouped = args.report or args.group or len(paths) > 200
    counts: Counter = Counter()
    problems: Counter = Counter()
    exemplars: dict[str, list[str]] = {}
    errors: Counter = Counter()
    error_exemplars: dict[str, list[str]] = {}
    survey: dict[str, Counter] = {k: Counter() for k in
                                  ("format", "ppq", "meters", "event_types",
                                   "tracks", "channels")}
    totals = Counter()
    worst = 0

    records = _run(paths, args.jobs)
    for done, record in enumerate(records, 1):
        counts[record["status"]] += 1
        if grouped and (done % 200 == 0 or done == len(paths)):
            print(f"\r  {done}/{len(paths)}...", end="", file=sys.stderr, flush=True)

        if record["status"] in ("unreadable", "missing", "declined"):
            if record["status"] != "declined":
                worst = max(worst, 1)
            key = f"{record['status']}: {record.get('error', '')[:70]}"
            errors[key] += 1
            error_exemplars.setdefault(key, [])
            if len(error_exemplars[key]) < EXEMPLARS:
                error_exemplars[key].append(record["path"])
            if not grouped:
                print(f"{record['path']}: {record['status'].upper()} — "
                      f"{record.get('error', '')}")
            continue

        s = record.get("survey", {})
        survey["format"][s.get("format")] += 1
        survey["ppq"]["SMPTE" if s.get("smpte") else s.get("ppq")] += 1
        survey["tracks"][s.get("tracks")] += 1
        for meter in s.get("meters", []):
            survey["meters"][meter] += 1
        for kind in s.get("event_types", []):
            survey["event_types"][kind] += 1
        for channel in s.get("channels", []):
            survey["channels"][channel + 1] += 1
        totals["notes"] += s.get("notes", 0)
        totals["with_meter_changes"] += bool(s.get("meter_changes"))
        totals["with_tempo_changes"] += bool(s.get("tempo_changes"))

        if not record["problems"]:
            if not grouped:
                print(f"{record['path']}: OK — {s.get('notes', 0)} notes, "
                      f"{s.get('tracks', 0)} track(s) came back identical")
            continue

        worst = max(worst, 1)
        for kind in set(record["problems"]):
            problems[kind] += 1
            exemplars.setdefault(kind, [])
            if len(exemplars[kind]) < EXEMPLARS:
                exemplars[kind].append(record["path"])
        if not grouped:
            print(f"{record['path']}: {len(record['problems'])} problem(s)")
            for kind in sorted(set(record["problems"])):
                print(f"  {kind}"
                      + (f" x{record['problems'].count(kind)}"
                         if record["problems"].count(kind) > 1 else ""))

    if grouped:
        print("\r" + " " * 30 + "\r", end="", file=sys.stderr)

    report = {
        "files": len(paths),
        "counts": dict(counts),
        "notes_checked": totals["notes"],
        "problems": [{"kind": k, "files": n, "examples": exemplars[k]}
                     for k, n in problems.most_common()],
        "errors": [{"kind": k, "files": n, "examples": error_exemplars[k]}
                   for k, n in errors.most_common()],
        "survey": {name: dict(c.most_common(25)) for name, c in survey.items()},
        "files_with_meter_changes": totals["with_meter_changes"],
        "files_with_tempo_changes": totals["with_tempo_changes"],
    }

    if grouped or len(paths) > 1:
        _print_summary(report)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nFull report written to {args.report}")
    return worst


def _run(paths, jobs: int):
    """Serial by default. Twenty thousand files is where -j starts to matter."""
    names = [str(p) for p in paths]
    if jobs and jobs > 1 and len(names) > 1:
        from concurrent.futures import ProcessPoolExecutor
        try:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                yield from pool.map(check_one, names, chunksize=16)
            return
        except Exception as exc:                  # noqa: BLE001 - CLI boundary
            print(f"seq: parallel run failed ({exc}); falling back to one "
                  f"process", file=sys.stderr)
    for name in names:
        yield check_one(name)


def _bar(count: int, total: int, width: int = 24) -> str:
    filled = 0 if not total else max(1, round(width * count / total))
    return "█" * filled + " " * (width - filled)


def _print_summary(report: dict) -> None:
    counts = report["counts"]
    total = report["files"]
    print(f"\n{'=' * 62}\n{total} file(s), {report['notes_checked']} notes checked\n"
          f"{'=' * 62}")
    for status, label in (("ok", "came back identical"),
                          ("problems", "lost or invented something"),
                          ("declined", "declined on purpose"),
                          ("unreadable", "could not be read"),
                          ("missing", "not found")):
        if counts.get(status):
            n = counts[status]
            print(f"  {n:>7}  {n / total:>5.1%}  {_bar(n, total)}  {label}")

    if report["problems"]:
        print("\nProblems, by kind — this is the bug list:")
        for entry in report["problems"]:
            print(f"  {entry['files']:>7}  {entry['kind']}")
            print(f"           e.g. {entry['examples'][0]}")
    if report["errors"]:
        print("\nCouldn't read or declined:")
        for entry in report["errors"]:
            print(f"  {entry['files']:>7}  {entry['kind']}")

    survey = report["survey"]
    print("\nWhat's actually in the library:")
    print("  formats      " + ", ".join(f"{k}: {v}" for k, v in survey["format"].items()))
    print("  ppq          " + ", ".join(f"{k}: {v}" for k, v in
                                        list(survey["ppq"].items())[:8]))
    print("  meters       " + ", ".join(f"{k}: {v}" for k, v in
                                        list(survey["meters"].items())[:8]))
    print(f"  {report['files_with_meter_changes']} file(s) change meter, "
          f"{report['files_with_tempo_changes']} change tempo")
    if survey["event_types"]:
        print("  passthrough  " + ", ".join(f"{k}: {v}" for k, v in
                                            list(survey["event_types"].items())[:10]))


def _build(args) -> int:
    if args.file == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.exists():
            print(f"seq: no such file: {path}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8")

    try:
        parsed = io_seq.parse(text)
    except io_seq.SeqParseError as exc:
        print(f"seq: {exc}", file=sys.stderr)
        return 1

    if args.source:
        source = Path(args.source)
        if not source.exists():
            print(f"seq: no such file: {source}", file=sys.stderr)
            return 2
        song = io_midi.load(source)
        try:
            commands, change = io_seq.diff(song, parsed)
        except io_seq.SeqParseError as exc:
            print(f"seq: {exc}", file=sys.stderr)
            return 1
        out = Path(args.out) if args.out else source
        # Run through History rather than mutating directly: same code path as
        # the GUI, and a build becomes undoable the moment anything holds on to it.
        history = History(song)
        for command in commands:
            history.run(command)
        print(f"{args.file} -> {out}: {change}")
        if change.skipped_rows:
            print(f"  {change.skipped_rows} row(s) had notes but weren't in the "
                  f"text — left alone. Blank a cell to delete; don't delete a line.")
        if args.dry_run:
            print("  (dry run — nothing written)")
            return 0
        io_midi.save(song, out)
        return 0

    if not args.out:
        print("seq: need -o when there's no source file to patch", file=sys.stderr)
        return 2
    song = io_seq.to_song(parsed)
    count = sum(len(t.notes) for t in song.tracks)
    print(f"{args.file} -> {args.out}: built {count} note(s) from the text alone")
    print("  velocities came from the header's bands; nothing off-grid survives "
          "a text-only build. Use -i to patch instead.")
    if args.dry_run:
        print("  (dry run — nothing written)")
        return 0
    io_midi.save(song, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
