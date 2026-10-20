# midiseq

A MIDI editor with the feel of a step sequencer. Notes run vertically (chromatic,
A–G plus sharps and flats), time runs horizontally, and a mark in a square means a
note starts there.

**Design and reasoning: [PLAN.md](PLAN.md). Progress: [TODO.md](TODO.md).**

Steps 1 to 6 are done: the `core/` skeleton, the `.mid` writer, text editing
that round-trips, a window that shows the grid, editing in that window — click
to write notes, drag to move them, undo, a Notes menu and a command palette
generated from the command registry — sound: every edit is auditioned and
the song plays through a MIDI output (on Windows, the built-in GS Wavetable
with nothing installed), with a playhead you can click the ruler to move —
and the same editor in a terminal, keyboard only, with a `:` palette over
the same registry.

## Try it

```
pip install -e ".[gui]"
python tools/make_demo.py examples/bluesy.mid
seq cat examples/bluesy.mid -t 2 -b 9-11 -w 3
seq gui examples/bluesy.mid -t 2 -b 9
seq tui examples/bluesy.mid -t 2 -b 9
```

If `seq` isn't found after installing — common on Windows, where pip's `Scripts`
directory often isn't on PATH — `python -m midiseq` is the same program:

```
python -m midiseq cat examples/bluesy.mid -t 2 -b 9-11 -w 3
```

```
    ┌─────────── bar 9 (4/4) ───────────┬───────────── bar 10 ──────────────┬────── bar 11 (3/4) ──────┐
    ║1 e & a │2 e & a │3 e & a │4 e & a ║1 e & a │2 e & a │3 e & a │4 e & a ║1 e & a │2 e & a │3 e & a ║
 A4 ║········│········│x──────·│········║········│········│········│········║········│x───────│───────·║
 G4 ║········│X──────·│········│········║········│····x·x·│········│········║········│········│········║
 F4~║········│········│········│········║········│········│········│········║x──────·│········│········║
 E4 ║x──────·│········│········│········║········│········│········│········║········│········│········║
▒D#4║········│········│········│x──·····║········│········│········│········║········│········│········║
 C4 ║x──────·│········│········│········║x──────·│········│········│········║········│········│········║
────╫────────┼────────┼────────┼────────╫────────┼────────┼────────┼────────╫────────┼────────┼────────╢
 f  ║        │█       │        │        ║        │        │        │        ║        │        │        ║
 mf ║█       │█       │█       │        ║█       │    █ █ │        │        ║        │        │        ║
 p  ║█       │█       │█       │█       ║█       │    █ █ │        │        ║█       │█       │        ║
 pp ║█       │█       │█       │█       ║█       │    █ █ │        │        ║█       │█       │        ║
────╨────────┴────────┴────────┴────────╨────────┴────────┴────────┴────────╨────────┴────────┴────────╨
```

Three things worth spotting in that picture:

* **Bar 11 is in 3/4** and is simply shorter. Cell width never changes with meter.
* **The `~` next to F4** says that row is drawn approximately — that note lands 37
  ticks late and doesn't sit on any cell boundary. The grid says so rather than
  quietly quantising it. `-z 1/32` zooms in until it's honest; `Quantise` makes it true.
* **The `▒` on D#4** marks an accidental row, so the black-key pattern reads at a
  glance and you never miscount which row is F♯ and which is G.

## The cell grammar

Each square is two characters: **what happens**, and **whether sound carries into
the next square.**

| char 1 — the event | | char 2 — the tie | |
|---|---|---|---|
| `·` | nothing | `·` | released within this cell |
| `o` | note on, soft | `─` | still sounding as the next cell begins |
| `x` | note on, medium | | |
| `X` | note on, loud | | |
| `─` | note continuing | | |
| `,` | ghost note (other track, read-only) | | |

```
x──────·   quarter note, fills the beat
x──·x──·   two eighths
x·x·x·x·   four staccato sixteenths
x───────   a note tying over into the next beat
```

## Commands

```
seq cat   FILE [-t TRACK] [-z 1/8] [-b 9-12] [-g] [--full] [--ascii]
               [--middle-c c3|c4|c5] [--no-lane] [-w BARS_PER_BLOCK]
seq stat  FILE
seq check FILE...            does it survive a round trip?
seq build FILE [-i SOURCE] [-o OUT] [-n]
seq gui   [FILE] [-t TRACK] [-z 1/16] [-b BAR] [--no-ghosts] [--middle-c c3|c4|c5]
          [--midi-out NAME]
seq tui   [FILE] [-t TRACK] [-z 1/16] [-b BAR] [--no-ghosts] [--middle-c c3|c4|c5]
          [--midi-out NAME]                              the same, in the terminal
seq play  FILE [-b BAR] [--port NAME] [--seconds N]      headless, Ctrl+C stops
```

## The window

`seq gui song.mid` opens the same grid in a DearPyGui window (`python -m
midiseq gui` or `python -m midiseq.ui_gui` do the same). It shows one track
with the others as grey ghosts, the ruler with the meter where it changes, the
`~` flag on approximate rows, and the velocity lane. Hover a square and the
status line names it: pitch, MIDI number, bar:beat.subdivision, length,
velocity.

```
wheel                 scroll pitches        Shift+wheel        scroll time
Ctrl+wheel            cell width            . / ,              finer / coarser grid (1/4 … 1/64)
arrows                a cell / a pitch      Ctrl+Left/Right    a bar
PageUp/PageDown       a screen of pitches   Home / End         first / last bar
Tab / Shift+Tab       next / previous track G                  ghosts on/off
F                     fit the pitch range   + / -              cell width
Ctrl+= / Ctrl+-       finer / coarser grid  File > Open        another file
```

Two zooms, deliberately on different hands. **Cell width** (Ctrl+wheel, `+`
`-`) is pixels: the same grid, bigger or smaller. **The grid** (`.` `,`) is
musical: what one cell *means*, from a quarter note to a sixty-fourth, which
is the zoom PLAN.md talks about — zoom finer until an approximate row lands
true. One modifier per wheel gesture, never two: a mouse driver that eats
Shift+Ctrl+wheel would otherwise make the two zooms look like one.

Zoom changes the cell width and nothing else — no font rescales, no widget
moves — and the window's size is taken from the viewport's client area and
nowhere else. Both are lessons from PySynthRack's zoom bug, kept here as
structure rather than discipline.

### Editing

No tool modes. What a click does depends only on what's under it:

```
    on a note                    on empty space
  click      select + play     click      insert + play
  drag       move it           drag       insert, drag out length
  edge drag  resize            shift+drag marquee select
  shift+clk  add to set        right      -
  right      delete            ctrl+drag  ignore snap
  right+drag erase sweep
  ctrl+drag  ignore snap
```

Esc deselects — clicking empty space can't, because it inserts. Right-click
deletes with no confirmation because Ctrl+Z is load-bearing: every edit is a
command object, and an erase sweep is one undo. With a selection the arrows
move the notes instead of the view: Up/Down a semitone, Shift+Up/Down a scale
degree, Left/Right a cell. Q quantises the selection to the grid, Del deletes
it, Ctrl+A selects the whole track, A toggles audition.

The velocity lane is editable, as PLAN.md always said it should be: drag a
bar, sweep a ramp across several, Shift+drag a flat line; a sweep is one undo.
Ctrl+Up/Down nudges a selection's velocity by five, Ctrl+Shift+Up/Down by one.
Cut, Copy, Paste and Duplicate (Ctrl+X, C, V, D) work on the selection; Paste
lands at the playhead and selects what it pasted, Duplicate lands right after
the selection.

The **Edit** and **Notes** menus are generated from the command registry in
`core/edit.py`, and so is the **command palette** (Ctrl+P: type `inv`, get
Invert). The Notes menu is laid out in the two groups the registry declares,
*keeps the shape* and *moves notes independently*, so the separator teaches the
difference before you run anything. Commands that take a parameter ask in a
small dialog: semitones; scale degrees and a key; an axis note; a quantise
strength; a velocity as `96`, `+10` or `x0.8`.

File > New, Save and Save as write `.mid` through the same writer `seq build`
uses, so the passthrough rule holds: nothing you didn't touch is rewritten.

### Playback

A transport bar sits between the menu and the grid: **Play / Stop** (Space),
**|<** rewind, the position as bar:beat.sub, the tempo at the playhead, the
**Audition** toggle (also A, also in the Edit menu — one flag, three doors),
and the **MIDI output** to use. Click the ruler to put the playhead there;
Shift+Space plays from the left edge of the view; Home and **|<** send the
playhead to bar 1 and the view with it. While rolling the playhead is a white
line and the view turns the page when it passes the right edge - unless you
have scrolled away from it, in which case the view is yours until the
playhead is back in it; never mid-drag. Space with nothing ahead of the
playhead starts again from the top, as |< would. Switching the MIDI output
while rolling carries on from where it was, on the new output.

Sound comes from a MIDI output, not from midiseq. `seq gui --midi-out NAME`
picks one by name, exact first, else substring; with nothing asked for, the
Microsoft GS Wavetable is
used when it exists, else the first port, else the app runs silent and the
status line says why. The sink is python-rtmidi (`pip install -e ".[audio]"`),
one scheduler thread with absolute due times; on this machine a note goes out
about a millisecond and a half after it is due, which the GS synth's own
latency dwarfs. Playing from the middle of a song sends the last program change
and controller values first, so the right instruments are in force, and
notes already sounding there restrike with what is left of them (chase), so
a seek into a held chord plays the chord. Edits while playing take effect
at once: the schedule is rebuilt from the playhead, and a held note across
the edit blinks rather than drops out.

`seq play song.mid -b 9` does the same from the console, printing the position
once a second; Ctrl+C releases every sounding note before exiting.

Audition never fires while the transport rolls, and deleting makes no sound —
both rules live in `core/transport.py`, which is still the only module that
thinks in seconds.

## The terminal

`seq tui song.mid` opens the same grid in the terminal (`python -m midiseq
tui` or `python -m midiseq.ui_tui` do the same), through Textual. It is the
picture `seq cat` prints, live: the bar header, the ruler, the shaded
accidental rows, ghosts, the `~` flag, the velocity lane, and a cursor —
`▼` over its column in the header, `>` in the gutter of its row — with the
status line underneath naming the square: pitch, MIDI number,
bar:beat.subdivision, length, velocity. Colour is shading on top of that,
so the picture still reads without it.

It is the window's mouse model at a cursor. What a click does there, Enter
does here, and it depends only on what is under the cursor — no tool modes.
`?` shows this table in the terminal:

```
    on a note                          on empty space
  Enter   select + play                Enter   insert one cell + play
  S       add to / drop from the set
  Del     delete

arrows            cursor a cell / a pitch      Shift+Left/Right  nudge a cell
Ctrl+Left/Right   cursor a bar                 Shift+Up/Down     transpose a semitone
PageUp/PageDown   cursor an octave             [ / ]             down / up a scale degree
Home / End        bar 1 (and rewind) / last    Ctrl+Up/Down      velocity by five
Tab / Shift+Tab   next / previous track        + / -             a cell longer / shorter
. / ,             finer / coarser grid         Q                 quantise to the grid
G / F             ghosts on/off / fit pitches  Ctrl+A / Esc      select the track / deselect
Space             play / stop                  Ctrl+Z / Ctrl+Y   undo / redo
Shift+P           play from the cursor         X / C / V / D     cut, copy, paste at the cursor, duplicate
P                 playhead to the cursor       Ctrl+S            save
A                 audition on/off              Ctrl+Q            quit (twice, with unsaved changes)
:                 the command palette          ?                 this table
```

Everything that acts on notes acts on **the selection, or else the note
under the cursor**, so a single note never needs selecting first. The
arrows always move the cursor — a keyboard has nothing else to move it
with — so the window's selection-moving arrows wear Shift here, and the
cursor follows a note it was on when the note moves: Shift+Up three times
carries one note up a minor third. The view follows the cursor by the bar:
walk off the right edge and the page turns to the cursor's bar. Paste lands
at the cursor, the one position a keyboard always has.

The letters are plain keys on purpose. A terminal only promises to deliver
characters: Windows Terminal cannot tell Shift+Enter from Enter or
Shift+Space from Space, sends Ctrl+Shift+S as Ctrl+S and Ctrl+Shift+Z as
Ctrl+Z, and keeps Ctrl+V and Ctrl+Shift+Up/Down for its own paste and
scrolling — so the chords the window uses for those are not the way in
here. S adds to the set, Shift+P plays from the cursor, X / C / V / D are
the clipboard, `[` and `]` move a scale degree, Ctrl+Y is redo, and Save as
is in the palette. Where a terminal does deliver the chords, they still
work.

`:` opens the palette: type `inv`, get Invert; Enter runs the highlighted
match, Up and Down pick another, Esc closes. It is the window's palette —
the same registry, the same entries, the same questions, asked one at a
time on the same line (`Invert - Axis note, e.g. G4 (blank: the midpoint):`),
blank taking the default — plus the terminal's own: Save, Save as, Go to
bar, Track, Play / Stop, Rewind, Keys, Quit.

Space plays from the playhead through the same sink as the window, chosen
the same way (`--midi-out NAME`, the GS Wavetable by default, else the first
port, else silent with the reason kept in the status line). The playhead is
a coloured column that turns the page when it runs off the right edge —
unless you have moved the view away, in which case it is yours until the
playhead is back in it. The lane is painted, not editable: Ctrl+Up/Down is
how velocity moves here.

## Editing in a text editor

```
seq cat song.mid -t 2 -b 9-11 -w 3 > edit.seq
vim edit.seq
seq build edit.seq -i song.mid -o edited.mid
   edit.seq -> edited.mid: 1 added, 1 removed, 9 untouched
```

`seq build` **patches** by default. It renders the same picture from the source
`.mid`, diffs your text against it, and changes only the cells that actually
differ. Everything you didn't type over is never rewritten — so velocities the
three bands can't express survive, off-grid notes keep their exact tick, and
unprinted bars, unprinted tracks and every unmodelled event are left alone.

That's the passthrough rule one storey up: **only what you actually changed
changes.**

Two consequences worth knowing:

* **To delete a note, blank the cell — don't delete the line.** A row that isn't
  in the text was never claimed about, and unclaimed is left alone. It has to
  work that way, or printing a two-bar window and building it back would wipe
  the rest of the song.
* **Without `-i` the text is all there is.** `seq build newtune.seq -o new.mid`
  builds from nothing, so velocities land on the header's band values and notes
  land on cell boundaries. That's the honest deal for composing from a blank
  file, and the reason `-i` is the one to reach for otherwise.

## Does your file survive?

```
seq check .\midi                          a whole folder, walked recursively
seq check .\midi\*.mid -q                 only the ones with problems
seq check weird.mid -v                    show me exactly what differed
seq check .\midi --report r.json -j 4     a whole library, grouped, in parallel
```

Loads each file, writes it straight back out, and compares what the two *say* —
notes, controllers, SysEx, tempo, meter, release velocity, metronome click. Not a
byte comparison: mido re-encodes, and `note_on` with velocity 0 comes back as
`note_off`. Those aren't changes. Anything `seq check` reports is.

Wildcards are expanded by `seq` itself rather than by the shell, so `*.mid` works
on Windows too. Folders are walked recursively. One unreadable file is reported
and the run carries on.

### On a real library

Past 200 files it stops listing files and starts listing **problem kinds**, because
twenty thousand files hit the same handful of bugs thousands of times and the
hundredth example teaches nothing:

```
======================================================
   19,847 file(s), 41,203,551 notes checked
======================================================
    19102  96.2%  ███████████████████████   came back identical
      488   2.5%  █                         lost or invented something
      257   1.3%  █                         could not be read

Problems, by kind — this is the bug list:
      401  sysex lost
           e.g. .\midioland\SC55-init.mid
       87  note lost
           e.g. .\midi\demos\overlap.mid

What's actually in the library:
  formats      1: 17204, 0: 2643
  ppq          480: 9120, 192: 4401, 96: 3877, 384: 1902, SMPTE: 90
  meters       4/4: 18334, 3/4: 1201, 6/8: 588, 12/8: 141, 7/8: 44
  1,204 file(s) change meter, 6,880 change tempo
```

`--report r.json` writes all of that as JSON, with up to six example files per
kind. **That's the file to send someone** — it's a few kilobytes even for twenty
thousand files, where the console output would be twenty thousand lines.

`-j 4` runs four at a time. Reckon on roughly 20 ms a file serially, so 20,000
files is about seven minutes, or half that in parallel.

The survey at the bottom is a free by-product and worth having on its own: it says
what real MIDI in the wild actually looks like, which is what the rest of this
should be built against rather than guessed at.

## Layout

```
midiseq/
  core/          never imports a UI
    model.py     Song, Track, Note — ticks always, stable note IDs
    grid.py      model ↔ cells: zoom, snap, bar map, off-grid detection
    render.py    → cell-state matrix, shared by every UI
    glyphs.py    cell state → characters, for the TUI and text view only
    edit.py      command objects — also the function set the menus read
    history.py   undo/redo
    io_midi.py   mido in and out, with the passthrough bucket
    io_seq.py    .seq back in — as a diff against the source, not as the truth
    io_text.py   the .seq view
    index.py     the notes touching a tick range, without scanning the track
    transport.py audition, the schedule (song → raw MIDI bytes with seconds), the
                 tempo clock both ways, Transport play/stop/position. Seconds live here only.
  audio/
    sink_null.py  swallows everything, keeps a log and a fake-able clock
    sink_rtmidi.py python-rtmidi: one scheduler thread, absolute due times, per-note release
    ports.py      which outputs exist; open one, or fall back and say why
  ui_common.py   what both editors share: menu and palette entries, prompts, the command builder. No toolkit.
  ui_gui/        DearPyGui
    paint.py     cell matrix → rectangles, lines, labels by role. No toolkit.
    painter.py   one primitive, one draw call — takes dpg as an argument
    grid.py      the widget's state: scroll, zooms, track, hover, status. No toolkit.
    editor.py    the mouse model, selection, undo, audition, the registry's doors. No toolkit.
    app.py       the window: menus, prompts, palette, keys, file dialogs, frames
  ui_tui/        Textual
    view.py      the screen's state: scroll, zoom, track, the cursor; the frame as lines and roles. No toolkit.
    editor.py    the keyboard model: key names to commands, the target rule, the registry's door. No toolkit.
    palette.py   the `:` line as a state machine: search, questions, run. No toolkit.
    app.py       the terminal: the pane, the status line, the palette's two lines, the timer
  cli/           seq
```

## Tests

```
python -m pytest
```

Four hundred and fifty-five tests, all on the design invariants rather than
the plumbing, and none of them opens a window or a terminal.

Step 6's tests judge the terminal as text: every line is the width and the
columns stop at it, the cursor is `▼` and `>` where it should be, walking
off the right edge turns the page to the cursor's bar, zooming keeps the
cursor on its tick, the 3/4 bar is three beats wide, the playhead is one
column from header to lane and nothing when off screen. The keyboard model
is driven by key names: Enter on empty inserts one cell and plays it, Del
takes the cursor note without a selection and the selection with one,
Shift+Up carries the cursor with the note, Q lands the off-grid F4 on the
bar line, `-` stops at one cell, `:tsd` asks two questions and `abc` for a
number comes back as a sentence, Ctrl+S writes a file that loads with the
edit, Ctrl+Q needs two presses with unsaved changes, `?` shows the key table
and any key puts it away. Six run the Textual app headless with its Pilot,
for the wiring only: keys reach the editor, Tab changes track rather than
focus, Ctrl+C copies rather than quitting.

Step 4's tests drive the mouse model with coordinates: a click on empty
inserts one cell and plays it, a drag draws the length out, Ctrl ignores snap
by sixty ticks, a drag on a note moves the whole selection and plays the
landing, an edge drag resizes, a marquee picks up bar 9's five notes and not
the ghost, a right-drag sweep deletes two notes as one undo, Q quantises the
off-grid F4 onto the bar line, and a bad value typed into a prompt comes back
as a sentence rather than a traceback.

Step 3's tests judge the *picture* without a toolkit: lay out bars 9–14 of the
demo file and assert ten note rectangles with ten heads, the dyad sharing a
column, the loud G4 painted loud at bar 9 beat 2, a note stopping where it
stops rather than at the cell edge, the `~` on the F4 row and nowhere else,
the 3/4 bar twelve cells wide against sixteen, and nothing painted past the
right edge. The DearPyGui glue is tested with a recorder in place of
DearPyGui: every primitive becomes exactly one call, with the colour its role
maps to. The note index is pinned to the scan it replaced on random songs.

Step 1 covers the cell grammar, off-grid honesty, tempo changes costing the grid
nothing, meter changes narrowing bars but not cells, undo round-trips, chromatic
vs diatonic transpose being genuinely different operations, structure-preserving
transforms leaving the glyph shape alone, and auditions keeping relative timing.

Step 2 covers the guarantee — that an unedited file comes back saying the same
thing — against files built ugly on purpose: format 0 with three channels, pitch
bend and both flavours of aftertouch, meter changing off the bar line, a 24-PPQ
file with a pitch overlapping itself, a file with no notes at all, and meta events
we don't model. Plus the text side: an unedited dump diffing to nothing, blanking
a cell deleting exactly one note, an untouched off-grid note keeping its 37 ticks,
velocities the bands can't express surviving a patch, and a row left out of the
text being left alone.

Ten of them are regressions from an adversarial pass over the finished code — a
`program_change` for another channel rehoming a whole part, a format 0 split
dropping controllers on silent channels, drum rows that couldn't be read back
because the ascii bar separator is `H` and twelve GM names contain one, and a
four-digit bar number truncating to three and still parsing. Each of those was
silent. WORKLOG.md has the full list and what each one broke.
