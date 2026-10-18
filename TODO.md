# Midi Sequencer — TODO

See [PLAN.md](PLAN.md) for the reasoning behind all of this, [README.md](README.md)
for how to run what exists.

## Decisions

- [x] **GUI toolkit — PySide6.** Decided 2026-08-15. LGPL via pip, so no license
      constraint on shipping. Grid is one custom-painted widget, not thousands of
      child widgets.
- [x] **GUI toolkit — DearPyGui.** Decided 2026-10-18, superseding PySide6
      above; reasons and costs in PLAN.md §15. Gated on the grid spike (Step 3,
      first item). Rule carried from PySynthRack's zoom bug: zoom scales the
      drawing, never the widgets.
- [ ] Beginner / child display profile — commit to it or defer to phase 7?
- [ ] `.seq` extension name (cosmetic now that it's only a view)

## Step 1 — core skeleton ✅ done, **test passed 2026-08-16 (FL Studio 2026)**

- [x] `core/model.py` — Song, Track, Note. Ticks always, never seconds. Stable
      note IDs so selection survives edits.
- [x] `core/grid.py` — model ↔ cells, zoom, snap, bar map, off-grid detection
- [x] `core/render.py` — model → **cell-state matrix** (shared by every UI)
- [x] `core/glyphs.py` — cell state → characters. TUI and text view only;
      the GUI paints rectangles from the same matrix
- [x] `core/edit.py` — command objects, categorised so the Notes menu can teach
      the "keeps the shape" / "moves notes independently" split
- [x] `core/history.py` — undo/redo over commands
- [x] `core/io_text.py` — `.seq` view writer
- [x] `core/transport.py` — audition; the sink takes **a set of notes with
      relative timing**, not a single note
- [x] `audio/sink_null.py`
- [x] `cli/seq cat` and `seq stat`
- [x] `core/io_midi.py` **read side** — pulled forward from step 2, because the
      step-1 test point is "cat a real .mid" and that needs a loader. The
      passthrough bucket is populated now so there's nothing to retrofit.
- [x] 50 tests, all on design invariants rather than plumbing
- [x] **Test PASSED — FL Studio 2026, 16 Aug 2026.** Every note, pitch and bar
      width agreed. Details in WORKLOG.md. Original instructions kept below.

      Open `examples/bluesy.mid` in a DAW,
      look at the **Rhodes** track (index 2), bars 9–11, and check it against
      `seq cat examples/bluesy.mid -t 2 -b 9-11 -w 3`. Specifically:

      | bar | beat | note | length | why it's in the file |
      |---|---|---|---|---|
      | 9 | 1 | C4 + E4 together | quarter | a dyad — two rows, one column |
      | 9 | 2 | G4 | quarter | loudest note, `X` band, vel 104 |
      | 9 | 3 | A4 | quarter | |
      | 9 | 4 | D#4 | eighth | accidental row, gets the `▒` shading |
      | 10 | 1 | C4 | quarter | |
      | 10 | 2½ and 2¾ | G4 ×2 | 16ths | staccato pair — collapses at `-z 1/8`, honest at `-z 1/16` |
      | 11 | 1 + 37 ticks | F4 | quarter | **off-grid on purpose** — raises the `~` flag |
      | 11 | 2 | A4 | half | ties across beat 3; bar 11 is **3/4** so it's the last beat |

      Tempo is 96 bpm throughout. Meter is 4/4 until bar 11, then 3/4.

      **FL Studio labels middle C as C5** — run with `--middle-c c5` and the note
      names match one-to-one. **FL's internal PPQ is 96, ours is 480**, so the F5's
      37-tick offset shows as roughly 7 ticks there; what matters is that it's
      visibly off the line, not the digits.

      **Don't save or export the file from FL.** If FL drops the bar-11 meter change
      the reference file stops being a reference. Open, look, close.

      If FL disagrees about *what's in the file*, that's a reader bug and it blocks
      the writer. If FL simply can't show something the file contains, that's FL —
      see PLAN.md §10, "Match DAW conventions, don't inherit DAW limitations".

### Corrections made during step 1

- **Tie semantics were wrong and the tests caught it.** The last cell of a note
  was carrying when it must always release — the tie means "still sounding as the
  next cell begins", and by definition the last cell is where the note stops.
  `CellSpan.fills_last_cell` survives for the GUI, which can draw a rectangle that
  stops where the note stops rather than at the cell boundary.
- **The `~` off-grid flag now fires on an off-grid *start* only.** An off-grid end
  is already told honestly by the tie character. Flagging both meant every
  ordinary slightly-detached note raised the warning, which made it meaningless.
- **Velocity bands realigned** to soft <56, medium <100, loud ≥100. Equal thirds
  put the default velocity of 80 within a hair of "loud", so nearly everything
  rendered as `X`.
- **`cells_per_beat` depends on the meter, not just the zoom.** In 6/8 a beat is
  an eighth, so 1/16 zoom gives two cells, not four. Taking it from the zoom alone
  mislabelled the ruler in compound time.
- **Zoom coarser than 1/4 is refused** — it would put more than one beat in a cell
  and the ruler couldn't label it honestly.

## Step 2 — round-trip ✅ done 2026-08-16

- [x] **Fixed: phantom default tempo/meter at tick 0.** `Song`'s
      `default_factory` seeds the maps with 120 bpm / 4/4 at tick 0 and the loader
      never clears it, so every loaded file carries an event it doesn't contain.
      `seq stat examples/bluesy.mid` already reports one too many of each. Harmless
      in the grid — `tempo_at`/`timesig_at` take the last entry at or before the
      tick, so the real value wins — but the writer would emit 120 bpm at tick 0
      *and then* 96 bpm at tick 0, inventing an event and breaking the
      "unedited file round-trips to the same music" guarantee. Fix on load, not at
      construction: an empty `Song` still wants defaults.

### Corrections made during step 2

- **The phantom seed had a twin.** `normalise()` also guaranteed an entry at tick
  0, inserting `DEFAULT_TEMPO` when the map didn't start there. Same
  invent-an-event mistake, second location: a file declaring no tempo would get a
  120 bpm event written into it that it never had. The default now lives in
  `tempo_at`/`timesig_at` — in the *reading*, not in the data. The map holds what
  the file said and nothing else.
- **`end_of_track` was going into the passthrough bucket.** It's a delimiter, not
  data. Written back in the middle of a track, everything after it is unreachable
  on the next read. Its *position* is real though — it's how a file says "four
  empty bars after the last note" — so it's kept as `Track.end_tick_hint` and
  re-made by the writer.
- **Same-tick ordering is correctness, not tidiness.** A pitch re-struck on the
  beat where the previous one ends came back as one note, because the release was
  written after the new attack. `note_off` now sorts before `note_on`, and
  everything else before `note_on` too, so a program change or CC on the same tick
  is in force *for* that note.
- **A meter change is in the `.seq` after all.** The bar header prints the
  signature wherever it changes, so a text-only build can read it back. Without
  that, `to_song` flattened bar 11 to 4/4 and every bar after it landed wrong.
- **Format 0 had to split before pairing, not after.** The old stub couldn't work:
  once note-ons are matched to note-offs the channel is gone and can't be
  recovered. Splitting the raw message stream first makes it fall out.

### Found by attacking it afterwards, not by building it

Ten more, all silent, all fixed with regression tests. Full write-up in
WORKLOG.md. The short version, because these are the shapes to watch for again:

- A `program_change` for another channel could rehome a whole part (piano in,
  drums out). Track channel now comes from the track's notes.
- Notes carry their own channel now — a format 1 track may legitimately hold
  several, and squashing them moved fifteen parts of sixteen.
- The format 0 split dropped events on channels with no notes. The passthrough
  loss, reintroduced one layer up.
- Drum tracks couldn't be read back: `drums` was a bare word the header parser
  couldn't see, and the ascii bar separator `H` is inside twelve GM drum names.
- Narrow bars lost their number in the header; long numbers lost a digit and
  still parsed, aiming an edit at the wrong bar.
- Zero-length notes grew a tick; format 2 was written wrong instead of refused;
  a restated tempo was deleted; release velocity and metronome click were lost.
- A duplicated pitch row silently discarded the first copy's edits.

**The one to remember:** `seq check` couldn't see release velocity or metronome
click, because `musical_content` didn't record them — so the checker was blind in
exactly the places the writer was lossy, and a green suite proved nothing there.
Before step 3 passes its test, ask what the test *can't* see.

### `seq build` semantics — decided 2026-08-15

**Patch by default; rebuild only when there's no source.** The text can't carry
everything the `.mid` does — three velocity bands, and only the printed rows and
bars exist at all — so treating it as the whole truth would flatten velocities and
quantise anything off-grid.

```
seq build edit.seq -i song.mid -o out.mid    render source, diff the text against
                                             it, edit only the cells that differ
seq build newtune.seq -o newtune.mid         no source: build fresh, velocities
                                             from the header's band values
```

Untouched notes keep their exact velocity and off-grid timing. Unprinted bars,
unprinted tracks and the passthrough bucket are never touched. The passthrough rule
one layer up: **only what you actually changed changes.**

- [x] `core/io_midi.py` **write side** via mido
- [x] Merge passthrough back in tick order; preserve PPQ, format 0 vs 1, track
      order, track names
- [x] `core/io_seq.py` — `.seq` reader, and the diff that makes it safe
- [x] `cli/seq build` — diff-patch mode (`-i`) and standalone mode
- [x] Report what changed: `1 added, 1 removed, 9 untouched`
- [x] `cli/seq check` — throw files at it and see if they survive
- [x] Wildcards expanded by the program, not the shell — **Windows hands
      `.\midi\*.mid` over literally**, so a Unix-only tool looks broken while
      working perfectly. Folders walked recursively; `python -m midiseq` works
      when pip's Scripts dir isn't on PATH
- [x] `--report FILE.json` — group by problem kind with example files, plus a
      survey of what's in the library. The thing to send someone, instead of
      twenty thousand console lines. `-j N` for parallel
- [x] SMPTE time division (negative `ticks_per_beat`) — round-trips fine, but has
      no beat to divide, so the grid refuses with a sentence instead of crashing
      three layers down on a negative cell width
- [x] Format 0 channel splitting — split *before* pairing note ons to offs, which
      is what made the old attempt impossible: after pairing the channel is gone
- [x] 77 new tests (127 total), including ten regressions from an
      adversarial pass — see WORKLOG.md for what each one broke
- [x] **Test:** `seq check` — run 2026-10-18 over 1,595 console/old-collection
      files: 89.3% identical, 47 lost or invented something, 123 unreadable by
      mido. WORKLOG has the breakdown. The edit-a-`.seq`-through-FL half is
      still **for Matthew**.
- [ ] **Bug: note invented** — 32 files; the examples are all Ecco the Dolphin.
      Open one with `seq check -v` and diff the event streams.
- [ ] **Bug: tempo/meter event lost** — 15 files (Landstalker, Sonic 1, Illusion
      of Gaia, Super Mario Kart). `_dedupe` is the first suspect.
- [ ] Maybe, later: a lenient reader for the 62 "data byte must be in range"
      files — old-game SysEx / running-status quirks that mido refuses. In the
      spirit of the thing; not a writer bug.
- [ ] **`seq check` is quadratic in notes per file.** `check_one` diffs
      `before[key]` against `after[key]` with `x not in <list>` — O(n·m) per
      field, so a file with a few thousand notes takes about a second and the
      38,601-file VGMusic run was heading for two hours. A `Counter` multiset
      diff makes it linear. Found 2026-10-18 while killing that run.

## Before step 3 — housekeeping

- [ ] **For Matthew:** move `midis/` out of the project folder (1.3 GB; it was a
      compatibility test), then `git init`. Claude writes the `.gitignore` and
      the first commit after.
- [x] `pip install python-rtmidi` on the 3.14 Python — built from source
      2026-10-18 (1.5.8; no 3.14 wheel, the build just worked).

## Step 3 — GUI shell (no editing)

- [x] **Spike, the gate (2026-10-18): GO.** `tools/spike_dpg_grid.py` — the cell
      matrix in a DearPyGui drawlist, virtualised, scroll + zoom + hover
      hit-test. Tubular Bells (1,087 bars, 37k notes, 38 tracks), repainting
      on every frame while scrolling: dpg draw 5 ms p50 / 13.5 p95, render.py
      7.8 ms p50 / 32 max, 67 fps p50. Numbers and screenshots in WORKLOG.
- [x] **Index the notes** — `core/index.py`, 2026-10-18. Sorted starts plus
      the running maximum of end, two bisects per query; same notes, same
      order as the scan (tested against it on random songs). Keyed on a new
      `Track.version` that `Command._apply` bumps, so nothing has to remember
      to invalidate. Found and fixed on the way: a zero-length note sitting
      exactly on a window's first tick was invisible to the old scan.
- [x] **Built 2026-10-18 — the shell.** `seq gui FILE` / `python -m
      midiseq.ui_gui FILE`: `ui_gui/paint.py` (the dpg-free paint plan),
      `painter.py` (plan → draw calls, tested with a recorder),
      `grid.py` (scroll, two zooms, track, ghosts, hover, the status line —
      no dpg), `app.py` (window, File/View menus, keys, the file dialog).
      32 GUI tests, suite at 179.
- [ ] **For Matthew — eyes, the step-3 test point:** run it and look.
      `seq gui examples/bluesy.mid -t 2 -b 9` (or `python -m midiseq gui …`).
      Wheel = pitches, Shift+wheel = time, Ctrl+wheel = cell width,
      `.` / `,` = finer / coarser grid (1/4…1/64), Tab = next track, G =
      ghosts, F = fit, Home/End, hover names the cell. Then a real file of
      yours with File > Open. Does it feel right? Is it readable? Say what
      fights you. *First pass, 2026-10-18:* Ctrl+Shift+wheel and Ctrl+wheel
      were one gesture on his machine → the grid zoom moved to `.` / `,`.
- [x] Grid as one drawlist (two layers: grid, hover); scroll offsets and the
      cell pixel size (= zoom) owned by `GridWidget`, never by the global font
      scale (PySynthRack's zoom/size feedback bug is the cautionary tale)
- [x] **Virtualised paint**: render.py builds only the visible bars and pitch
      rows, the plan lays out only those; note *spans*, one rect per note with
      a head mark, stopping where the note stops (`Cell.end_fill`)
- [x] A dpg-free "paint plan" layer (matrix → rects/lines/labels by *role*)
      tested without a window; the drawlist glue is forty lines
- [x] Accidental row shading, ghost track rendering, the `~` gutter flag
- [x] Velocity lane with the pp/p/mf/f axis
- [x] Zoom (1/4 … 1/64 as cells-per-beat, plus pixel size), Ctrl+wheel and
      Ctrl+Shift+wheel; the musical zoom keeps the left edge on the same tick
- [x] **Decided: ASCII labels throughout** (`C#4`, not `C♯4`) — DPG's default
      font paints anything above U+00FF as `?`, and the text view already has
      an `--ascii` mode. A font range for ♭/♯ is a later nicety, not a need.
- [ ] Open: the meter label repeats on *every* bar in the text view but only
      where it changes in the window — make the two agree one way or the other
- [ ] Open: no scrollbars yet — position is only visible in the ruler. A thin
      overview strip (where am I in the song) would do more than scrollbars
- [ ] **Test:** does it feel right, is it readable — judge before interaction
      exists. **For Matthew**, see above.

## Step 4 — GUI editing

- [x] **Built 2026-10-18.** `ui_gui/editor.py` — the mouse model, selection,
      undo and audition with no toolkit; `app.py` grew the Edit and Notes
      menus, the palette, the prompts, New / Save / Save as and the
      unsaved-changes guard. 43 editor tests, suite at 222.
- [x] Mouse model (see PLAN §11) — no tool modes. Plus one row the plan didn't
      have: **drag a note's right edge to resize** (`ResizeNotes` had waited
      for a gesture since step 1)
- [x] Note-set selection, Shift+drag marquee (adds to the set), Esc deselects;
      switching track clears it (ghosts can't be selected)
- [x] Undo wired to history; an erase sweep is one undo
- [x] Menu bar + command palette, both generated from the command registry
      (`editor.menu_sections()` / `edit_menu()` / `palette()`; `PROMPTS` says
      which commands ask for a value, as data, so one dialog serves them all).
      `Augment` and `Diminish` became their own registry entries for it.
- [x] Audition fires into the null sink: click-insert plays the note, a drop
      plays what landed, delete is silent; A / Edit menu toggles it
- [x] Keys with a selection: Up/Down transpose, Shift+Up/Down diatonic (C
      major until a prompt names a key), Left/Right nudge a cell, Q quantise,
      Del delete, Ctrl+A select all
- [x] **The velocity lane is editable** (PLAN §8) — built 2026-10-18 by an
      agent: drag a bar, sweep a ramp (interpolated between mouse events so a
      fast sweep leaves no holes), Shift+drag a flat line, one undo per sweep
      (`edit.SetVelocities`); Ctrl+Up/Down nudges a selection by 5, Ctrl+Shift
      by 1; the status line names the velocity under the mouse
- [x] **Clipboard** — Cut / Copy / Paste / Duplicate (Ctrl+X/C/V/D), in the
      Edit menu and the palette; paste lands at the playhead as one undo
      (`edit.AddNotes`) and selects what it pasted; duplicate lands after the
      selection's end. The clipboard holds plain tuples, never Note objects.
- [ ] Open: closing the window with its X skips the unsaved-changes guard
      (DearPyGui has no cancellable close); Ctrl+Q and File > Quit don't
- [ ] **Test — for Matthew:** write a bar by hand, save it, open it in FL,
      and report what fights. `seq gui` with no file starts an empty song.

## Step 5 — playback

- [x] **Built 2026-10-18**, the same day as 3 and 4, by three agents in
      parallel worktrees on a contract written first: `core/transport.py`
      (`schedule()`, `TempoClock`, `Transport.play/stop/position`),
      `audio/ports.py`. Merged clean; adversarially reviewed after.
- [x] `audio/sink_rtmidi.py` — one scheduler thread, a heap of absolute due
      times, an urgent deque for releases, `pump(now)` for tests, winmm
      `timeBeginPeriod(1)` while the thread lives (Windows otherwise wakes
      ~13 ms late). Measured through the GS port: note-on 1.4 ms after due.
- [x] Transport bar (Play/Stop, |<, position, tempo, Audition, output combo),
      the playhead, click-the-ruler seek, Space / Shift+Space / Home, follow
      that turns the page; `--midi-out` on `seq gui`; `seq play` headless
- [x] Don't fire audition while transport is rolling — `Transport.audition`
      checks `sink.rolling`; deleting makes no sound
- [ ] Open: the running schedule is not rewritten by edits during playback —
      stop and play again. A live-updated schedule is a later feature.
- [ ] Open: Windows ignores `timeBeginPeriod` for a minimised window, so a
      minimised editor can wake ~15 ms late. Nothing to do in-process.
- [ ] Open: no loop / cycle range, no metronome, no record from a MIDI input
- [ ] **Test — for Matthew, ears:** does it sound right, is the latency OK?
      `seq gui examples/bluesy.mid -t 2 -b 9`, Space. Then `seq play` a file
      of yours from the console. Then click squares with Audition on.

## Step 6 — TUI

- [ ] Textual app reusing `core/render.py` and `core/glyphs.py`
- [ ] `:` palette
- [ ] **Test:** keyboard-only editing

## Step 7 — the good stuff

- [x] Transformations exist as commands already (transpose ×2, invert, retrograde,
      augment/diminish, quantise, snap to scale) — they just have no UI yet
- [ ] Scale filter, drum name rows for ch10 (names already in `render.GM_DRUMS`)
- [ ] Humanise
- [ ] Beginner profile — solfège labels, vocal-range shading
