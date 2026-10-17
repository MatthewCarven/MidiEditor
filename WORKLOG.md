# Midi Sequencer — Worklog

## 2026-08-15 — Design session

Planned the whole thing end to end with Claude. No code written, by request.
Output: [PLAN.md](PLAN.md), [TODO.md](TODO.md).

### Decided

- **Two-character cell grammar.** Char 1 = the event (`·` `o` `x` `X` `─` `,`),
  char 2 = the tie (`─` carries, `·` releases). Note length comes free, with no second
  display mode and no ambiguity between adjacent notes.
- **Python confirmed** — mido + python-rtmidi, no better option in any language.
- **`.mid` is the only save format.** Killed the custom canonical format; `.seq` demoted
  to a view. Removed a lot of round-trip complexity.
- **Passthrough bucket** for unmodelled MIDI events — build it day one, not later.
- **Middle C = C4 (scientific) shipped, C3 as personal setting.** There's no MIDI standard
  here; the spec only has note numbers. Header declares the convention per file.
- **Velocity lane stays**, dynamics-labelled and editable, default velocity ~80 not 127.
- **Note-set selection, no tool modes.** Click empty inserts and plays. Plain drag moves,
  Shift+drag marquees, right-click deletes, Ctrl ignores snap.
- **Audition on drop** — fires the existing "play selection" command, not a special case.
- **Menu bar for browsing + command palette for speed**, both from one command registry.
- Command objects (built for undo) turn out to *be* the function set.

### Notable turns

- Started at "select-only default, keyboard writes" and Matthew corrected it to
  FL-Studio-style click-empty-to-insert. His version is better and it's the one that's
  in the plan.
- The design principle that fell out of two separate arguments: **the interface should
  teach you what it can do by being pokeable.**
- Named the transpose trap: chromatic and diatonic transpose are two different
  operations and must never share a menu entry.

### Later same day — GUI toolkit

**PySide6.** LGPL via pip, so nothing constrains shipping later. Two consequences that
changed the plan:

- `core/render.py` now produces a **cell-state matrix**, not glyph rows. Glyphs move to
  `core/glyphs.py`, used by the TUI and text view only — the GUI paints rectangles from
  the same matrix. Both UIs still agree by construction, but the GUI isn't forced to
  pretend it's a terminal.
- Each command object maps to one `QAction`, which carries its own menu entry, keyboard
  shortcut and palette entry. "One class, four doors" stops being an aspiration and
  becomes a Qt idiom.

Grid widget will sit on `QAbstractScrollArea` with a virtualised paint — only cells in
the visible rect get drawn, so speed doesn't depend on song length.

### Later same day — step 1 built

`core/` skeleton done, 50 tests passing, `seq cat` renders the PLAN.md mockup from
a real `.mid`. No GUI yet — that's step 3.

Pulled the **read side** of `io_midi.py` forward from step 2: the step-1 test point
is "cat a real .mid", which needs a loader. The passthrough bucket is populated now
so there's nothing to retrofit when the writer lands.

**Four things the plan had wrong, found by building it:**

1. **Tie semantics.** The last cell of a note was carrying when it must always
   release — the tie means "still sounding as the next cell begins", and the last
   cell is by definition where the note stops. Caught by a test, not by reading.
   `fills_last_cell` survives on `CellSpan` for the GUI to draw sub-cell-accurate
   rectangles.
2. **The `~` off-grid flag** now fires on an off-grid *start* only. An off-grid end
   is already told honestly by the tie character, so flagging both meant every
   ordinary detached note raised the warning and it stopped meaning anything.
3. **Velocity bands** realigned to soft <56 / medium <100 / loud ≥100. Equal thirds
   of 1-127 put the default velocity of 80 within a hair of "loud", so almost
   everything rendered as `X` — the same mistake as defaulting velocity to 127,
   one layer up.
4. **`cells_per_beat` depends on the meter**, not just the zoom. In 6/8 a beat is
   an eighth, so 1/16 zoom gives two cells, not four. Zoom alone mislabelled the
   ruler in compound time. Also: zoom coarser than 1/4 is now refused, since it
   would put more than one beat in a cell and the ruler couldn't label it.

The demo file exercises the interesting cases deliberately: a meter change to 3/4
at bar 11, a note landing 37 ticks late so the `~` flag has something to say, and
a controller sweep so the passthrough bucket has real data to hold.

Nice moment: at 1/8 zoom the two staccato sixteenths in bar 10 collapse into one
visible cell and the row raises `~`. Zooming to 1/16 makes it honest again. That's
the off-grid rule working exactly as designed, without anyone having to code the
case specially.

### Later same day — step 2 stopped before it started

Matthew called a halt before the writer: he hadn't finished reading the docs and
step 1's test point (open `examples/bluesy.mid` in a DAW, check the grid agrees)
was still outstanding. Right call — see the bug below, which is a *reader* bug.

**Decided: `seq build` patches by default, rebuilds only when there's no source.**

Editing a `.seq` in vim and building it back is lossy if the text is treated as the
whole truth: the glyphs only carry three velocity bands, and only the printed bars
and rows exist at all. So `seq build edit.seq -i song.mid -o out.mid` renders the
source, diffs the text against it, and **edits only the cells that actually differ**.
Untouched notes keep their exact velocity and their off-grid timing; unprinted bars,
unprinted tracks and the passthrough bucket are never touched.

With no `-i`, it builds a fresh file from the text alone — velocities come from the
header's band values. That keeps console-only composing from a blank file possible,
which was one of the points of the text mode.

This is the passthrough rule one layer up: **only what you actually changed changes.**

### Bug found: phantom default tempo/meter at tick 0

`Song`'s `default_factory` seeds `tempo_map` with `DEFAULT_TEMPO` (120 bpm @ 0) and
`timesig_map` with `DEFAULT_TIMESIG` (4/4 @ 0). The loader appends what the file
actually contains and never clears the seed, so every loaded song carries an event
at tick 0 that isn't in the file.

`normalise()` doesn't catch it — it only inserts a default when one is *missing*.

Visible now: `seq stat examples/bluesy.mid` reports `1 tempo change(s) · 2 meter
change(s)`. The file has one tempo and two meters, i.e. **zero** tempo changes and
**one** meter change. Both counts are one too high.

Not visible in the grid, by luck: `tempo_at`/`timesig_at` take the *last* entry at
or before the tick, so the real value always wins over the phantom.

**Why it had to be found before the writer.** Writing the map back would emit
120 bpm at tick 0 and then 96 bpm at tick 0 — inventing a tempo event the file never
had, and a DAW reading the first one shows the wrong tempo. That breaks the standing
guarantee that opening and saving an unedited file gives back the same music. It
would also have looked like a writer bug and been debugged in the wrong file.

Fix: drop the seeds on load rather than at construction — a `Song` built from
nothing still wants sensible defaults, a `Song` built from a file wants the file's.

### Later same day — what a DAW comparison is actually for

Matthew, on being warned FL Studio might drop the 3/4 change at bar 11: *"I don't
want to break the ability to go custom here, that is kind of the point of having our
own reference design."* Correct, and it's now a rule in PLAN.md §10 — **match DAW
conventions, don't inherit DAW limitations.**

The distinction that dissolves the tension: two very different things both look like
"the DAW disagrees with us".

1. **We disagree about what's in the file.** Ours to fix; a mature DAW is the
   better-tested reader and we assume we're wrong first.
2. **The DAW can't represent something the file legitimately contains.** Not a
   disagreement about the file at all — a missing capability at the other end.

**The arbiter is the file, not the DAW.** `mido` reads the raw bytes on demand and
both readings get judged against those. No need to decide who to trust in the
abstract.

Conforming to a limitation would repeat a mistake this design already refused: the
`~` flag exists because we won't silently quantise. Dropping something real because
a popular tool can't show it is the same silent lie, one level up.

Conventions we *do* follow, as options rather than defaults: FL's middle C = C5
(`--middle-c c5`, already built), and preserving the file's PPQ rather than
normalising to ours.

Matthew's plan is to test FL 2026 rather than assume the old behaviour — right
instinct, and 2026 is current (2026.1.3, July 2026). Either result is useful: if it
handles the meter change we get a full cross-check on the demo file; if it doesn't
we have evidence for an interop note, and possibly a "this file has meter changes,
some DAWs drop them" warning on export later.

**Warned:** don't save or export `bluesy.mid` from FL. If FL does drop the meter
change, the reference file stops being a reference. Open, look, close.

## 2026-08-16 — Step 1 test PASSED against FL Studio 2026

Matthew imported `examples/bluesy.mid` into FL Studio 2026 and sent screenshots.
**The grid agrees.** Step 1 is signed off.

### FL 2026 handles mid-song meter changes

The thing we were braced for turned out fine. FL's import dialog has an **"Import
time signatures"** checkbox, it was on, and it worked: the piano roll ruler carries a
`3/4` marker at bar 11, and the bar is visibly narrower.

Measured off the ruler pixels:

| bar | width | ratio to bar 10 |
|---|---|---|
| 9 | 243 px | 1.021 |
| 10 | 238 px | 1.000 |
| 11 | **183 px** | **0.769** |

3/4 as a fraction of 4/4 is 0.750. Bar 11 is short by exactly the right amount.

So the interop worry is retired, and the "warn on export about meter changes" idea
is unnecessary for FL at least. The PLAN.md §10 rule stands regardless — it was
always about *how to judge* a disagreement, not about this one file.

### Every pitch row confirmed

FL's keyboard labels C5 and C6 give a scale of 18.1 px per semitone. Predicting each
row from that and comparing against the note blocks:

| note (FL's C5 naming) | predicted y | observed | error |
|---|---|---|---|
| C5 | 623.0 | 627 | 4.0 |
| D5 (Pad) | 586.8 | 591 | 4.2 |
| D#5 | 568.8 | 573 | 4.2 |
| E5 | 550.7 | 555 | 4.3 |
| F5 | 532.6 | 537 | 4.4 |
| G5 | 496.4 | 500 | 3.6 |
| A5 | 460.2 | 465 | 4.8 |

The error is a **constant** ~4 px across the whole range — label baseline versus note
block top. A constant offset means the *spacing* is exact; a reading error would
have grown with distance from the reference.

### Everything else that checked out

- Tempo reads **96.000** in the transport.
- Channel rack has **Pad** and **Rhodes**, in file order. The Conductor track has no
  notes so FL made no channel for it — correct, not a loss.
- Pad's single note is **D5** in FL, which is our D4. Confirms the C4/C5 offset is
  the only difference in naming, and that it's purely a label.
- All ten Rhodes notes land where the grid puts them, including the **C+E dyad** on
  bar 9 beat 1, the **staccato pair** at bar 10 beat 2½ and 2¾, and the **F5** in
  bar 11.
- Velocity lane shows real variation, with the tallest spike on the bar 9 beat 2 G5 —
  our loudest note at 104, the one that renders as `X`.
- "Realign events" was **off** in the import dialog, which is what we wanted: nothing
  got nudged on the way in.

### Next

Step 2 is unblocked. One fix first: the phantom default tempo/meter seed at tick 0,
which would otherwise make the writer invent a 120 bpm event. Then the `.mid` writer,
the `.seq` reader, and `seq build` in its two modes.

## 2026-08-16 — Step 2 built: the writer, the reader, and the diff

127 tests pass (77 new). `bluesy.mid` and a battery of deliberately awkward files
round-trip with nothing lost and nothing invented.

### What went in

- **`io_midi.save`** — the write side. Passthrough and notes merged back in tick
  order, PPQ / format / track order / track names taken from the song rather than
  from our preferences.
- **`io_midi.musical_content`** — what the round-trip tests actually compare.
  Diffing bytes would fail on three things that aren't changes: mido re-encodes,
  `note_on` velocity 0 is a `note_off`, and same-tick ordering of unrelated events
  isn't information. The guarantee is about what a file *says*.
- **`io_seq.py`** — the `.seq` reader, and the diff that makes reading it safe.
- **`seq build`** — patch mode and standalone mode, as decided.
- **`seq check`** — throw files at it, see what survives. This is the step-2 test
  point turned into a command rather than a procedure.
- **Format 0 channel splitting**, finished.

### Five things building it proved wrong

1. **The phantom seed had a twin, and it was worse.** `normalise()` also
   guaranteed an entry at tick 0, inserting `DEFAULT_TEMPO` whenever the map
   didn't start there. Exactly the same invent-an-event mistake in a second
   place — and this one fired on any file that declares no tempo, writing a
   120 bpm event into a file that never had one. Found by a test named for the
   *first* bug. The default now lives in `tempo_at`/`timesig_at`: the map holds
   what the file said, the lookup knows the MIDI default. That's the better shape
   anyway — a default belongs in the reading, not in the data.
2. **`end_of_track` was in the passthrough bucket.** It's a delimiter, not data.
   Written back mid-track, everything after it vanishes on the next read. But its
   *position* is real — it's how a file says "four empty bars after the last
   note" — so it became `Track.end_tick_hint`, dropped on load and re-made on
   save. The bucket is for things we don't understand, not for things that aren't
   content.
3. **Same-tick ordering is correctness.** A pitch re-struck on the beat the
   previous one ends came back as *one* note: the release got written after the
   new attack and killed it. `note_off` before `note_on`, and everything else
   before `note_on` as well, so a CC or program change on the same tick is in
   force for the note it was meant for.
4. **A meter change is in the `.seq` after all.** The bar header already prints
   the signature wherever it changes — `bar 11 (3/4)` — so the reader can take it
   back off the page. Missed at first, which flattened bar 11 to 4/4 in a
   text-only build and put every bar after it in the wrong place. The picture was
   already telling the truth; the parser just wasn't listening.
5. **Format 0 had to split before pairing.** The old `_split_by_channel` stub
   couldn't ever have worked — once note-ons are matched to note-offs the channel
   is gone. Splitting the raw message stream first makes it trivial.

### The band values

The header now declares `bands o=40 x=80 X=112`, as PLAN.md always said it should.
`x` is 80 — `DEFAULT_VELOCITY` — rather than the 88 in the plan's sketch, so a note
typed into a text file comes out *identical* to one clicked into the grid instead
of a near-miss. The other two sit clear of the band edges so a rebuilt file
re-renders with the glyphs it had.

### Where the design earned its keep

The patch-not-rebuild decision is visible in one test: blank a single cell, and
the F4 that sits 37 ticks late still sits 37 ticks late, while velocities of 104,
92, 88, 84, 76 and 64 — six values the three glyph bands cannot tell apart — all
survive untouched. They survive because they are never rewritten at all. A
rebuild-from-text would have collapsed every one of them to 80.

The velocity lane is the one place in the picture that shows the loss, which is
why there's a test asserting it *does* differ after a text-only rebuild. It's the
visible argument for `-i`.

### Next

Step 3: the GUI shell. `QAbstractScrollArea`, virtualised paint, no editing —
readability gets judged before interaction is committed to.

### Later same day — the adversarial pass, and ten more bugs

The five above were found by *building* step 2. Then a subagent was pointed at the
finished code with one instruction: break the two promises. It broke them ten
times in about fifteen minutes. All ten are fixed and each has a regression test;
127 tests now.

Worth recording because the ratio is the lesson: **building the feature found five
bugs, attacking it found ten** — and the attack found the worse ones. Every single
one of these was silent.

**Silently wrong music:**

1. **A `program_change` for another channel could move a whole part.** The track's
   channel was taken from whichever channel-bearing message arrived first. A GM
   file that sets up the drum kit on channel 10 before playing a piano on channel
   1 opened as a piano solo and *saved as a drum solo*. The channel now comes from
   the track's notes.
2. **A track with notes on more than one channel collapsed to one.** `Track` has a
   single channel and format 1 is never split, so a 16-channel track came back
   with all sixteen parts on channel 1. Notes now carry their own channel, `None`
   meaning "the track's" — so editing still means what it meant.
3. **A format 0 split deleted every event on a channel with no notes.** The split
   bucketed by channels that *sound*, so a volume CC on an otherwise silent
   channel belonged to no track and vanished. Precisely the loss the passthrough
   bucket exists to prevent, reintroduced one layer up. Only visible once a file
   had two sounding channels, so it hid on every simple test.
4. **Drum tracks couldn't be read back at all**, for two unrelated reasons. The
   header wrote `drums` as a bare word the keyword/value parser couldn't see, so
   every GM name was read as a pitch name and dropped. And the ascii bar separator
   is `H` — which is inside twelve GM names including all three hi-hats — so
   scanning a row for a separator cut it inside its own label. The gutter is now
   measured off the ruler line, which is exact and has no such trap.
5. **Narrow bars lost their number, and a long number lost a digit.** A 2/4 bar at
   1/4 zoom is five characters and " bar 9 " is seven, so the header printed
   `┌ bar ┬ bar ┐` and nothing could read it. Worse: a four-digit number truncated
   to three still *parsed*, aiming an edit at bar 123 instead of bar 1234. The
   header now shortens the words and never the number, and the reader
   cross-checks the header's bar numbers against the bars that actually rendered.

**Quietly lost data:**

6. **Zero-length notes grew by a tick.** Real exporters write them. The fix needed
   the one exception to the note-off-before-note-on rule: a zero-length note's
   release must follow its own attack, or it's released before it sounds.
7. **Format 2 was written wrong rather than refused.** Independent patterns each
   have a tempo at their own tick 0; the model pools tempo song-wide, so saving
   collapsed every pattern onto the last tempo declared. Now refused with a
   sentence explaining why, and an offer of format 1.
8. **A tempo restated at a section boundary was deleted.** `_dedupe` collapsed
   repeats by *value*; it should only collapse by *tick*. The value rule existed
   to handle a format 1 file restating 4/4 in two tracks — both at tick 0, so
   deduping by tick covers it and the invented rule was pure loss. `seq check` was
   reporting a failure on its own correct output.
9. **Release velocity was forced to 0, and a time signature's `clocks_per_click`
   was rebuilt at the default** — changing a 6/8 file's metronome from a dotted
   quarter to a quarter. Both were invisible to `seq check`, because
   `musical_content` didn't record either. *The checker was blind in exactly the
   places the writer was lossy*, which is the most uncomfortable of the ten:
   a green test suite was evidence of nothing there.
10. **A duplicated pitch row silently discarded the first one's edits** (a dict
    assignment, second copy wins), and **a hand-broken header field escaped as a
    traceback** instead of a message. Both now refuse, loudly.

The one that should change how the next step is tested is #9. A round-trip checker
that doesn't record a field can't report losing it, and every test that leans on
the checker inherits the blind spot. When step 3 lands, ask what the test *can't*
see before asking whether it passes.

### Later same day — scale, and Windows

Matthew has **20,000+ old MIDI files** and asked whether the full feedback was
wanted. Yes — but not as 20,000 console lines. Three changes so a library that
size is usable, and one more bug.

**Windows doesn't expand wildcards.** His first guess was
`python seq.py .\midi\*.mid`, and the wildcard half of that would have failed:
cmd and PowerShell hand the literal string to the program. A Unix-developed tool
would have printed "no such file: .\midi\*.mid" while working perfectly. `seq`
now expands wildcards itself, walks folders recursively, and de-duplicates. Also
added `midiseq/__main__.py` so `python -m midiseq` works when pip's `Scripts`
directory isn't on PATH — "the command doesn't exist" is a bad first experience of
a tool that's fine.

**Past 200 files, report problem *kinds*, not files.** Twenty thousand files hit
the same handful of bugs thousands of times; the hundredth example teaches
nothing. `--report FILE.json` groups by kind with up to six example paths each —
about a kilobyte for a clean library, where the console output would be twenty
thousand lines. `-j N` for parallel: measured 2,000 files in 43 s serially and
22 s at `-j 4`, so 20,000 is roughly seven minutes or half that.

**"Declined on purpose" is not "we broke".** Format 2 is refused by design, and
across a big library that has to land in a different bucket from a real failure —
one is a footnote, the other is the bug list. It raises `io_midi.Unsupported` now
so a caller can tell them apart without reading a message string.

**Bug: SMPTE time division.** A MIDI header can declare absolute frames instead of
ticks-per-quarter-note, which mido reports as a *negative* `ticks_per_beat`. Old
sequencers wrote these and a 20,000-file library will have some. They round-trip
perfectly — the bytes never needed interpreting — but the grid took the negative
number as a PPQ, computed a **negative cell width**, and died three layers down
with "degenerate time signature 4/4 at tick 0". The honest answer isn't to coerce
it: the grid's horizontal axis *is* musical time, and a file with no quarter note
has no bars to draw. `Song.is_smpte` now says so and `GridView` refuses with a
sentence that explains itself, while `seq check` and `seq stat` carry on working.

**The survey is a free by-product and worth keeping.** Every checked file
contributes its format, PPQ, meters, channels and passthrough event types to a
histogram in the report. A 20,000-file run will say what real MIDI in the wild
actually looks like — which is what steps 3 onwards should be built against
rather than guessed at. Cheap to collect while we're already parsing everything.

135 tests.

### Open

- Beginner/child profile — scoped, not committed.
- `.seq` can't express a *tempo* change (only the initial tempo is in the header).
  Harmless while patching, since the source keeps it; a text-only build flattens
  it. Fixable the same way the meter change was, if it ever matters.

### Next

Steps 1 and 2 both signed off by build; step 2's test point is Matthew's to run
(`seq check` on his own files, and a text edit round-tripped through FL). Then
step 3: the GUI shell.

## 2026-10-18 — Status check, two months on, and the toolkit question re-opened

Matthew asked where we were up to, what language this is (Python — it matches
PySynthRack), what could be borrowed from PySynthRack, and whether the GUI
should be DearPyGui rather than PySide6. A planning session; no code changed.

### Where it stands

- 135 tests pass on the system Python 3.14.4, in 0.4 s. `mido` 1.3.3 is
  there; `python-rtmidi` isn't, but 1.5.8 resolves for 3.14 under
  `pip install --dry-run`, so step 5 isn't blocked. PySide6 6.11.1, DearPyGui
  2.3.1 and Textual 0.71.0 are all already installed on 3.14.
- Steps 1 and 2 are signed off. Nothing has moved since 2026-08-16; `ui_gui/`
  and `ui_tui/` are empty packages.
- **Not a git repo** — flagged for Matthew. The `midis/` folder (1.3 GB, about
  40,000 files) inside the project was a compatibility test for `seq check`;
  he'll move it out before `git init` when asked. Asked.
- The only MIDI output port on this machine is `Microsoft GS Wavetable Synth 0`.
  No virtual-port driver (loopMIDI), no hardware synth.

### Step 2's test point, finally run

`seq check -j 4` over the console and old-collection folders — 1,595 files,
3.4 M notes, report in the session scratchpad. 89.3% came back identical,
2.9% (47 files) lost or invented something, 7.7% (123) couldn't be read.

Two round-trip bug kinds, both silent, both ours:

- **note invented** — 32 files; the six examples are all Ecco the Dolphin.
  Something in the pairing manufactures a note the file doesn't have. Open
  one with `-v` and diff the event streams.
- **tempo/meter event lost** — 15 files (Landstalker, Sonic 1, Illusion of
  Gaia, Super Mario Kart). Cause unknown; `_dedupe` is the first suspect.

The 123 unreadable are mido refusing the bytes, not us: 45 "MThd not found"
(the Sonic examples start `$\x1a\x9c\x92` — not SMF at all), 62 "data byte
must be in range 0..127" (valid headers, e.g. Descent's 15-track format 1 at
192 PPQ — the old-game running-status / SysEx quirks mido is strict about),
15 EOFError (The Settlers 2, format 0 at 255 PPQ, truncated tracks), and one
key signature with 6 flats and mode 255. A lenient reader for the 62 is a
possible later item, in the spirit of the thing; it isn't a writer bug.

The survey earned its keep even on a throwaway folder: 120 PPQ is the
commonest in this corpus, not 480; 1/4 is the second commonest meter (59
files — one-beat bars, worth a grid test); `midi_port`, `sequencer_specific`
and `channel_prefix` are everyday passthrough events. The two VGMusic folders
(38,601 files) were still running at the end of the session.

### The toolkit question

PLAN.md §15 and the 2026-08-15 decision picked PySide6. Matthew is now asking
about DearPyGui, which PySynthRack is built in — 7.7 k lines of `ui/app.py`,
with zoom, window persistence, scroll-to-adjust and a mocked-dpg test idiom
already worked out. Nothing in `core/` cares and no GUI code exists, so the
decision is free to change today and expensive after step 3. Recommended in
chat: DearPyGui, after a one-session grid spike (render `bluesy.mid`'s cell
matrix in a drawlist, virtualised, scroll + zoom + hover hit-test, measure
frame time on a long file). His call; PLAN.md and TODO.md flip once made.

### What PySynthRack can lend, noted for when it's decided

- `modules/midiinput.py` — the mido port-handling pattern (enumerate, open
  idempotently, callback thread, lock + snapshot) is the mirror of what
  `audio/sink_rtmidi.py` needs on the output side. PySynthRack's own
  keep-list has `midi_output` as the same mirror.
- `ui/zoom.py`, `ui/window_geometry.py`, `ui/param_scroll.py`, `settings.py`
  — small and dpg-flavoured; borrowable if DPG is chosen.
- `_crash.py` + `error_handler.py` — crash logging, drop-in.
- `pysynthrack.spec` / `build.ps1` — PyInstaller packaging, for later.
- Sound, three ways to put the editor through the synth, cheapest first: a
  MIDI cable through a loopMIDI virtual port into `midi_input` (zero code,
  one driver install; `midi_input`'s channel filter already makes one patch
  multi-timbral); a `midi_file_player` Source module in PySynthRack (plays
  the library with no editor involved); an in-process sink driving
  `MIDIInput.note_on/off` directly (tightest timing, most coupling).

### Proposed order, revised (awaiting Matthew)

Housekeeping (move `midis/`, `git init`, rtmidi) → toolkit spike → step 3 →
**step 5 pulled before step 4**, because "click a square and hear it" is the
design principle and editing should be judged with ears, not mute → step 4 →
steps 6 and 7 → PySynthRack's `midi_file_player` in its own sessions.

### Later same day — DearPyGui decided, and the spike says go

Matthew chose DearPyGui ("DearPyGui it is") and warned about PySynthRack's
zoom bug: adjusting the zoom resizes the widgets, which adjusts the zoom
again. Its `_apply_zoom` scales the *global font*, so the slider you zoom with
grows under the pointer. Logged in PySynthRack's TODO (its commit ee1807e)
with the hypotheses to check; the rule carried here is **zoom scales the
drawing, never the widgets**.

Flipped PLAN.md §13/§15, the README, `pyproject.toml` (`gui =
["dearpygui>=2.0"]`), the TODO and four docstrings. `core/` untouched, as
promised.

**The spike:** `tools/spike_dpg_grid.py` — one drawlist, two layers (grid and
hover), virtualised twice (render.py builds only the visible bars and pitch
rows; only those cells are drawn), notes painted as spans with a head mark,
accidental shading, bar and beat lines, the `~` flag, a velocity lane, hover
hit-testing with the status line naming the cell. `--bench N` scrolls two
cells every frame, changes cell width and musical zoom on a schedule, and
prints the two clocks separately.

| file | render.py p50 / p95 / max | dpg draw p50 / p95 / max | items p50 | fps p50 / min |
|---|---|---|---|---|
| bluesy.mid | 4.7 / 8.4 / 22.8 ms | 4.2 / 5.6 / 6.1 ms | 123 | 77 / 51 |
| Tubular Bells, spans | 7.8 / 14.0 / 32.0 ms | 5.0 / 13.5 / 17.4 ms | 212 | 67 / 39 |
| Tubular Bells, per-cell | 7.2 / 12.6 / 32.0 ms | 6.0 / 14.2 / 21.4 ms | 318 | 65 / 46 |

Tubular Bells is 1,087 bars, 37,317 notes, 38 tracks — the longest file in
the old collection. Viewport 1280×760, 14 px cells, 47 rows × 96 columns per
paint. That is repainting on *every* frame while scrolling, which the real
widget never does.

**Verdict: go.** The toolkit is not the cost. DearPyGui draws a screenful in
about five milliseconds and a thousand items in about twenty. The larger
number is ours: on the big file `render.py` spends more than the drawing,
because `notes_in_cell_range` scans every note of every track on every
repaint and ghosts multiply that by the track count. Sort-and-bisect or bar
buckets fix it; it's in the step-3 list. Spans versus per-cell made no
measurable difference at this density, but spans are still right — a note is
one thing.

Screenshots (`--screenshot`, via `dpg.output_frame_buffer`) checked against
the FL Studio pass of 2026-08-16: the dyad, the loud `X` G4 in red, the
staccato pair, the off-grid F4 with its `~`, the narrower 3/4 bar 11, the
pad's D4 as a grey ghost — all where they should be. Two spike-only
blemishes: the lane and status line were clipped (viewport height versus
client height — fixed), and the meter is printed on every non-4/4 bar rather
than only where it changes.

Two environment facts, recorded in the notebook: `dpg.get_dearpygui_version()`
segfaults before `create_context()`; and the Bash tool's 600 s timeout does
*not* apply to a background call — the VGMusic `seq check` has run for over
twenty-five minutes without being cut.

### Next

Matthew: move `midis/` out, `git init`, and run the spike for the eyeball.
Then step 3 proper: the grid widget with a dpg-free paint-plan layer, the
note index, and the rest of the step-3 list.

### Later — the VGMusic run, stopped

Killed at 13,600 of 38,601 files after forty minutes so Matthew could move
`midis/` out: he'd said to ignore the folder, and the first 1,595 files had
already surfaced the bug kinds. Nothing was lost but the run — the report is
only written at the end. Two chess engines were at High priority at the time,
but our six workers were each still about 80 % busy, so the real cost is
`check_one`'s quadratic diff (`x not in after[key]` over lists, per field):
about a second per file with a few thousand notes. Logged in the TODO; a
`Counter` multiset diff is the fix.

## 2026-10-18 — Step 3 built: the window

Matthew moved `midis/` out and ran `git init`; first commit 8d64db2 is the
project as it stood before any step-3 code, so the history starts clean.
Then the shell, in one session. 179 tests pass (32 new for the GUI layer,
none of which open a window).

### What went in

```
midiseq/
  core/index.py      NoteIndex / SongIndex — the notes touching [lo, hi)
  ui_gui/paint.py    Layout + plan(): matrix → Rect/Line/Label by *role*. No dpg.
  ui_gui/painter.py  DpgPainter: one primitive, one draw call. Takes dpg as an arg.
  ui_gui/grid.py     GridWidget: scroll, two zooms, track, ghosts, hover, status. No dpg.
  ui_gui/app.py      GridApp: window, File/View menus, keys, file dialog, frames.
  ui_gui/__main__.py python -m midiseq.ui_gui
  cli/seq.py         seq gui FILE [-t] [-z] [-b] [--no-ghosts] [--middle-c]
```

The boundary is the point. `paint.py` is tested the way `glyphs.py` is: load
`bluesy.mid`, lay out bars 9–14, and assert the picture — ten note rectangles
and ten heads, the dyad sharing a column, the loud G4 painted `note_loud` at
bar 9 beat 2, the C4 stopping short of its fourth cell's edge, the `~` on the
F4 row and nowhere else, bar 11 twelve cells wide against sixteen, nothing
painted past the right edge. A tripwire asserts the module imports no
toolkit. `painter.py` is tested with a recorder standing in for DearPyGui
(PySynthRack's mocked-dpg idiom): every primitive becomes exactly one call,
with the colour its role maps to; an unknown role paints magenta rather than
raising, so a typo is visible instead of fatal.

**The index.** Sorted starts plus the running maximum of end: `bisect_left`
on the ends for the first note whose prefix could reach `lo`, `bisect_left`
on the starts for the first at or past `hi`, exact test in between, results
put back in the track's own order so the picture is identical to the scan's
— including which of two overlapping notes wins a square. It keys on a new
`Track.version` (excluded from equality) that `Command._apply` bumps, which
is the one place notes change, so the GUI never invalidates anything by
hand. `GridView(song, zoom, index)` and `render(..., index=)` take it
optionally; `seq cat` carries on scanning.

**Found on the way: a zero-length note on a window's first tick was
invisible.** The scan's `end > lo` excluded it, which breaks "nothing ever
vanishes for being too short to draw" (PLAN.md §6). Now `sounds_in()` in
`index.py` is the one definition, used by the scan and the index both: a
zero-length note counts where it sits, `lo` included, and an inverted range
holds nothing. The index tests pin the index to that definition on random
songs with notes up to fifty bars long.

**`Cell.end_fill`** is new: the fraction of its last cell a note covers
(clamped to a tenth so a zero-length note keeps a sliver). The GUI stops the
rectangle there; the glyphs ignore it, because the tie already tells that
story honestly. `CellSpan.fills_last_cell` had promised this since step 1.

**The widget** keeps PySynthRack's lesson as structure: zoom is
`Layout.cell_w`, a number the plan reads. No font scale, no widget resize,
nothing to feed back. The window's size comes from the viewport's *client*
area (the viewport's own height includes the decorations and clipped the
lane in the spike). The musical zoom keeps the left edge on the same tick.
Hover is its own layer, so moving the mouse never repaints the grid.

### Decided

- **ASCII labels throughout.** DearPyGui's default font paints anything above
  U+00FF as `?`; `C#4` reads fine and the text view already has `--ascii`.
- **The meter is printed only where it changes** in the window (`11 (3/4)`,
  then `12`), and on the first visible bar when it isn't 4/4. The text view
  prints it on every bar; one of them should move. Open in the TODO.
- **The lane labels pp / p / mf / f**, as PLAN.md §2 draws them; six labels
  overlap at this height.

### Not yet

No scrollbars (the ruler is the only position cue — an overview strip is
the better idea), no playhead, no editing, no selection. Step 4 waits on
Matthew's eyes: `seq gui examples/bluesy.mid -t 2 -b 9`, then a real file of
his through File > Open.

### Eyes, first pass — the two zooms were one

Matthew, within minutes: *"Ctrl+wheel changes cell width, Ctrl+Shift+wheel
changes the musical zoom seems to be the same option?"* On his machine the
Shift wasn't seen alongside the Ctrl, so both gestures fell through to cell
width. I can't inject modifier-plus-wheel input from here to say whether
that's DearPyGui's key state during a wheel event or a mouse driver eating
the combination, and it doesn't matter: a gesture that needs two modifiers
on the wheel is fragile by construction. Split them. **Ctrl+wheel is cell
width; the musical zoom is `.` and `,`** — which PLAN.md §6 had assigned to
it from the start ("pressing `.` zooms finer until it lands true") — plus
Ctrl+= / Ctrl+- and the View menu, relabelled "Finer grid / Coarser grid" so
the two zooms stop sharing a word. One modifier per wheel gesture, never two,
is now a rule in the app's docstring. The lesson sits next to the zoom one:
the first thing eyes find is never the thing the tests covered.
