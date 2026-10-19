"""The ``:`` line's state machine and the entries behind it."""

from __future__ import annotations

from midiseq.core.edit import REGISTRY
from midiseq.ui_gui.editor import GESTURE_ONLY, MenuEntry
from midiseq.ui_tui.palette import TUI_ACTIONS, Palette, match, tui_entries


def test_every_registry_command_that_is_not_a_gesture_is_an_entry():
    keys = {e.key for e in tui_entries()}
    assert all(k in keys for k in REGISTRY if k not in GESTURE_ONLY)
    assert all(k in keys for k, _, _ in TUI_ACTIONS)
    assert len(keys) == len(tui_entries())           # no duplicates


def test_match_is_substring_first_then_letters_in_order():
    labels = [e.label for e in match("tsd", tui_entries())]
    assert labels[0] == "Transpose · scale degrees…"
    assert [e.label for e in match("inv", tui_entries())] == ["Invert…"]
    assert match("", tui_entries()) == tui_entries()


def test_search_prompt_run():
    p = Palette(lambda: [MenuEntry("a", "Alpha", None, False), MenuEntry("b", "Beta…", None, True)],
                lambda key: [("x", "X value", "1"), ("y", "Y value", "")] if key == "b" else None)
    p.open()
    assert p.searching and p.line == ":" and [e.label for e, _ in p.shown(5)] == ["Alpha", "Beta…"]
    p.type("al")
    assert p.submit() == ("a", {}) and not p.is_open
    p.open()
    p.type("be")
    assert p.submit() is None and p.prompting
    assert p.caption == "Beta - X value [1]: "
    assert p.submit() is None                        # blank takes the default
    assert p.caption == "Beta - Y value: "
    p.type("hi")
    assert p.submit() == ("b", {"x": "1", "y": "hi"}) and not p.is_open


def test_shown_keeps_the_highlight_in_the_window():
    entries = [MenuEntry(str(i), f"Entry {i}", None, False) for i in range(20)]
    p = Palette(lambda: entries, lambda key: None)
    p.open()
    for _ in range(12):
        p.move(1)
    window = p.shown(8)
    assert len(window) == 8 and window[-1] == (entries[12], True)
    p.move(-100)
    assert p.highlight == 0 and p.shown(8)[0] == (entries[0], True)


def test_prompt_straight_in():
    p = Palette(lambda: [], lambda key: [("path", "Save as", "")] if key == "s" else None)
    assert not p.prompt(MenuEntry("x", "X", None, False))
    assert p.prompt(MenuEntry("s", "Save as…", None, True)) and p.prompting
    p.type("tune")
    assert p.submit() == ("s", {"path": "tune"})
