"""Vector (SVG) renderer for settlements.

Renders a :class:`~mapwright.settlement.Settlement` as a scalable SVG: optional
sea on the coastal side, the town footprint, named/typed Voronoi wards with
kind-based fills, the town boundary (drawn heavier when walled — proper towers
and gates come with the wall layer), and labels.

Mirrors :class:`~mapwright.svg_renderer.RegionalSVGRenderer` and
:class:`~mapwright.dungeon_renderer.DungeonSVGRenderer`: pure string-building, a
single ``scale`` (pixels per tile) dial, no new dependency.
"""

from __future__ import annotations

import math
import xml.sax.saxutils as su
from typing import Optional

from ._geometry import clip_halfplane, polygon_centroid
from .settlement import Settlement

# Ward fill by kind (parchment-ish town palette); falls back to residential.
_WARD_RGB: dict[str, tuple[int, int, int]] = {
    "market": (217, 192, 138),
    "residential": (205, 191, 158),
    "craftsmen": (194, 180, 143),
    "noble": (216, 205, 176),
    "slums": (179, 166, 132),
    "temple": (223, 214, 192),
    "garrison": (185, 169, 140),
    "docks": (174, 187, 176),
}
_WARD_DEFAULT = (205, 191, 158)
_WARD_STROKE = (74, 66, 48)
_BUILDING = (125, 108, 82)
_BUILDING_STROKE = (74, 62, 46)
_ROAD = (228, 217, 188)        # pale road surface
_ROAD_CASING = (108, 96, 74)   # darker edge so roads read over any fill
_FOOTPRINT = (230, 220, 192)
_COUNTRYSIDE = (201, 210, 187)
_WATER = (47, 109, 143)
_WALL = (60, 54, 40)
_TOWER_EDGE = (28, 24, 16)
_LABEL = (35, 33, 28)
_LABEL_HALO = (247, 243, 234)


def _rgb(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


class SettlementSVGRenderer:
    """Renders a :class:`Settlement` to an SVG document string."""

    def __init__(self, scale: float = 8.0):
        # scale = pixels per tile-unit.
        self.scale = scale

    def render(
        self,
        town: Settlement,
        *,
        show_lots: bool = True,
        show_streets: bool = True,
        show_labels: bool = True,
        label: str = "kind",
        show_title: bool = True,
    ) -> str:
        """Render ``town`` to SVG.

        ``show_lots`` draws the building footprints; ``show_streets`` overlays the
        road network. ``label`` chooses ward labels: ``"kind"`` (e.g. *Market*),
        ``"name"`` (the ward's place-name), or ``"none"``. ``show_title`` draws the
        town name as a header.
        """
        s = self.scale
        w_px, h_px = town.width * s, town.height * s

        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px:.0f}" '
            f'height="{h_px:.0f}" viewBox="0 0 {w_px:.0f} {h_px:.0f}">',
            f'<rect width="{w_px:.0f}" height="{h_px:.0f}" fill="{_rgb(_COUNTRYSIDE)}"/>',
        ]

        # 1. Sea on the water side of the coastline.
        if town.coastal and town.water_edge is not None:
            water = self._water_svg(town)
            if water:
                parts.append(water)

        # 2. Town footprint (land base under the wards).
        parts.append(self._poly_svg(town.footprint, fill=_rgb(_FOOTPRINT)))

        # 3. Wards.
        parts.append('<g stroke="%s" stroke-width="1" stroke-linejoin="round">'
                     % _rgb(_WARD_STROKE))
        for ward in town.wards:
            if len(ward.polygon) < 3:
                continue
            fill = _rgb(_WARD_RGB.get(ward.kind, _WARD_DEFAULT))
            parts.append(self._poly_svg(ward.polygon, fill=fill, stroke=None))
        parts.append("</g>")

        # 4. Building lots.
        if show_lots and town.lots:
            parts.append(self._lots_svg(town))

        # 5. Streets (overlaid on the building mass, with a casing for contrast).
        if show_streets and town.streets:
            parts.append(self._streets_svg(town))

        # 6. Wall (with towers + gates) when walled, else a light boundary.
        if town.wall is not None:
            parts.append(self._wall_svg(town))
        else:
            parts.append(self._poly_svg(town.footprint, fill="none",
                                        stroke=_rgb(_WALL), stroke_width=1.5))

        # 7. Labels + title.
        if show_labels and label != "none":
            parts.append(self._labels_svg(town, label))
        if show_title and town.name:
            parts.append(self._title_svg(town, w_px))

        parts.append("</svg>")
        return "".join(parts)

    # -- helpers ---------------------------------------------------------

    def _poly_svg(
        self, poly, *, fill: str, stroke: Optional[str] = None, stroke_width: float = 1.0
    ) -> str:
        s = self.scale
        if len(poly) < 2:
            return ""
        pts = " ".join(f"{x * s:.1f},{y * s:.1f}" for x, y in poly)
        attrs = f'points="{pts}" fill="{fill}"'
        if stroke is not None:
            attrs += f' stroke="{stroke}" stroke-width="{stroke_width:.1f}"'
        return f'<polygon {attrs}/>'

    def _water_svg(self, town: Settlement) -> str:
        """Fill the canvas half beyond the coastline with sea."""
        s = self.scale
        e0, e1 = town.water_edge
        dx, dy = e1[0] - e0[0], e1[1] - e0[1]
        if dx == 0 and dy == 0:
            return ""  # degenerate (zero-length) edge → no determinable water side
        n = (dy, -dx)  # a normal to the coast
        # Point the normal away from the town (toward open water).
        fc = polygon_centroid(town.footprint)
        if n[0] * (fc[0] - e0[0]) + n[1] * (fc[1] - e0[1]) > 0:
            n = (-n[0], -n[1])
        rect = [(0.0, 0.0), (float(town.width), 0.0),
                (float(town.width), float(town.height)), (0.0, float(town.height))]
        # Keep the side where (p - e0)·n >= 0  →  clip_halfplane with a = -n.
        sea = clip_halfplane(rect, e0[0], e0[1], -n[0], -n[1])
        if len(sea) < 3:
            return ""
        pts = " ".join(f"{x * s:.1f},{y * s:.1f}" for x, y in sea)
        return f'<polygon points="{pts}" fill="{_rgb(_WATER)}"/>'

    def _lots_svg(self, town: Settlement) -> str:
        s = self.scale
        rects: list[str] = []
        for lot in town.lots:
            if len(lot.polygon) < 3:
                continue
            pts = " ".join(f"{x * s:.1f},{y * s:.1f}" for x, y in lot.polygon)
            rects.append(f'<polygon points="{pts}"/>')
        if not rects:
            return ""
        return (f'<g fill="{_rgb(_BUILDING)}" stroke="{_rgb(_BUILDING_STROKE)}" '
                f'stroke-width="0.5" stroke-linejoin="round">'
                + "".join(rects) + "</g>")

    def _streets_svg(self, town: Settlement) -> str:
        s = self.scale
        # (path-d, road-width) pairs; main roads wider than minor.
        roads: list[tuple[str, float]] = []
        for st in town.streets:
            if len(st.path) < 2:
                continue
            d = "M" + " L".join(f"{x * s:.1f},{y * s:.1f}" for x, y in st.path)
            roads.append((d, 3.2 if st.kind == "main" else 1.8))
        if not roads:
            return ""
        out = ['<g fill="none" stroke-linecap="round" stroke-linejoin="round">']
        # Casings first (a darker, wider stroke underneath), then the pale surface.
        for d, w in roads:
            out.append(f'<path d="{d}" stroke="{_rgb(_ROAD_CASING)}" '
                       f'stroke-width="{w + 1.6:.1f}"/>')
        for d, w in roads:
            out.append(f'<path d="{d}" stroke="{_rgb(_ROAD)}" stroke-width="{w:.1f}"/>')
        out.append("</g>")
        return "".join(out)

    def _wall_svg(self, town: Settlement) -> str:
        s = self.scale
        wall = town.wall
        ring = wall.ring
        if len(ring) < 2:
            return ""
        gate_keys = {(round(x, 3), round(y, 3)) for x, y in wall.gates}
        gap = 1.3  # tiles pulled back on each side of a gate → the gateway gap
        m = len(ring)
        count = m if wall.closed else m - 1

        segs: list[str] = []
        for i in range(count):
            a = ring[i]
            b = ring[(i + 1) % m]
            a2 = self._pull(a, b, gap) if (round(a[0], 3), round(a[1], 3)) in gate_keys else a
            b2 = self._pull(b, a, gap) if (round(b[0], 3), round(b[1], 3)) in gate_keys else b
            segs.append(f'M{a2[0] * s:.1f},{a2[1] * s:.1f} L{b2[0] * s:.1f},{b2[1] * s:.1f}')

        out: list[str] = [
            f'<path d="{" ".join(segs)}" fill="none" stroke="{_rgb(_WALL)}" '
            f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        ]
        # Towers at each corner.
        out.append(f'<g fill="{_rgb(_WALL)}" stroke="{_rgb(_TOWER_EDGE)}" stroke-width="0.6">')
        for x, y in ring:
            out.append(f'<circle cx="{x * s:.1f}" cy="{y * s:.1f}" r="2.6"/>')
        out.append("</g>")
        # Gatehouses (square) at the gates.
        if wall.gates:
            r = 3.0
            out.append(f'<g fill="{_rgb(_WALL)}" stroke="{_rgb(_TOWER_EDGE)}" stroke-width="0.8">')
            for x, y in wall.gates:
                out.append(f'<rect x="{x * s - r:.1f}" y="{y * s - r:.1f}" '
                           f'width="{2 * r:.1f}" height="{2 * r:.1f}"/>')
            out.append("</g>")
        return "".join(out)

    @staticmethod
    def _pull(p, toward, d: float):
        """Move point ``p`` a distance ``d`` toward ``toward`` (to open a gate gap),
        clamped to half the segment so it can't overshoot the midpoint and reverse
        the segment when both endpoints are gates on a short edge."""
        dx, dy = toward[0] - p[0], toward[1] - p[1]
        length = math.hypot(dx, dy) or 1.0
        d = min(d, length * 0.5)
        return (p[0] + dx / length * d, p[1] + dy / length * d)

    def _labels_svg(self, town: Settlement, label: str) -> str:
        s = self.scale
        out: list[str] = [
            '<g font-family="Georgia, serif" font-size="8" text-anchor="middle">'
        ]
        for ward in town.wards:
            text = ward.kind.title() if label == "kind" else ward.name
            if not text:
                continue
            cx, cy = ward.center[0] * s, ward.center[1] * s
            out.append(
                f'<text x="{cx:.1f}" y="{cy + 3:.1f}" '
                f'stroke="{_rgb(_LABEL_HALO)}" stroke-width="2.5" paint-order="stroke" '
                f'fill="{_rgb(_LABEL)}">{su.escape(text)}</text>'
            )
        out.append("</g>")
        return "".join(out)

    def _title_svg(self, town: Settlement, w_px: float) -> str:
        return (
            f'<text x="{w_px / 2:.1f}" y="20" text-anchor="middle" '
            f'font-family="Georgia, serif" font-size="16" font-weight="bold" '
            f'stroke="{_rgb(_LABEL_HALO)}" stroke-width="3" paint-order="stroke" '
            f'fill="{_rgb(_LABEL)}">{su.escape(town.name)}</text>'
        )
