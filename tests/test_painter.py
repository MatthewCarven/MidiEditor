"""The DearPyGui glue, with a recorder standing in for DearPyGui."""

from __future__ import annotations

from midiseq.ui_gui.paint import DEFAULT_THEME, Label, Line, Rect
from midiseq.ui_gui.painter import UNKNOWN_ROLE, DpgPainter


class FakeDpg:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def draw_rectangle(self, pmin, pmax, **kw):
        self.calls.append(("rect", pmin, pmax, kw))

    def draw_line(self, p1, p2, **kw):
        self.calls.append(("line", p1, p2, kw))

    def draw_text(self, pos, text, **kw):
        self.calls.append(("text", pos, text, kw))

    def delete_item(self, item, **kw):
        self.calls.append(("delete", item, kw))


def test_every_primitive_becomes_exactly_one_call():
    dpg = FakeDpg()
    painter = DpgPainter(dpg)
    n = painter.draw([
        Rect(1, 2, 3, 4, "note_loud"),
        Rect(5, 6, 7, 8, "hover", filled=False),
        Line(0, 0, 9, 9, "bar_line"),
        Label(4, 4, "G4", "row_label", 12),
    ], "layer")
    assert n == 4
    kinds = [c[0] for c in dpg.calls]
    assert kinds == ["rect", "rect", "line", "text"]
    filled, outline = dpg.calls[0], dpg.calls[1]
    assert filled[3]["fill"] == DEFAULT_THEME["note_loud"]
    assert filled[3]["parent"] == "layer"
    assert "fill" not in outline[3] and outline[3]["thickness"] == 1
    assert dpg.calls[3][2] == "G4" and dpg.calls[3][3]["size"] == 12


def test_an_unknown_role_is_loud_not_fatal():
    dpg = FakeDpg()
    DpgPainter(dpg).draw([Rect(0, 0, 1, 1, "no_such_role")], "layer")
    assert dpg.calls[0][3]["fill"] == UNKNOWN_ROLE


def test_repaint_clears_the_layer_first():
    dpg = FakeDpg()
    DpgPainter(dpg).repaint([Line(0, 0, 1, 1, "beat_line")], "grid")
    assert dpg.calls[0] == ("delete", "grid", {"children_only": True})
    assert dpg.calls[1][0] == "line"


def test_a_theme_override_only_touches_what_it_names():
    dpg = FakeDpg()
    painter = DpgPainter(dpg, {"ghost": (1, 2, 3, 4)})
    assert painter.colour("ghost") == (1, 2, 3, 4)
    assert painter.colour("note_soft") == DEFAULT_THEME["note_soft"]
