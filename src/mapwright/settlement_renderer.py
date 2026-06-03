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
from .themes import DEFAULT_THEME, Theme, get_theme

# Town colours come from the theme's SettlementPalette (see themes.py); the
# default "parchment" palette reproduces the classic town look byte-for-byte.


class SettlementSVGRenderer:
    """Renders a :class:`Settlement` to an SVG document string.

    ``theme`` selects the palette (a name from :data:`~mapwright.themes.THEMES`
    or a :class:`~mapwright.themes.Theme`); the default ``"parchment"`` is the
    classic look.
    """

    def __init__(self, scale: float = 8.0, theme: str | Theme = DEFAULT_THEME):
        # scale = pixels per tile-unit.
        self.scale = scale
        self.theme = get_theme(theme)
        self._pal = self.theme.settlement

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
            f'<rect width="{w_px:.0f}" height="{h_px:.0f}" fill="{self._pal.countryside}"/>',
        ]

        # 1. Sea on the water side of the coastline.
        if town.coastal and town.water_edge is not None:
            water = self._water_svg(town)
            if water:
                parts.append(water)

        # 2. Town footprint (land base under the wards).
        parts.append(self._poly_svg(town.footprint, fill=self._pal.footprint))

        # 3. Wards.
        parts.append('<g stroke="%s" stroke-width="1" stroke-linejoin="round">'
                     % self._pal.ward_stroke)
        for ward in town.wards:
            if len(ward.polygon) < 3:
                continue
            fill = self._pal.ward_fill(ward.kind)
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
                                        stroke=self._pal.wall, stroke_width=1.5))

        # 6b. Landmark marker (when a purpose set one) — a star over its ward.
        if town.landmark is not None:
            parts.append(self._landmark_svg(town))

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
        return f'<polygon points="{pts}" fill="{self._pal.water}"/>'

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
        return (f'<g fill="{self._pal.building}" stroke="{self._pal.building_stroke}" '
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
            out.append(f'<path d="{d}" stroke="{self._pal.road_casing}" '
                       f'stroke-width="{w + 1.6:.1f}"/>')
        for d, w in roads:
            out.append(f'<path d="{d}" stroke="{self._pal.road}" stroke-width="{w:.1f}"/>')
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
            f'<path d="{" ".join(segs)}" fill="none" stroke="{self._pal.wall}" '
            f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        ]
        # Towers at each corner.
        out.append(f'<g fill="{self._pal.wall}" stroke="{self._pal.tower_edge}" stroke-width="0.6">')
        for x, y in ring:
            out.append(f'<circle cx="{x * s:.1f}" cy="{y * s:.1f}" r="2.6"/>')
        out.append("</g>")
        # Gatehouses (square) at the gates.
        if wall.gates:
            r = 3.0
            out.append(f'<g fill="{self._pal.wall}" stroke="{self._pal.tower_edge}" stroke-width="0.8">')
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

    def _landmark_svg(self, town: Settlement) -> str:
        """A five-pointed star at the landmark ward's centre, haloed for contrast."""
        s = self.scale
        lm = town.landmark
        cx, cy = lm.center[0] * s, lm.center[1] * s
        outer, inner = 6.5, 2.7
        pts: list[str] = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5  # point up; alternate out/in
            r = outer if i % 2 == 0 else inner
            pts.append(f"{cx + r * math.cos(ang):.1f},{cy + r * math.sin(ang):.1f}")
        poly = " ".join(pts)
        return (
            f'<polygon points="{poly}" fill="{self._pal.wall}" '
            f'stroke="{self._pal.label_halo}" stroke-width="1.4" '
            f'stroke-linejoin="round" paint-order="stroke"/>'
        )

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
                f'stroke="{self._pal.label_halo}" stroke-width="2.5" paint-order="stroke" '
                f'fill="{self._pal.label}">{su.escape(text)}</text>'
            )
        out.append("</g>")
        return "".join(out)

    def _title_svg(self, town: Settlement, w_px: float) -> str:
        return (
            f'<text x="{w_px / 2:.1f}" y="20" text-anchor="middle" '
            f'font-family="Georgia, serif" font-size="16" font-weight="bold" '
            f'stroke="{self._pal.label_halo}" stroke-width="3" paint-order="stroke" '
            f'fill="{self._pal.label}">{su.escape(town.name)}</text>'
        )
