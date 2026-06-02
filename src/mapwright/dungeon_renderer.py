"""Vector (SVG) renderer for dungeons.

Renders a :class:`~mapwright.dungeon.Dungeon` as a scalable SVG: a dark wall
background, the carved floor (rooms + corridors) taken straight from the walkable
grid, room outlines so chambers read as rooms rather than wide corridors, an
optional faint tile grid for a battlemap look, and optional per-room labels.

Mirrors :class:`~mapwright.svg_renderer.RegionalSVGRenderer`: pure string-building,
no new dependencies, a single ``scale`` (pixels per tile) dial. The grid is the
source of truth, so what you see is exactly what ``Dungeon.ascii()`` reports.
"""

from __future__ import annotations

import xml.sax.saxutils as su
from typing import Optional, Sequence, Union

from .dungeon import Dungeon

# Palette — muted stone floor on a near-black wall, dark room outlines.
_WALL_BG = "#1b1b22"
_FLOOR = "#c9bd9e"
_ROOM_FILL = "#d8cdae"
_ROOM_STROKE = "#3a3527"
_GRID_LINE = "#000000"  # hairline; opacity applied separately (SVG 1.1-safe)
_GRID_OPACITY = 0.13
_LABEL = "#23211c"
_LABEL_HALO = "#f7f3ea"

# labels=True numbers rooms; a sequence supplies explicit strings; None = off.
LabelSpec = Union[bool, Sequence[str], None]


class DungeonSVGRenderer:
    """Renders a :class:`Dungeon` to an SVG document string."""

    def __init__(self, scale: float = 14.0):
        # scale = pixels per dungeon tile.
        self.scale = scale

    def render(
        self,
        dungeon: Dungeon,
        *,
        show_rooms: bool = True,
        show_grid: bool = False,
        labels: LabelSpec = None,
    ) -> str:
        """Render ``dungeon`` to SVG.

        ``show_rooms`` overlays room outlines; ``show_grid`` draws a faint tile
        grid; ``labels`` is ``None`` (off), ``True`` (number rooms 1..N), or a
        sequence of strings (one per room, e.g. generated room names).
        """
        s = self.scale
        w_px, h_px = dungeon.width * s, dungeon.height * s

        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px:.0f}" '
            f'height="{h_px:.0f}" viewBox="0 0 {w_px:.0f} {h_px:.0f}">',
            f'<rect width="{w_px:.0f}" height="{h_px:.0f}" fill="{_WALL_BG}"/>',
        ]

        # 1. Floor — run-length rows of the walkable grid (one rect per run keeps
        #    the node count low and avoids seams between adjacent tiles).
        parts.append(self._floor_svg(dungeon))

        # 2. Optional faint tile grid (battlemap look).
        if show_grid:
            parts.append(self._grid_svg(dungeon))

        # 3. Room outlines, so chambers are distinct from corridors.
        if show_rooms:
            parts.append(self._rooms_svg(dungeon))

        # 4. Labels.
        label_texts = self._resolve_labels(dungeon, labels)
        if label_texts:
            parts.append(self._labels_svg(dungeon, label_texts))

        parts.append("</svg>")
        return "".join(parts)

    # -- floor -----------------------------------------------------------

    def _floor_svg(self, dungeon: Dungeon) -> str:
        s = self.scale
        grid = dungeon.grid
        rects: list[str] = []
        for y in range(dungeon.height):
            x = 0
            while x < dungeon.width:
                if grid[y, x]:
                    x0 = x
                    while x < dungeon.width and grid[y, x]:
                        x += 1
                    rects.append(
                        f'<rect x="{x0 * s:.1f}" y="{y * s:.1f}" '
                        f'width="{(x - x0) * s:.1f}" height="{s:.1f}"/>'
                    )
                else:
                    x += 1
        if not rects:
            return ""
        return f'<g fill="{_FLOOR}">' + "".join(rects) + "</g>"

    # -- grid ------------------------------------------------------------

    def _grid_svg(self, dungeon: Dungeon) -> str:
        s = self.scale
        w_px, h_px = dungeon.width * s, dungeon.height * s
        segs: list[str] = []
        for x in range(dungeon.width + 1):
            segs.append(f'M{x * s:.1f},0 L{x * s:.1f},{h_px:.1f}')
        for y in range(dungeon.height + 1):
            segs.append(f'M0,{y * s:.1f} L{w_px:.1f},{y * s:.1f}')
        return (f'<path d="{" ".join(segs)}" stroke="{_GRID_LINE}" '
                f'stroke-opacity="{_GRID_OPACITY}" stroke-width="1" fill="none"/>')

    # -- rooms -----------------------------------------------------------

    def _rooms_svg(self, dungeon: Dungeon) -> str:
        s = self.scale
        rects: list[str] = []
        for r in dungeon.rooms:
            rects.append(
                f'<rect x="{r.x * s:.1f}" y="{r.y * s:.1f}" '
                f'width="{r.w * s:.1f}" height="{r.h * s:.1f}"/>'
            )
        if not rects:
            return ""
        return (f'<g fill="{_ROOM_FILL}" stroke="{_ROOM_STROKE}" '
                f'stroke-width="2" stroke-linejoin="round">'
                + "".join(rects) + "</g>")

    # -- labels ----------------------------------------------------------

    def _resolve_labels(self, dungeon: Dungeon, labels: LabelSpec) -> Optional[list[str]]:
        if labels is None or labels is False:
            return None
        if labels is True:
            return [str(i + 1) for i in range(len(dungeon.rooms))]
        return [str(t) for t in labels]

    def _labels_svg(self, dungeon: Dungeon, texts: Sequence[str]) -> str:
        s = self.scale
        out: list[str] = [
            '<g font-family="Georgia, serif" font-size="11" text-anchor="middle">'
        ]
        for room, text in zip(dungeon.rooms, texts):
            if not text:
                continue
            # Centre on the rect's true pixel centre (room.cx uses // so it would
            # sit half a tile off for even-sized rooms).
            cx, cy = (room.x + room.w / 2) * s, (room.y + room.h / 2) * s
            out.append(
                f'<text x="{cx:.1f}" y="{cy + 4:.1f}" '
                f'stroke="{_LABEL_HALO}" stroke-width="3" paint-order="stroke" '
                f'fill="{_LABEL}">{su.escape(text)}</text>'
            )
        out.append("</g>")
        return "".join(out)
