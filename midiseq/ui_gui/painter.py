"""DearPyGui glue: a paint plan to draw calls. Deliberately dumb.

Every primitive becomes exactly one draw call, coloured by its role. The
module takes the ``dearpygui.dearpygui`` module as a constructor argument
rather than importing it, so a test can hand it a recorder and check the
calls without a display - the same mocked-dpg idiom PySynthRack settled on.
"""

from __future__ import annotations

from typing import Any, Iterable

from .paint import DEFAULT_THEME, Label, Line, Primitive, Rect

#: An unknown role paints magenta rather than raising, so a typo is visible
#: on screen instead of killing the frame.
UNKNOWN_ROLE = (255, 0, 255, 255)


class DpgPainter:
    def __init__(self, dpg: Any, theme: dict[str, tuple[int, int, int, int]] | None = None) -> None:
        self.dpg = dpg
        self.theme = dict(DEFAULT_THEME)
        if theme:
            self.theme.update(theme)

    def colour(self, role: str) -> tuple[int, int, int, int]:
        return self.theme.get(role, UNKNOWN_ROLE)

    def clear(self, layer: Any) -> None:
        self.dpg.delete_item(layer, children_only=True)

    def draw(self, items: Iterable[Primitive], layer: Any) -> int:
        """Draw every primitive into ``layer``; returns how many."""
        dpg = self.dpg
        count = 0
        for p in items:
            c = self.colour(p.role)
            if isinstance(p, Rect):
                if p.filled:
                    dpg.draw_rectangle((p.x0, p.y0), (p.x1, p.y1), color=c, fill=c, parent=layer)
                else:
                    dpg.draw_rectangle((p.x0, p.y0), (p.x1, p.y1), color=c, thickness=1, parent=layer)
            elif isinstance(p, Line):
                dpg.draw_line((p.x0, p.y0), (p.x1, p.y1), color=c, parent=layer)
            elif isinstance(p, Label):
                dpg.draw_text((p.x, p.y), p.text, color=c, size=p.size, parent=layer)
            else:
                continue
            count += 1
        return count

    def repaint(self, items: Iterable[Primitive], layer: Any) -> int:
        self.clear(layer)
        return self.draw(items, layer)


__all__ = ["DpgPainter", "UNKNOWN_ROLE"]
