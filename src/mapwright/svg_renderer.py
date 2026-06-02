"""Vector (SVG) renderer for regional terrain, with shaded relief.

Renders a :class:`~src.mapwright.terrain.TerrainResult` as a scalable SVG map:
organic Voronoi biome polygons, slope-based **shaded relief** (the hillshade
technique from rlguy/Mewo2's FantasyMapGenerator — per-cell surface normals lit
by a fixed light direction), a coastline stroke, rivers whose width tracks
discharge, and labelled settlement markers.

Why SVG for this tier (vs the PNG tile compositor used for tactical maps):
world/regional maps want to zoom, restyle, and carry crisp labels — all of which
vector handles for free, and it sidesteps the "fog/grid baked into the PNG"
tech-debt. Everything here is pure string-building (no new dependencies).
"""

from __future__ import annotations

import math
import xml.sax.saxutils as su
from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from . import _serde
from .terrain import Biome, TerrainCell, TerrainResult, compute_cell_polygons


@dataclass
class Marker:
    """A neutral point-of-interest to label on the map (e.g. a settlement).

    Domain-neutral on purpose: a host app maps its own feature objects onto this
    rather than the renderer depending on the host's models. ``kind`` selects the
    marker size (see ``_SETTLEMENT_RADIUS``) and a substring of it (e.g. "city")
    bumps the label font.
    """

    name: str
    x: float
    y: float
    kind: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Marker":
        return cls(**_serde.only_known(cls, data))

    def to_json(self, **kwargs) -> str:
        """Serialise to a JSON string (``kwargs`` pass to :func:`json.dumps`)."""
        return _serde.to_json(self, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "Marker":
        return _serde.from_json(cls, text)


# Base biome fill colours (before relief shading), tuned for a parchment-ish
# fantasy palette rather than the dungeon tile colours.
_BIOME_RGB: dict[Biome, tuple[int, int, int]] = {
    Biome.OCEAN: (31, 78, 107),
    Biome.COAST: (61, 126, 166),
    Biome.BEACH: (217, 199, 155),
    Biome.DESERT: (214, 196, 130),
    Biome.PLAINS: (169, 196, 127),
    Biome.FOREST: (79, 130, 74),
    Biome.SWAMP: (107, 123, 74),
    Biome.HILLS: (160, 154, 100),
    Biome.MOUNTAIN: (140, 132, 122),
    Biome.TUNDRA: (188, 196, 180),
    Biome.SNOW: (240, 244, 248),
    Biome.RIVER: (127, 168, 106),  # riverbank green; the river line draws on top
    Biome.LAKE: (96, 152, 184),    # inland freshwater — lighter than coast, distinct from sea
}

_OCEAN_BG = (24, 62, 86)
_COASTLINE = (40, 54, 64)
_RIVER_COLOR = (74, 130, 175)
_ROAD_COLOR = (110, 78, 50)        # dirt-road brown
_ROAD_CASING = (245, 238, 222)     # pale casing so roads read over any biome
_REGION_BORDER = (120, 36, 44)     # political boundary (dark crimson)
_REGION_LABEL = (74, 24, 30)

_SETTLEMENT_RADIUS = {
    "settlement_city": 5.5,
    "settlement_town": 4.0,
    "settlement_village": 3.0,
    "settlement_castle": 4.5,
}


def _shade(base: tuple[int, int, int], brightness: float) -> str:
    """Apply a relief brightness multiplier to a colour → ``#rrggbb``."""
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(ch * brightness)))) for ch in base
    )


def _rgb(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


class RegionalSVGRenderer:
    """Renders a :class:`TerrainResult` to an SVG document string."""

    def __init__(self, scale: float = 16.0, relief_strength: float = 60.0):
        # scale = pixels per tile-unit; relief_strength exaggerates slope so the
        # hillshade reads on gentle terrain (height diffs between cells are tiny,
        # so this needs to be large).
        self.scale = scale
        self.relief_strength = relief_strength
        # Light from the upper-left (classic cartographic convention).
        lx, ly, lz = -1.0, -1.0, 1.4
        norm = math.sqrt(lx * lx + ly * ly + lz * lz)
        self._light = (lx / norm, ly / norm, lz / norm)

    def render(
        self,
        terrain: TerrainResult,
        markers: Optional[Sequence[Marker]] = None,
        *,
        roads: Optional[Sequence] = None,
        regions: Optional[Sequence] = None,
        show_relief: bool = True,
        show_labels: bool = True,
    ) -> str:
        s = self.scale
        w_px, h_px = terrain.width * s, terrain.height * s
        polys = compute_cell_polygons(terrain.cells, terrain.width, terrain.height)
        brightness = self._relief(terrain.cells) if show_relief else {}

        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px:.0f}" '
            f'height="{h_px:.0f}" viewBox="0 0 {w_px:.0f} {h_px:.0f}">',
            f'<rect width="{w_px:.0f}" height="{h_px:.0f}" fill="{_rgb(_OCEAN_BG)}"/>',
        ]

        # 1. Biome polygons with relief shading.
        parts.append('<g stroke-linejoin="round">')
        for cell in terrain.cells:
            poly = polys.get(cell.id)
            if not poly or len(poly) < 3:
                continue
            fill = _shade(_BIOME_RGB[cell.biome], brightness.get(cell.id, 1.0))
            pts = " ".join(f"{x * s:.1f},{y * s:.1f}" for x, y in poly)
            # A hairline stroke in the fill colour hides seams between cells.
            parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{fill}" '
                         f'stroke-width="0.5"/>')
        parts.append("</g>")

        # 2. Coastline — edges of land cells that border the sea.
        parts.append(self._coastline_svg(terrain, polys))

        # 2b. Region borders — political boundaries between territories.
        if regions:
            parts.append(self._regions_svg(terrain, polys, regions, show_labels))

        # 3. Rivers — downhill polylines, width by discharge.
        parts.append(self._rivers_svg(terrain))

        # 4. Roads — terrain-routed trade routes between settlements.
        if roads:
            parts.append(self._roads_svg(terrain, roads))

        # 5. Settlements.
        if markers:
            parts.append(self._settlements_svg(markers, show_labels))

        parts.append("</svg>")
        return "".join(parts)

    # -- relief ----------------------------------------------------------

    def _relief(self, cells: list[TerrainCell]) -> dict[int, float]:
        """Per-cell brightness from a slope normal lit by the fixed light."""
        out: dict[int, float] = {}
        lx, ly, lz = self._light
        for c in cells:
            if c.is_water or c.is_lake:  # flat water surfaces take no hillshade
                out[c.id] = 1.0
                continue
            gx = gy = 0.0
            count = 0
            for nid in c.neighbors:
                n = cells[nid]
                dx, dy = n.cx - c.cx, n.cy - c.cy
                d2 = dx * dx + dy * dy
                if d2 <= 0:
                    continue
                dh = (n.height - c.height) * self.relief_strength
                gx += dh * dx / d2
                gy += dh * dy / d2
                count += 1
            if count:
                gx /= count
                gy /= count
            # Surface normal of the local plane z = -gx*x - gy*y, lit by the
            # light direction. Steeper slopes facing the light brighten; those
            # facing away darken — classic hillshade.
            nx, ny, nz = -gx, -gy, 1.0
            nlen = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            shade = (nx * lx + ny * ly + nz * lz) / nlen  # ~0..1, ~0.7 when flat
            # Re-centre around the flat-ground value (lz) so flat terrain stays
            # neutral and aspect drives the contrast.
            out[c.id] = max(0.66, min(1.28, 1.0 + 1.1 * (shade - lz)))
        return out

    # -- coastline -------------------------------------------------------

    def _coastline_svg(self, terrain: TerrainResult, polys) -> str:
        s = self.scale
        cells = terrain.cells
        segs: list[str] = []
        eps = 1e-6
        w, h = terrain.width, terrain.height
        for c in cells:
            if c.is_water:
                continue
            poly = polys.get(c.id)
            if not poly or len(poly) < 3:
                continue
            n = len(poly)
            for i in range(n):
                a, b = poly[i], poly[(i + 1) % n]
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                # Skip edges lying on the map border (not a real coastline).
                if mx < eps or my < eps or mx > w - eps or my > h - eps:
                    continue
                # The neighbour across this edge is the nearest cell-site to the
                # edge midpoint (the edge lies on their shared bisector).
                nb = min(c.neighbors,
                         key=lambda k: (cells[k].cx - mx) ** 2 + (cells[k].cy - my) ** 2,
                         default=None)
                if nb is not None and cells[nb].is_water:
                    segs.append(f'M{a[0] * s:.1f},{a[1] * s:.1f} '
                                f'L{b[0] * s:.1f},{b[1] * s:.1f}')
        if not segs:
            return ""
        return (f'<path d="{" ".join(segs)}" fill="none" stroke="{_rgb(_COASTLINE)}" '
                f'stroke-width="2.0" stroke-linecap="round"/>')

    # -- regions ---------------------------------------------------------

    def _regions_svg(self, terrain: TerrainResult, polys, regions, labels: bool) -> str:
        s = self.scale
        cells = terrain.cells
        cell_region: dict[int, int] = {}
        for r in regions:
            for cid in r.cells:
                cell_region[cid] = r.id

        segs: list[str] = []
        eps = 1e-6
        w, h = terrain.width, terrain.height
        for c in cells:
            rid = cell_region.get(c.id)
            if rid is None:
                continue
            poly = polys.get(c.id)
            if not poly or len(poly) < 3:
                continue
            n = len(poly)
            for i in range(n):
                a, b = poly[i], poly[(i + 1) % n]
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                if mx < eps or my < eps or mx > w - eps or my > h - eps:
                    continue
                nb = min(c.neighbors,
                         key=lambda k: (cells[k].cx - mx) ** 2 + (cells[k].cy - my) ** 2,
                         default=None)
                if nb is None or cells[nb].is_water:
                    continue  # the sea edge is the coastline, not a political border
                nrid = cell_region.get(nb)
                if nrid == rid:
                    continue
                # Draw each shared border once (lower region id, or vs unassigned land).
                if nrid is None or rid < nrid:
                    segs.append(f'M{a[0] * s:.1f},{a[1] * s:.1f} '
                                f'L{b[0] * s:.1f},{b[1] * s:.1f}')

        out: list[str] = []
        if segs:
            out.append(f'<path d="{" ".join(segs)}" fill="none" '
                       f'stroke="{_rgb(_REGION_BORDER)}" stroke-width="2.2" '
                       f'stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>')
        if labels:
            out.append('<g font-family="Georgia, serif" font-size="12" '
                       'text-anchor="middle" font-style="italic">')
            for r in regions:
                cap = cells[r.capital]
                out.append(
                    f'<text x="{cap.cx * s:.1f}" y="{cap.cy * s:.1f}" '
                    f'stroke="#f7f3ea" stroke-width="3" paint-order="stroke" '
                    f'fill="{_rgb(_REGION_LABEL)}">{su.escape(r.name)}</text>'
                )
            out.append("</g>")
        return "".join(out)

    # -- rivers ----------------------------------------------------------

    def _rivers_svg(self, terrain: TerrainResult) -> str:
        s = self.scale
        cells = terrain.cells
        paths: list[str] = []
        for river in terrain.rivers:
            if len(river.cells) < 2:
                continue
            pts = [(cells[i].cx * s, cells[i].cy * s) for i in river.cells]
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            width = max(0.8, min(4.0, 0.4 * river.width))
            paths.append(f'<path d="{d}" fill="none" stroke="{_rgb(_RIVER_COLOR)}" '
                         f'stroke-width="{width:.1f}" stroke-linecap="round" '
                         f'stroke-linejoin="round"/>')
        if not paths:
            return ""
        return '<g opacity="0.9">' + "".join(paths) + "</g>"

    # -- roads -----------------------------------------------------------

    def _roads_svg(self, terrain: TerrainResult, roads: Sequence) -> str:
        s = self.scale
        cells = terrain.cells
        ds: list[str] = []
        for road in roads:
            if len(road.cells) < 2:
                continue
            pts = [(cells[i].cx * s, cells[i].cy * s) for i in road.cells]
            ds.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts))
        if not ds:
            return ""
        d = " ".join(ds)
        # Pale casing under a brown surface so routes read over any biome.
        return (
            '<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{d}" stroke="{_rgb(_ROAD_CASING)}" stroke-width="3.4" opacity="0.7"/>'
            f'<path d="{d}" stroke="{_rgb(_ROAD_COLOR)}" stroke-width="1.6" '
            f'stroke-dasharray="5,3"/>'
            "</g>"
        )

    # -- settlements -----------------------------------------------------

    def _settlements_svg(self, markers: Sequence[Marker], labels: bool) -> str:
        s = self.scale
        out: list[str] = ['<g font-family="Georgia, serif">']
        for m in markers:
            r = _SETTLEMENT_RADIUS.get(m.kind, 3.0)
            cx, cy = m.x * s, m.y * s
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
                       f'fill="#f4ead8" stroke="#2b2b2b" stroke-width="1.2"/>')
            if labels and m.name:
                name = su.escape(m.name)
                tx, ty = cx + r + 2, cy + 3
                fs = 11 if "city" in m.kind else 9
                # White halo behind the label for legibility over any biome.
                out.append(
                    f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="{fs}" '
                    f'stroke="#f7f3ea" stroke-width="3" paint-order="stroke" '
                    f'fill="#23211c">{name}</text>'
                )
        out.append("</g>")
        return "".join(out)
