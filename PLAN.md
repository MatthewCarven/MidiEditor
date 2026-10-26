# Midi Sequencer — Plan

A MIDI editor with the feel of a step sequencer. Notes run vertically (chromatic,
A–G plus sharps and flats), time runs horizontally, and a mark in a square means a
note starts there.

**Status:** planning only. No code written yet.
**Language:** Python — `mido` for the file format, `python-rtmidi` for live MIDI I/O.

---

## 1. The design principle

> **The interface should teach you what it can do by being pokeable.**

The velocity lane exists so people discover that quiet is possible. Clicking an empty
square and hearing the note exists so people discover what the grid *is* by prodding it.
Every later design idea gets checked against this.

---

## 2. The view

Bars 9–10 at 1/16 zoom, editing track 2, cursor on G4 beat 2:

```
    ┌─────────▼─── bar 9 ───────────────┬───────────── bar 10 ─────────────┐
    │1 e & a │2 e & a │3 e & a │4 e & a ║1 e & a │2 e & a │3 e & a │4 e & a ║
 C5 │········│········│········│········║········│········│········│········║
 B4 │········│········│········│········║········│········│········│········║
▒A#4│········│········│········│········║········│········│········│········║
 A4 │········│········│x──────·│········║········│········│········│········║
▒G#4│········│········│········│········║········│········│········│········║
>G4 │········│X──────·│········│········║········│····x·x·│········│········║
▒F#4│········│········│········│········║········│········│········│········║
 F4 │········│········│········│········║········│········│········│········║
 E4 │x──────·│········│········│········║········│········│········│········║
▒D#4│········│········│········│o──·····║········│········│········│········║
 D4 │,-------│--------│--------│--------║--------│--------│--------│-------·║
▒C#4│········│········│········│········║········│········│········│········║
 C4 │x──────·│········│········│········║x──────·│········│········│········║
 B3 │········│········│········│········║········│········│········│········║
────┼────────┼────────┼────────┼────────╫────────┼────────┼────────┼────────╢
 f  │        │█       │        │        ║        │        │        │        ║
 mf │█       │█       │█       │        ║█       │        │        │        ║
 p  │█       │█       │█       │█       ║█       │        │        │        ║
 pp │█       │█       │█       │█       ║█       │    █ █ │        │        ║
────┴────────┴────────┴────────┴────────╨────────┴────────┴────────┴────────╨
 G4 · MIDI 67 · 9:2.1 · len ♩ · vel 104 · trk 2 "Rhodes" ch1 · ghost: trk 3
```

`▒` marks accidental rows. In the real UIs that's a shaded background, so the black-key
pattern reads at a glance and you never miscount which row is F♯ and which is G.

The `,----` row on D4 is a **ghost** — a note on another track, visible for context,
not editable.

---

## 3. The cell grammar

Everything hangs off this. **Each square is two characters: what happens, and whether
sound carries into the next square.**

| char 1 — the event | | char 2 — the tie | |
|---|---|---|---|
| `·` | nothing | `·` | silence into next cell |
| `o` | note on, soft | `─` | sound continues into next cell |
| `x` | note on, medium | | |
| `X` | note on, loud | | |
| `─` | note continuing | | |
| `,` | ghost note (other track, read-only) | | |

Which gives you, one beat at a time at 1/16 zoom:

```
  x──────·   quarter note, fills the beat
  x──·x──·   two eighths
  x·x·x·x·   four staccato sixteenths
  x·······   one sixteenth, then silence
  ────────   a longer note passing through
  x───────   a note tying over into the next beat
```

Char 1 is the original idea — a dot or an x at the beginning of the square. Char 2 falls
out of it: if a square has a beginning it has a remainder, so the tie encodes note length
for free, with no second display mode and no ambiguity when notes butt up against each
other.

---

## 4. Zoom

The grammar doesn't change with zoom, only how many cells a beat gets. Same G4 quarter
note at three levels:

```
 1/4    │1 │2 │3 │4 ║        G4  │··│X·│··│··║
 1/8    │1 & │2 & │3 & │4 & ║    G4  │····│X──·│····│····║
 1/16   │1 e & a │2 e & a │…      G4  │········│X──────·│…
```

---

## 5. Meter and tempo changes

The grid survives both, for free.

```
    ┌──────── bar 12 (4/4) ────────────┬─── bar 13 (3/4) ───────┐
    │1 e & a │2 e & a │3 e & a │4 e & a ║1 e & a │2 e & a │3 e & a ║
 G4 │x──────·│········│········│········║x──────·│········│········║
```

Cell width stays constant, bar width follows the meter, so a 3/4 bar is simply shorter.

Tempo changes cost nothing at all — the grid's axis is *musical* time, not seconds, so a
rall. from ♩=120 to ♩=60 changes nothing on screen. Only the playhead's clock cares.
That's a payoff from choosing ticks as the axis early.

---

## 6. Off-grid notes

Real MIDI files are full of notes a few ticks off any grid line — anything humanly played.

**Rule: draw it in the nearest cell, and flag the row with `~` in the gutter.** Pressing
`.` zooms finer until it lands true; `Q` quantises it for real.

This is the one place "sequencer feel" and "real MIDI file" genuinely disagree, and the
choice is *tell the truth loudly* rather than quietly quantise behind the user's back.

---

## 7. Pitch labelling

There is **no MIDI standard here** — the spec only defines note numbers 0–127. Octave
names were invented by applications, which is why they never agreed:

| convention | middle C | used by |
|---|---|---|
| Scientific / ASA | **C4** = 60 | notation software, acoustics, textbooks |
| Yamaha | C3 = 60 | Yamaha gear, Logic, Ableton, Cubase, Pro Tools |
| FL | C5 = 60 | FL Studio, off on its own |

**Shipped default: C4 (scientific).** Matthew's personal setting: C3.

Labelling is a pure display-layer offset — the model only ever stores note 60. The `.seq`
header declares its own convention (`middle-c C4`), so a file written in one and opened in
the other shifts its *labels* and not its *notes*. A file can't be silently misread.

### Beginner / child profile (built 2026-10-26)

Since the label is free, it can be anything:

```
    default    Matthew's     little-kid mode
    (C4=60)    (C3=60)       (no numbers)
   ┌────┐     ┌────┐        ┌─────┐
   │ C5 │     │ C4 │        │ do  │  ceiling
   │ B4 │     │ B3 │        │ ti  │
   │▒A#4│     │▒A#3│        │▒    │
   │ A4 │     │ A3 │        │ la  │  ┐
   │▒G#4│     │▒G#3│        │▒    │  │
   │ G4 │     │ G3 │        │ so  │  │ comfy
   │▒F#4│     │▒F#3│        │▒    │  │ singing
   │ F4 │     │ F3 │        │ fa  │  │ range
   │ E4 │     │ E3 │        │ mi  │  │ (bright)
   │▒D#4│     │▒D#3│        │▒    │  │
   │ D4 │     │ D3 │        │ re  │  ┘
   │▒C#4│     │▒C#3│        │▒    │
   │ C4 │     │ C3 │        │ DO  │  ← middle C, "home"
   │ B3 │     │ B2 │        │ ·   │  ← below range, dimmed
   └────┘     └────┘        └─────┘
```

Solfège for the naturals, accidentals left blank rather than teaching a seven-year-old
"di ri fi si li." The **range shading** does the real work — a child can see which rows
are singable, so they write things they can actually perform.

Reference: children aged 5–7 sing comfortably in a narrow band around D–A above middle C,
widening to roughly an octave by nine. C6 is high-soprano territory and out of reach for
almost all of them.

---

## 8. The velocity lane

It stays visible under the grid, permanently. **It's an affordance, not a readout** —
seeing it advertises that quiet is possible, and nudges toward using the full dynamic
range instead of everything landing at one loudness.

Three consequences:

- **Axis labelled with dynamics** (pp / p / mf / f), not 0–127. Someone learning meets
  the vocabulary they'll find in real sheet music, and "make this softer" becomes
  something you aim at rather than a number you guess.
- **The lane is editable.** Drag a bar, or arrow up/down with focus in it. A
  visible-but-untouchable lane is a scold; an editable one is an invitation.
- **Default note velocity ≈ 80 (mf), never 127.** If new notes land at maximum you can
  only ever go down, so nobody does. Landing at mf puts louder and softer each one gesture
  away in opposite directions. A wall of identical bars *looks* boring, and looking boring
  is the nudge.

---

## 9. Tracks

**One track editable at a time; the others render as dim ghosts** (`,` heads, thin ties).
Tab cycles. You always know what you're touching, and you can't damage a track you
weren't looking at.

Channel 10 swaps note names for GM drum names automatically — same grid, suddenly a drum
machine:

```
 HH-o│········│········│····x···│········║
▒HH-c│x·x·x·x·│x·x·x·x·│x·x·····│x·x·x·x·║
 Snr │········│X──·····│········│X──·····║
 Kick│X──·····│········│X──··X··│········║
```

---

## 10. Files

**`.mid` is the only save format. `mido` writes it. There is no custom canonical format.**

`.seq` is a **view**, not a format — what `seq cat` prints and what the TUI draws. You can
export it, edit it in vim, and `seq build` it back, which gives console-only MIDI editing.
But nothing depends on it and nothing breaks if you never touch it.

```
%seq 1
song   title "Bluesy"  ppq 480  tempo 96  time 4/4  middle-c C4
track  2  name "Rhodes"  chan 1  program 5
zoom   1/16   bands  o=40 x=88 X=112

@9     │1 e & a │2 e & a │3 e & a │4 e & a │
  A4   │········│········│x──────·│········│
  G4   │········│X──────·│········│········│
  E4   │x──────·│········│········│········│
  D#4  │········│········│········│o──·····│
  C4   │x──────·│········│········│········│
```

Only rows with notes are printed (`--full` for all).

### The passthrough rule — build this on day one

```
  load  .mid ──┬──► notes we model        → the grid, editable
               └──► everything else       → opaque bucket, keyed by tick
                    (SysEx, CC automation, pitch bend,
                     unknown meta, exact positions)

  save  .mid ◄───── merge both back, in tick order
```

Without it: you open someone's file, nudge one note, save, and their mod-wheel automation
is gone — because the model only knew about notes and mido faithfully wrote out exactly
the nothing it was given.

Also preserve PPQ, format 0 vs 1, track order and track names rather than normalising to
our own preferences.

**Guarantee: opening and saving a file you didn't edit gives back the same music.** Not
the same bytes — mido re-encodes — but nothing lost. Retrofitting this later is miserable.

---

### Match DAW conventions; don't inherit DAW limitations

Checking the grid against a DAW is a check on *our reading*, not a spec to conform
to. When we and a DAW disagree, it's one of two things, and they pull opposite ways:

**1. We disagree about what's in the file.** We say the G is on beat 2, it says
beat 3. That's ours to fix — a mature DAW is the better-tested reader and we should
assume we're wrong first.

**2. The DAW can't represent something the file legitimately contains.** A mid-song
meter change, say. That isn't a disagreement about the file at all — it's a missing
capability at the other end.

**The arbiter is the file, not the DAW.** Both readings get judged against the raw
bytes, which `mido` will read out for us at any time. That dissolves the question of
who to believe: whoever matches the file.

Adjusting the model to fit a limitation at the other end would be the same mistake
this design already refused once. The `~` flag exists because we won't silently
quantise an off-grid note — we say so, loudly, even when it's inconvenient. Dropping
support for something real because a popular tool can't show it is that same silent
lie, one level up.

Where a DAW *should* move us is where it has a **convention** rather than a limit:

| | | what we do |
|---|---|---|
| middle C = C5 in FL Studio | convention | **offer it** — `--middle-c c5`, a display option, already built |
| 96 PPQ internally | a setting | **preserve the file's** PPQ, don't normalise to ours |
| no mid-song meter change | a limitation | **keep ours** — and consider warning on export later |

A practical corollary: **don't round-trip a reference file through a DAW that might
drop something.** Open it, look, close without saving. Otherwise the file you were
using as ground truth quietly stops being ground truth.

---

## 11. Interaction model

Selection is a **set of notes**. A rectangle drag is just a fast way to populate that set,
not a different kind of selection. The set stays stable across edits, so transposing
doesn't lose it.

**No tool modes.** No pencil / eraser / arrow toolbar to get stuck in.

```
    on a note                    on empty space
  ──────────────────────       ─────────────────────────────
  click      select + play     click      insert + play
  drag       move it           drag       insert, drag out length
  shift+clk  add to set        shift+drag marquee select
  right      delete            right      —
  right+drag erase sweep       ctrl+drag  ignore snap
  ctrl+drag  ignore snap
  edge drag  resize            (added when step 4 was built, 2026-10-18)
```

- **Esc deselects.** Clicking empty space can't, because it inserts.
- **Right-click-delete is only safe because undo is load-bearing from step 1.**
  Destructive-with-no-confirmation requires a genuinely reliable Ctrl+Z. It costs us the
  context menu, which is a fine trade for a grid — the function set lives in the menu bar
  instead, where it can be browsed.
- Modelled on FL Studio's piano roll, which is the one people consistently describe as
  fastest to write in, because clicking empty space *just writes a note*.

### Audition

**Clicking an empty square plays the note. Dropping a dragged note or selection plays it
back.** This is what makes the grid explorable rather than something you have to already
understand.

It isn't a special feature — it fires the same **"play selection"** command that the menu,
the palette and a keyboard shortcut also fire. One class, four doors.

Two guards:
- A toggle in the transport bar, on by default, for when you're moving forty notes and
  don't want forty little performances.
- It doesn't fire while the transport is already rolling.

**The audition sink must accept a set of notes with relative timing, not a single note.**
Getting this wrong at step 1 means rewriting every command later.

Sound arrives at step 5, but the hook and a null sink go in at step 1. The gesture works
silently from step 4.

---

## 12. Commands, menus and the palette

Edits are **command objects** in `core/edit.py` — built for undo, but that means the
**command registry *is* the function set.** One new class buys undo, a menu entry, a
palette entry, a keyboard shortcut, TUI access and scriptability, with nothing to wire.

Two front doors onto the same registry:

- **Menu bar — for browsing.** You can't search a palette for a name you don't know.
  A beginner needs to be able to *look*.
- **Command palette — for speed.** Ctrl+P in the GUI, `:` in the TUI. Type "inv", get
  Invert. Works identically in both UIs, which is where GUI/TUI parity comes from.

Shortcuts shown in the menu are how people graduate menu → shortcut → palette without
anyone explaining it.

### The Notes menu teaches the taxonomy

```
┌─ Notes ─────────────────────────────────────┐
│  ─ keeps the shape ─────────────────         │
│   Transpose      ▸  semitones…      ↑ ↓      │
│                     scale degrees…  ⇧↑ ⇧↓    │
│   Invert…                                    │
│   Retrograde                                 │
│   Augment  ×2                                │
│   Diminish ÷2                                │
│  ─ moves notes independently ───────         │
│   Quantise…                         Q        │
│   Snap to scale…                             │
│   Humanise…                                  │
│   Velocity       ▸  scale / ramp / flatten   │
└──────────────────────────────────────────────┘
```

- **Keeps the shape** — every note moves together, relationships survive intact.
- **Moves notes independently** — relationships are collateral damage. Sometimes exactly
  what you want, but a different kind of act, and it shouldn't sit in the same
  unlabelled list.

The separator does the teaching. Someone browsing learns there are two kinds of operation
before running either one.

**The trap, named honestly:** "transpose up a third" is two operations. *Chromatic*
transpose adds fixed semitones — intervals identical, may leave the key. *Diatonic*
transpose moves by scale degrees — stays in key, but some notes move 3 semitones and some
4, so the intervals change. Both are legitimate; calling both of them "Transpose" is not.
Two entries, two names.

**Side benefit of a character grid:** a structure-preserving transform produces an
identical glyph shape, just moved.

```
      │1 e & a │2 e & a │            │1 e & a │2 e & a │
 G4   │····x──·│········│      C5    │····x──·│········│
 E4   │··x·····│········│      A4    │··x·····│········│
 C4   │x·······│········│      F4    │x·······│········│
         original                    transpose +5
```

If a transform claims to preserve structure and the shape changes, it lied — and you can
*see* that it lied. Inversion mirrors vertically, retrograde mirrors horizontally,
augmentation stretches the ties. The picture is the proof.

---

## 13. Architecture

```
midiseq/
  core/                 ← never imports a UI. Ever.
    model.py            Song, Track, Note, Tempo — in ticks, always
    grid.py             model ↔ cells. zoom, snap, off-grid detection
    render.py           cells → glyph rows   ← GUI, TUI and .seq all use this
    edit.py             AddNote, DelNote, SetVel, Move, Resize, Transpose…
                        (command objects — also the function set)
    history.py          undo/redo over commands
    io_midi.py          mido in/out, plus the passthrough bucket
    io_text.py          .seq view in/out
    transport.py        playhead clock, abstract output sink
  audio/
    sink_rtmidi.py      real MIDI out
    sink_null.py        swallows auditions until step 5
  ui_gui/               DearPyGui (PySide6 until 2026-10-18 — see §15)
  ui_tui/               Textual
  cli/                  seq cat | build | play | stat
```

Two load-bearing rules:

1. **`core/render.py` is shared by every UI and by the text view.** "The UIs look alike"
   then holds by construction rather than by discipline.
2. **Every edit is a command object.** Undo, redo, the menu, the palette, scripting and
   the TUI all fall out of one implementation — and all of it is testable with no window
   open.

**Sound:** neither `mido` nor `python-rtmidi` makes any noise on its own. On Windows,
python-rtmidi can open the built-in Microsoft GS Wavetable Synth for free; FluidSynth
plus a SoundFont is the upgrade path.

---

## 14. Build order

Each step ends with something concrete to run, look at, or listen to.

| # | Build | Test |
|---|---|---|
| 1 | `core/` — model, grid, render, `.seq` writer, command objects, history, audition hook + null sink | `seq cat` a real `.mid` — does the grid match your DAW? |
| 2 | `.seq` reader, passthrough bucket, round-trip | Throw ugly MIDI files at it; diff the events |
| 3 | GUI shell — window, grid widget, scroll, zoom. **No editing** | Does it *feel* right? Is it readable? |
| 4 | GUI editing, mouse model, undo, menu bar, palette | Write a bar by hand; report what fights you |
| 5 | Playback via rtmidi — the null sink gets replaced and everything starts making noise | Does it sound right? Latency OK? |
| 6 | TUI in Textual | Same shape, keyboard-only |
| 7 | Ghosts, transformations, scale filter, drum names, beginner profile | Live with it a week |

**Step 3 is deliberately non-editable.** Readability gets judged before interaction is
committed to, because readability is the expensive thing to change later.

---

## 15. Still open

- **GUI toolkit — decided twice.** PySide6 on 2026-08-15, on one argument: the grid
  wants to be one custom-painted widget, and Qt repaints thousands of cells at 60fps
  without a fight. **Changed to DearPyGui on 2026-10-18**, before any GUI code existed,
  for three reasons: Matthew already builds PySynthRack in it, so its zoom, window
  persistence, scroll-to-adjust and mocked-dpg test idiom come for free; a sequencer and
  a modular synth in one toolkit can later share a window (the Jeskola Buzz shape — a
  tracker with machines behind it); and an immediate-mode GPU drawlist paints a grid of
  rectangles at least as fast as QPainter. Known costs, each answered: scrolling and
  keyboard are DIY (the virtualised paint was planned anyway); the default font is ASCII
  only (the GUI paints rectangles, and labels get a custom font range); testing is weaker
  than Qt's offscreen mode (so a thin "paint plan" layer sits between the cell matrix and
  the draw calls, and is tested without a window). **Gate:** a one-session grid spike —
  the demo file's matrix in a drawlist, virtualised, scroll + zoom + hover hit-test,
  frame time measured on a long file. If it stutters, PySide6 is still there and nothing
  in `core/` has to change. One rule carried over from PySynthRack's zoom bug: **zoom
  scales the drawing, never the widgets** — a zoom that resizes the control you zoom with
  feeds back on itself.
- **Beginner / child display profile** — solfège labels and vocal-range shading. Scoped
  above; decided and built 2026-10-26: B in both editors, `Singing range...` sets the band.
- **`.seq` extension name** — now cosmetic, since it's only a view.

---

*Planned collaboratively, 15 August 2026. No code written yet — by design.*
*§13/§15 revised 18 October 2026: GUI toolkit switched to DearPyGui.*
