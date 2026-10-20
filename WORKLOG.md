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

## 2026-10-18 — Step 4 built: editing

Matthew: *"It reads right. Start step 4."* Same session. 222 tests pass (43
new, none opens a window).

### What went in

- **`ui_gui/editor.py`** — the mouse model from PLAN.md §11 as a small
  gesture state machine (`insert`, `move`, `resize`, `marquee`, `erase`),
  the selection as the one set the picture also reads, `History` for undo,
  `Transport` + the null sink for audition, and the registry's two doors:
  `menu_sections()` for the Notes menu, `edit_menu()`, `palette()`. Pointer
  events arrive in drawlist pixels; keys arrive as names with modifier
  flags. No DearPyGui anywhere in it.
- **`PROMPTS`** — which commands ask for a value and what to ask, as data:
  `(field, caption, default)` triples per registry key. The window builds
  one generic dialog from it; the editor parses the strings (`+5`, `A
  minor`, `G4`, `x0.8`) and turns a bad one into a sentence on the status
  line rather than a traceback.
- **`app.py`** — Edit and Notes menus generated from the registry (the
  Notes menu's two groups are headed by disabled items, so the separator
  teaches), the Ctrl+P palette (type `inv`, get Invert; Enter runs the first
  match), prompts, New / Save / Save as through `io_midi.save`, an
  unsaved-changes guard on New / Open / Quit, the gesture preview on its
  own draw layer, Undo/Redo labels that name the command.
- **`core/edit.py`** — `Augment` and `Diminish` as their own classes and
  registry entries. `Scale` set its label per instance, so the registry's
  entry for it read "command"; the menu couldn't be generated honestly
  without the split.
- **Selection in the picture** — `paint.plan` outlines a selected note's
  rectangle; `span_rect` and `tick_rect` are now the shared geometry for
  notes and for previews (sub-cell accurate, so an unsnapped drag shows
  where it will really land).

### Decisions, each one a line in the plan's table or a line it didn't have

- **Edge drag resizes.** Not in §11's table; added. The grab zone is the
  last five pixels of a note's rectangle, only when the note is wider than
  twice that, so a one-cell note still moves.
- **Inserting doesn't select.** Writing four notes in a row shouldn't leave
  only the last one selected. FL agrees.
- **A marquee adds to the set.** It starts with Shift, and Shift means add.
  Esc first if you wanted to replace.
- **An erase sweep is one undo**, committed on release; the swept notes are
  outlined red while the button is down so you can see what you're about to
  lose.
- **With a selection the arrows move the notes, not the view** — Up/Down a
  semitone, Shift+Up/Down a scale degree, Left/Right a cell. Without one
  they scroll as before. Q quantises the selection to the current grid
  (the keyboard is the fast path; the menu entry asks for strength).
- **Diatonic moves need a key**; C major until a prompt names one, and the
  Snap-to-scale and diatonic prompts both remember what they were told.
- **Deleting makes no sound**; inserting and dropping do. The audition
  toggle is `A` and a checkbox in the Edit menu.

### Not done, and said so in the TODO

The velocity lane is still a readout (PLAN §8 wants a drag). The window's X
button skips the unsaved-changes guard, because DearPyGui's close can't be
cancelled; Ctrl+Q and File > Quit ask. No clipboard yet.

### Next

Matthew's step-4 test point: write a bar by hand, save it, open it in FL,
say what fights. Then step 5 — the rtmidi sink, and everything that has
been auditioning silently starts making noise.

## 2026-10-18/19 — Step 5 built: sound

Matthew: *"Nothing fights. Start step 5"* — and, a minute later, *"whatever
else you want to knock through with agents."* So this step was built
differently: the contract first, by hand, then three agents in parallel
worktrees, then a review by agents, then two more features by an agent
while the review ran. The suite went from 222 to 363.

### The contract (by hand, commit 681fe74)

`core/transport.py` grew the rest of the clock. `schedule(song, start_tick)`
turns a song into raw MIDI bytes with the second each is due, walking the
tempo map: releases before restrikes on the same tick, set-up messages
before the notes they are for, a zero-length note's release *after* its own
attack (the same three rules the writer learned in step 2, now on the wire),
and when playing from the middle of a song a prelude at second zero carrying
the last program change, the last value of each controller and the last
pitch bend per channel, so the right instrument is in force from the first
note. `TempoClock` is the walk and its inverse; the inverse is what the
playhead reads. Two bugs found by writing it: `seconds_at` returned zero for
a file that declares no tempo (the empty-map case the step-2 fix moved out
of the data and into the lookup, missed here), and the inverse landed a tick
short when a float fell a billionth under an integer — a hair of slack.
The `Sink` protocol gained `start / stop / elapsed`; the null sink keeps
time with an injectable clock; `audio/ports.py` lists outputs and opens one,
preferring the GS Wavetable, falling back to silence *with a reason*.

python-rtmidi 1.5.8 has no wheel for 3.14 and built from source without a
word of complaint.

### Three agents, one worktree each, eleven minutes

Each got the contract, a file list that overlapped no one else's, and the
instruction to commit on its branch and not touch the docs. All three came
back green and merged without a conflict.

- **The sink** (`audio/sink_rtmidi.py`): one scheduler thread, a heap of
  absolute due times (no drift), an urgent deque for releases that a halt
  never clears, `pump(now)` so tests drive it with a fake clock and a fake
  port, and winmm `timeBeginPeriod(1)` while the thread lives — the agent
  measured `Condition.wait(3 ms)` waking 13 ms late at Windows' default
  timer and 0.5 ms with the period raised. Through the real GS port a
  note-on left 1.4 ms after it was due. Stop releases each sounding note
  individually (the GS synth honours per-note release envelopes); CC 123 is
  kept for panic.
- **The window** (`ui_gui/`): a fixed-size transport bar, the playhead on
  its own layer, click-the-ruler seek, Space / Shift+Space / Home, a page
  turn when the playhead passes the right edge, the output combo, the sink
  opened once and carried across New/Open, closed on quit. All of it in the
  dpg-free layers first, tested with coordinates and a fake clock.
- **The console** (`cli/seq.py`): `seq play FILE -b BAR --port --seconds`,
  and the quadratic diff in `seq check` replaced by a `Counter` multiset
  diff — 6,000 notes went from 460 ms to 4.5 ms with the same answer. The
  agent also found that `Transport.stop()` after a schedule had run out
  reported the *start* tick unless someone had asked for the position in
  between; fixed in `stop()` with a test.

`seq play examples/bluesy.mid -b 9 --seconds 3` through the GS Wavetable:
opened, printed `9:1.1  9:2.3  9:4.1`, stopped at `10:1.4`, exit 0. It made
a sound on Matthew's machine; whether it is the *right* sound is his ears'
job and the step-5 test point.

### While the review ran: the lane and the clipboard

A fourth agent, in a worktree, took PLAN §8's promise that the lane is
editable and the clipboard the step-4 notes said would be missed. Drag a bar;
sweep a ramp (it interpolates between mouse events, because a fast sweep
with sparse events would leave holes — the agent's call, and right);
Shift+drag a flat line; one `SetVelocities` per sweep, one undo. Ctrl+Up/Down
nudges by five. Cut / Copy / Paste / Duplicate, the clipboard as plain
tuples so note ids never collide, paste at the playhead as one `AddNotes`
that auditions as a phrase. 55 tests. Merged clean on top of the other three.

### The review, cut short

Four reviewer agents with one lens each (threads and stuck notes; the
arithmetic of time; the window's state; the console and ports) produced 24
findings. Three skeptics per finding were to vote; the session's usage limit
hit in the middle and 65 of those votes died, so only one finding has a
verdict: **the sink keeps sounding notes as a set**, so two overlapping notes
on one pitch and channel leave the second unreleased by Stop. Reproduced by
both surviving skeptics; inaudible on the GS Wavetable and FluidSynth, which
release the key on the first note-off, audible on a synth that counts
note-ons. A `Counter` per (channel, pitch) is the fix, with one test. The
other 23 are unvoted, not refuted — among the ones worth a look: the `|<`
button not bringing the view to bar 1 the way Home does, the output combo
keeping a port that failed to open, re-selecting the current port silencing
the app, and `seq check`'s new multiset diff possibly reporting the model's
own same-tick tempo dedupe as a loss. Matthew asked to stop there; the full
list is in the workflow's output. Triage by hand next session.

### Ears

2026-10-19, Matthew: *"working perfectly I must say."* Step 5's test point,
passed.

### The review, triaged by hand

2026-10-19. The twenty-three unvoted findings read against the code, no
agents, on a low token budget. Twenty were real and are fixed, some sharing a
fix, with a test each (376 tests now); three were refuted on reading.

Fixed:

- The prelude's stale set-up value went out *after* a set-up message the file
  has at the start tick (both at second zero, stable sort, prelude appended
  last). The prelude now goes in front.
- A schedule of nothing but the prelude counted as something to play, so
  `seq play -b` past the end "played" and Space at the end rolled for an
  instant. Notes alone count now, and the CLI's "nothing to play" is
  reachable.
- Space after a run-out nobody observed replayed from the previous start.
  `play()` takes its default from `position()`, which lands the playhead at
  the end; the editor says "Nothing to play from here" instead.
- `position()` read `rolling` and `elapsed()` under separate lock holds; a
  run-out between the two gave the start tick, and `stop()` would have kept
  it. It reads twice and clamps to the schedule's end.
- A 0 us/beat tempo divided by zero in `tick_at` and `bpm`. Clamped to 1.
- The scheduler thread could park forever: dropping the sink's last reference
  from inside the thread runs `__del__` there, after its wait was computed as
  None. The idle wait is bounded (0.5 s), so it re-checks the weakref.
- `start()` took its lead before an O(n) build; the build comes first now.
- `choose_port` took the first substring hit, so an exact name lost to an
  earlier port containing it. Exact first.
- With python-rtmidi missing, `open_sink` said "no MIDI output ports" and the
  install hint was dead code. The import is probed first; the hint shows.
- `seq check` reported the model's own same-tick tempo/meter dedupe as a
  loss. `musical_content` keeps the last word per tick, as the model does;
  four hand-made files went from 100% problems to 100% identical.
- `|<` did not bring the view to bar 1 the way Home does, so while rolling
  the playhead sat off-screen and `follow()` only pages forward.
- `follow()` yanked back a view you had scrolled away from, and a drag held
  across a page turn landed a page away. The editor follows only while the
  view is where the follower left it or the playhead is in it, never
  mid-drag.
- In the window: re-selecting the current port silenced the app (the port was
  opened a second time while still held; now a no-op), a port that failed to
  open stayed selected in the combo (it shows silent), and New/Open turned
  audition back on (the flag carries over).

Refuted: the urgent release lost when `send_message` raises (a dead port
cannot take it, and a retry would spin); the KeyboardInterrupt window between
`open_sink` and the CLI's `try` (microseconds, and `__del__` closes the
port); seeking while rolling into held notes "stopping" the transport
(nothing starts after the seek point - a seek that restarts the notes
sounding there, chase, is a feature, now in the TODO).

Method note: the notebook's heredoc rule bit again - a three-hundred-line
quoted heredoc with no backslash in it failed to parse in the Bash tool. The
patch went to the scratchpad as a file and ran from there, as the rule says.

## 2026-10-19 — Step 6 built: the terminal

`seq tui examples/bluesy.mid -b 9`: the same grid in a terminal, through
Textual 0.71, keyboard only. One side session, no agents, by hand.

### What went in

- `midiseq/ui_tui/view.py` — `TextView`, the terminal's `GridWidget`:
  scroll, zoom, track, ghosts, plus a cursor (a pitch and an absolute cell)
  that the view follows by the bar. `build()` renders the visible bars and
  rows through `core/render.py`, lays them out with `core/glyphs.py` - the
  picture `seq cat` prints, so the terminal and the text view agree by
  construction - slices the matrix to the columns that fit the width, and
  hands back a `Frame`: lines plus spans by role (note, selected, ghost,
  accidental, playhead, cursor). The cursor is in the text too (`▼` in
  the header, `>` in the gutter, both glyphs.py's), so a colourless
  terminal still reads.
- `midiseq/ui_tui/editor.py` — the keyboard model. Enter is a click:
  select and play a note, or insert one cell and play it. Everything that
  acts on notes acts on the selection, or else the note under the cursor.
  The arrows always move the cursor, so the window's selection-moving
  arrows wear Shift; the cursor follows a note it was on when the note
  moves. `frame()` is the window's without the pixels: reads the clock,
  turns the page. Save, Save as, a two-press quit with unsaved changes.
- `midiseq/ui_tui/palette.py` — the `:` line as a state machine: search,
  then one prompt per field, blank meaning the default, then `(key, raw)`
  for the editor to run. The entries, prompts and parsers are the window's
  (`ui_gui/editor.py`, toolkit-free), so the two front doors can't drift.
- `midiseq/ui_tui/app.py` — the only module that imports Textual: a pane
  that paints the frame as one Rich `Text`, a status line, two `Static`s
  for the palette, a 20 Hz timer for the playhead, Ctrl+C rebound from
  Textual's quit to Copy. `open_sink(--midi-out)` as the window; the
  silent reason stays in the status line.
- `seq tui` in `cli/seq.py`, the same options as `seq gui`.
- 76 tests: the view judged as text (width, cursor marks, page turns,
  zoom keeping the cursor's tick, the 3/4 bar, roles, the playhead
  column), the editor by key names (every key in the table, the target
  rule, the palette's questions and a bad answer coming back as a
  sentence, save, the two-press quit), the palette's state machine, five
  Textual Pilot runs headless (the pane paints at the terminal's size,
  keys reach the editor and Tab is ours not focus traversal, Ctrl+C
  copies, the palette shows, Ctrl+Q quits), and `seq tui` handing its
  options over. 453 in all, 3.5 s.

### Decisions

- **Enter inserts one cell, not one beat**, and `+`/`-` stretch it: the
  click's rule, so the two UIs teach the same thing. One beat would have
  saved three keys at 1/16 and taught a different grammar.
- **Arrows move the cursor; Shift moves notes.** The window has a mouse
  to navigate with and arrows to spare; the terminal doesn't. Chromatic on
  Shift+Up/Down, the scale degree on Ctrl+Shift+Up/Down - which in the
  window is velocity by one; the terminal has no key for that.
- **The target rule** (the selection, else the cursor note), so a single
  note is never selected first. The window says "nothing selected"; the
  terminal says "nothing selected, and no note under the cursor".
- **Paste lands at the cursor**, not the playhead: the keyboard's one
  position that is always yours.
- **The view follows the cursor by the bar**: the page turns to the
  cursor's bar, or to the cursor itself when a bar is wider than the screen
  (1/64 at 80 columns).
- **No Textual `Input` for the palette.** Every key routes through the
  grid pane, which stays focused; the `:` line is a `Static` drawn from the
  editor's state. One key path, no focus to manage, and the whole palette
  is tested without a terminal.
- **The TUI imports the window's toolkit-free modules** (`PROMPTS`, the
  entries, `parse_key`, `velocity_change`, `default_track`, `beats_text`)
  rather than copying them. A UI importing another UI's toolkit-free layer
  is new here; lifting those into a shared module is in the TODO.
- **Shrinking stops at one cell**: `ResizeNotes` would happily make a
  one-tick note, which the grid still draws and you still can't see.
- **S and Shift+P** double for Shift+Enter and Shift+Space, which some
  terminals deliver as plain Enter and Space.

### Not done, and in the TODO

New / Open in the terminal, an editable lane, an output chooser, a mouse,
and the lift of the shared palette model out of `ui_gui/`.

### Eyes

Mine, from the Code tab's terminal panel: `python -m midiseq tui
examples/bluesy.mid -b 9` opened on the Pad track (the first with notes,
as `seq cat` picks), drew bars 9 to 12 across 114 columns with the Rhodes
as ghosts, the lane, the `▼` and `>` cursor marks and the status line
ending in "Microsoft GS Wavetable Synth 0". No keys could be sent from
here, so the rest is Matthew's. The tab was closed again so it would not
hold the GS port.

What to try, Matthew (copy the example first - Ctrl+S writes the file you
opened):

    copy examples\bluesy.mid scratch.mid
    seq tui scratch.mid -t 2 -b 9

- The picture: is it `seq cat`'s? The cursor cell is reverse video, its
  column has `▼` in the header and its row `>` in the gutter; accidental
  rows are shaded; the playhead is a red column at bar 1.
- Arrows move the cursor; Ctrl+Left/Right a bar; walk off the right edge
  and the page should turn to the cursor's bar.
- Enter on an empty square: `x·` and a blip. `+` three times: `x──────·`.
  `-` back. Del removes it. Ctrl+Z, Ctrl+Y.
- Enter on the loud G4 at 9:2: it turns yellow and sounds. Shift+Up/Down,
  Shift+Left/Right, Ctrl+Shift+Up/Down, Ctrl+Up/Down - the cursor should
  ride along. Q on the `F4~` row at bar 11 straightens it.
- Ctrl+A, Shift+Up, Ctrl+Z.
- Space plays from bar 1 and the page turns when the red column leaves the
  screen; Esc stops. Shift+Space (or Shift+P) plays from the cursor; P
  parks the playhead there; Home rewinds.
- `:` then `inv` Enter Enter; `:` `tsd` Enter `2` Enter `A minor` Enter;
  `:` `go` Enter `11` Enter; Tab for the next track.
- Ctrl+S, then Ctrl+Q; or Ctrl+Q twice to discard.

What I could not check from here: whether Windows Terminal delivers
Shift+Enter, Shift+Space and Ctrl+Shift+arrows (S and Shift+P are the
spellings that don't need the first two), how the colours sit in your
theme, and how the playhead's 20 Hz repaint feels.

### Eyes, first pass — the chords the terminal keeps

Matthew, same day, in Windows Terminal: *"shift enter and enter seem to
do the same thing and shift space makes no difference to space."* The
limit flagged above, confirmed: a terminal only promises to deliver
characters, and Shift adds nothing to Enter or Space. Looked at the rest
of the table with that in mind: Ctrl+Shift+S and Ctrl+Shift+Z are the
same control characters as Ctrl+S and Ctrl+Z, so they never arrive
either; Windows Terminal binds Ctrl+V to its own paste and
Ctrl+Shift+Up/Down to scrolling, so those never reach the app at all.

So the terminal's keys are plain letters now, and the chords stay as
aliases for terminals that do deliver them: **S** adds to the set,
**Shift+P** plays from the cursor, **X / C / V / D** are cut, copy,
paste, duplicate, **[** and **]** move a scale degree down and up,
**Ctrl+Y** is redo, Save as lives in the palette, and **?** shows the
whole table in the terminal (the palette's "Keys" too). The table is one
list, `KEY_HELP` in `ui_tui/editor.py`, that `?` draws and the README
quotes. Ctrl+Up/Down and Shift+Tab are unconfirmed and in the TODO with
their fallbacks. Two tests more (the brackets, the table): 455.

### Ears, round two

2026-10-19, Matthew tried the four window changes: all work. Two calls from
him, both done the same hour. Space with nothing ahead of the playhead now
rewinds and plays ("do the |< for the user"), with "From the top" in the
status line. And switching the MIDI output while rolling, which used to stop
the transport (`set_sink` stopped first and never handed over the schedule),
now carries on from the position on the new sink; silent and back costs a
lead, not the song. The test that had pinned the old swap rule was rewritten
to the new one. 377 tests.

Confirmed by ear the same evening, Matthew: *"Working fine."* Both window
changes stand.

### On GitHub

2026-10-20. Matthew: "commit and push as you please." The repo is at
https://github.com/MatthewCarven/MidiEditor (public); master pushed with
everything through the gitignore tweak, and it tracks origin now. Pushes are
part of the routine on this project from here.

### Step 6 passed

2026-10-20, Matthew: *"the tui is in."* The terminal editor's gate, passed a
day after the Shift chords went to plain letters. Next, by his word: work
through the open TODO items, cheapest and surest first, and he will start
asking for specific things once the list is done.

### The lift

2026-10-20. The terminal had been importing the palette entries, `PROMPTS`,
`parse_key`, `velocity_change`, `default_track` and `beats_text` from the
window's package, and carried its own copy of `Editor.build` as a function.
That is the wrong way round: the terminal is not a kind of window. They
live in `midiseq/ui_common.py` now, toolkit-free, with the command builder
as the one function (`build_command`; the window's `Editor.build` is a
four-line wrapper that remembers the key signature). `ui_gui.editor` and
`ui_gui.grid` re-export the names they had, so nothing that imported them
moved. Done by a script that cut the definitions out by their `ast` spans
rather than by hand; the suite did not notice, which is the point. Four
layering tests pin it. 459 tests.

### Chase, and edits while rolling

2026-10-20. Two open items that are one change. `schedule()` used to skip
every note that began before the start tick, so a seek into a held chord
was silence until the next attack, and (the other item) an edit while
rolling could not be heard until you stopped and played again, because
the sink's schedule was built once. Now a note sounding at the start tick
restrikes there with what is left of it - chase, what a DAW does - and
`Transport.resync()` plays again from the position; both editors call it
after every run, undo and redo. A held note across an edit blinks for a
lead rather than dropping out. The one test that pinned "not restarted"
now pins the chase; four tests added. 463 tests.

### The X button, and the meter label

2026-10-20. DearPyGui has no cancellable close, so the window's X button
skipped the unsaved-changes question. It still cannot ask, but it no longer
loses anything: on the way out, unsaved work is written beside the song as
`name.autosave.mid` (`untitled.autosave.mid` in the working directory for a
song never saved), the console says so, and opening the song again flashes
a hint when a newer autosave sits next to it. `autosave_path` and
`autosave` live in `ui_common.py`, where the terminal can reach them too.

The text view labelled the meter on every bar whose meter differed from the
*first* bar's - so a song that goes to 3/4 at bar 11 said (3/4) on every bar
after - while the window labels it where it changes. The text view now does
the same: the first bar of a slice, and where it changes from the bar
before. One test added. 464 tests.
