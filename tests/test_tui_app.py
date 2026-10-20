"""The Textual app, driven headless with its Pilot.

Only the wiring is judged here - keys reach the editor, the pane paints
the frame, the status line and the palette show, the app's own bindings
win over Textual's - because everything else is tested on the toolkit-free
layer without a terminal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from midiseq.audio.sink_null import NullSink
from midiseq.core import io_midi
from midiseq.ui_tui.app import GridPane, SeqApp, frame_text
from midiseq.ui_tui.editor import Editor
from midiseq.ui_tui.view import TextView

BLUESY = Path(__file__).resolve().parent.parent / "examples" / "bluesy.mid"
BAR9 = 8 * 4 * 480
G4 = (67, BAR9 + 480)


def make_app(bar: int = 9) -> SeqApp:
    grid = TextView(io_midi.load(BLUESY), 2)
    grid.go_to_bar(bar)
    return SeqApp(Editor(grid, NullSink(), path=BLUESY))


def run(test):
    """Run a coroutine test body: ``async def body(app, pilot)``."""
    async def go():
        app = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await test(app, pilot)
        return app
    return asyncio.run(go())


def plain(static) -> str:
    return str(static.render())


class TestTheApp:
    def test_the_pane_paints_the_grid_at_the_terminals_size(self):
        async def body(app, pilot):
            grid = app.editor.grid
            assert (grid.width, grid.height) == (100, 29)        # the status line takes one
            text = app.query_one(GridPane).render().plain
            assert "bar 9 (4/4)" in text and "X──────·" in text
            assert "9:1.1" in plain(app.query_one("#status"))
        run(body)

    def test_keys_reach_the_editor(self):
        async def body(app, pilot):
            grid = app.editor.grid
            cell = grid.cursor_cell
            await pilot.press("right", "right", "up")
            assert grid.cursor_cell == cell + 2 and grid.cursor_pitch == 61
            await pilot.press("tab")                              # ours, not focus traversal
            assert grid.track == 0
            assert app.focused is app.query_one(GridPane)
        run(body)

    def test_ctrl_c_copies_instead_of_quitting(self):
        async def body(app, pilot):
            grid = app.editor.grid
            grid.put_cursor(67, grid.view.tick_to_cell(G4[1]))
            await pilot.press("enter", "ctrl+c")
            assert app.editor.clipboard == ((67, 0, 460, 104),)
            assert app.return_code is None and not app.editor.want_quit
        run(body)

    def test_the_palette_shows_and_runs(self):
        async def body(app, pilot):
            await pilot.press("colon")
            assert app.editor.palette.is_open
            assert app.query_one("#palette_line").display and app.query_one("#palette_list").display
            await pilot.press("i", "n", "v")
            assert plain(app.query_one("#palette_line")).startswith(":inv")
            assert "Invert" in plain(app.query_one("#palette_list"))
            await pilot.press("escape")
            assert not app.query_one("#palette_line").display
        run(body)

    def test_question_mark_shows_the_key_table(self):
        async def body(app, pilot):
            await pilot.press("question_mark")
            table = app.query_one("#palette_list")
            assert table.display and "Shift+P" in plain(table) and "[ / ]" in plain(table)
            await pilot.press("escape")
            assert not table.display
        run(body)

    def test_ctrl_q_quits_a_clean_song(self):
        app = run(lambda app, pilot: pilot.press("ctrl+q"))
        assert app.editor.want_quit and app.return_code == 0

    def test_seq_tui_hands_its_options_to_launch(self, monkeypatch):
        from midiseq.cli.seq import main
        from midiseq.ui_tui import app as tui_app
        seen = {}

        def fake_launch(path, **kw):
            seen.update(path=path, **kw)
            return 0

        monkeypatch.setattr(tui_app, "launch", fake_launch)
        assert main(["tui", str(BLUESY), "-t", "2", "-b", "9", "-z", "1/8",
                     "--no-ghosts", "--middle-c", "c3", "--midi-out", "GS"]) == 0
        assert seen["path"] == str(BLUESY) and seen["track"] == 2 and seen["bar"] == 9
        assert str(seen["zoom"]) == "1/8" and seen["ghosts"] is False
        assert seen["middle_c_octave"] == 3 and seen["midi_out"] == "GS"

    def test_the_frame_text_carries_the_roles(self):
        grid = TextView(io_midi.load(BLUESY), 2)
        grid.resize(100, 26)
        grid.go_to_bar(9)
        text = frame_text(grid.build())
        assert text.plain.count("\n") == 25
        styled = {span.style for span in text.spans}
        assert any(getattr(s, "reverse", False) for s in styled)    # the cursor


def test_a_click_in_the_pane_reaches_the_editor():
    async def body(app, pilot):
        grid = app.editor.grid
        x = grid._xs[grid._col_index[grid.view.tick_to_cell(8 * 4 * 480 + 480)]]
        row = next(i for i, r in enumerate(grid.matrix.rows) if r.pitch == 67)
        await pilot.click(GridPane, offset=(x, row + 2))
        assert app.editor.selection and grid.cursor_pitch == 67
    run(body)
