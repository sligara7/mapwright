"""Procedural regional terrain: Voronoi cells, erosion, rivers, biomes.

A clean-room Python port of the *ideas* in Azgaar's Fantasy-Map-Generator (MIT)
and rlguy/Mewo2's FantasyMapGenerator (Zlib) — no code copied from either. It
replaces the old ``sin/cos`` noise in ``layout_generator._generate_regional_terrain``
with a physically-motivated pipeline that produces organic coastlines, drainage
basins, rivers, and climate-driven biomes.

Pipeline (all on a Voronoi cell graph, then rasterised to the tile grid):

  1. **Voronoi cells** — jittered seed points + nearest-seed assignment, refined
     by a couple of Lloyd-relaxation passes (the FMG/Watabou trick for evenly
     organic cells). Done in pure numpy; no scipy dependency.
  2. **Heightmap** — additive primitives (a central landmass hill, a few random
     hills/ranges) minus a radial edge falloff so the map reads as a continent
     ringed by sea.
  3. **Planchon–Darboux depression fill** — guarantees every land cell drains to
     the sea, so flux/rivers never dead-end in a pit.
  4. **Flux + hydraulic erosion** — water flows to the lowest neighbour, flux
     accumulates downstream, and height is lowered by
     ``river·√flux·slope + creep·slope²`` (FantasyMapGenerator's hybrid model),
     carving valleys. Iterated a few passes.
  5. **Rivers** — cells whose accumulated flux exceeds a threshold trace a
     downhill polyline; width scales with √flux.
  6. **Climate** — temperature from latitude minus an elevation lapse; moisture
     from graph distance to water plus river proximity.
  7. **Biomes** — a Whittaker-style temperature×moisture matrix → biome → tile.

Everything is driven by a single :class:`SeededRNG`, so a seed reproduces the
whole world. The cell model is deliberately geometry-light (centroids + adjacency)
so a future SVG/graph exporter can swap nearest-seed cells for true
``scipy.spatial.Voronoi`` polygons without touching the hydrology/climate code.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np

from .rng import SeededRNG


class Biome(IntEnum):
    """Coarse biome classes. Mapping to a host app's tile vocabulary is the
    consumer's job (this library stays domain-neutral)."""

    OCEAN = 0
    COAST = 1
    BEACH = 2
    DESERT = 3
    PLAINS = 4
    FOREST = 5
    SWAMP = 6
    HILLS = 7
    MOUNTAIN = 8
    TUNDRA = 9
    SNOW = 10
    RIVER = 11


@dataclass
class TerrainCell:
    """One Voronoi cell with its terrain/hydrology/climate state."""

    id: int
    cx: float
    cy: float
    neighbors: list[int] = field(default_factory=list)
    height: float = 0.0          # 0..1, sea level is generation sea_level
    filled: float = 0.0          # depression-filled height (for drainage)
    flux: float = 1.0            # accumulated upstream flow
    downhill: int = -1           # cell id water flows to, or -1 at the sea
    is_water: bool = False
    is_river: bool = False
    temperature: float = 0.5     # 0 (frozen) .. 1 (hot)
    moisture: float = 0.5        # 0 (arid) .. 1 (wet)
    biome: Biome = Biome.PLAINS


@dataclass
class River:
    """A traced river as a polyline of cell centroids."""

    cells: list[int]
    width: float


@dataclass
class TerrainResult:
    """Full regional terrain output: cells, the rasterised grid, and rivers."""

    width: int
    height: int
    cells: list[TerrainCell]
    cell_of: np.ndarray          # int grid [height, width] -> cell id
    rivers: list[River]
    sea_level: float

    def elevation_at(self, cell: "TerrainCell") -> float:
        """Normalised height above sea level, 0..1 — handy for rasterisers."""
        return max(0.0, (cell.height - self.sea_level) / max(1e-6, 1 - self.sea_level))


class RegionalTerrainGenerator:
    """Builds :class:`TerrainResult` for a width×height regional map."""

    def __init__(self, rng: SeededRNG):
        # All terrain draws come from a derived sub-stream so terrain stays
        # decoupled from naming/placement streams sharing the same root seed.
        self._rng = rng.derive("terrain")
        self._np = self._rng.numpy

    # -- public entry ----------------------------------------------------

    def generate(
        self,
        width: int,
        height: int,
        *,
        sea_level: float = 0.32,
        cell_area: float = 6.0,
        relax_iterations: int = 2,
        erosion_passes: int = 3,
        river_threshold: float = 0.55,
    ) -> TerrainResult:
        """Run the full pipeline and return terrain for a ``width×height`` grid."""
        n_cells = int(np.clip(round(width * height / cell_area), 16, 1500))
        seeds = self._sample_seeds(width, height, n_cells)
        cell_of, seeds = self._voronoi(width, height, seeds, relax_iterations)
        cells = self._build_cells(seeds, cell_of)

        self._init_heightmap(cells, width, height)
        for cell in cells:
            cell.is_water = cell.height < sea_level

        for _ in range(erosion_passes):
            self._fill_depressions(cells, sea_level)
            self._compute_flux(cells)
            self._erode(cells, sea_level)
            for cell in cells:
                cell.is_water = cell.height < sea_level

        # Final hydrology pass for stable rivers, then climate + biomes.
        self._fill_depressions(cells, sea_level)
        self._compute_flux(cells)
        rivers = self._trace_rivers(cells, river_threshold)
        self._compute_climate(cells, width, height, sea_level)
        self._assign_biomes(cells, sea_level)

        return TerrainResult(
            width=width,
            height=height,
            cells=cells,
            cell_of=cell_of,
            rivers=rivers,
            sea_level=sea_level,
        )

    # -- 1. Voronoi cells (pure numpy) -----------------------------------

    def _sample_seeds(self, width: int, height: int, n: int) -> np.ndarray:
        """Jittered-grid seed points — even coverage without clumping."""
        cols = max(1, int(round(math.sqrt(n * width / max(1, height)))))
        rows = max(1, int(math.ceil(n / cols)))
        cw, ch = width / cols, height / rows
        pts = []
        for r in range(rows):
            for c in range(cols):
                jx = self._rng.random()
                jy = self._rng.random()
                pts.append(((c + jx) * cw, (r + jy) * ch))
        return np.array(pts[:n] if len(pts) >= n else pts, dtype=float)

    def _voronoi(
        self, width: int, height: int, seeds: np.ndarray, relax: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Assign every grid cell to its nearest seed, with Lloyd relaxation."""
        xs, ys = np.meshgrid(np.arange(width), np.arange(height))
        coords = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(float)
        cell_of = self._nearest(coords, seeds).reshape(height, width)

        for _ in range(relax):
            # Move each seed to its region's centroid, then reassign.
            new_seeds = seeds.copy()
            flat = cell_of.ravel()
            for cid in range(len(seeds)):
                mask = flat == cid
                if mask.any():
                    new_seeds[cid] = coords[mask].mean(axis=0)
            seeds = new_seeds
            cell_of = self._nearest(coords, seeds).reshape(height, width)
        return cell_of, seeds

    @staticmethod
    def _nearest(coords: np.ndarray, seeds: np.ndarray) -> np.ndarray:
        """Nearest-seed index per coord, computed in blocks to bound memory."""
        p, n = coords.shape[0], seeds.shape[0]
        out = np.empty(p, dtype=np.int32)
        block = max(1, int(4_000_000 / max(1, n)))  # ~4M float cap per block
        for start in range(0, p, block):
            chunk = coords[start : start + block]
            d2 = ((chunk[:, None, :] - seeds[None, :, :]) ** 2).sum(axis=2)
            out[start : start + block] = d2.argmin(axis=1)
        return out

    def _build_cells(self, seeds: np.ndarray, cell_of: np.ndarray) -> list[TerrainCell]:
        """Construct cells with centroids and grid-adjacency neighbours."""
        cells = [TerrainCell(id=i, cx=float(s[0]), cy=float(s[1])) for i, s in enumerate(seeds)]
        neigh: list[set[int]] = [set() for _ in seeds]
        # Two cells are adjacent if they touch horizontally or vertically.
        a = cell_of[:, :-1]
        b = cell_of[:, 1:]
        for u, v in np.unique(np.stack([a.ravel(), b.ravel()], axis=1), axis=0):
            if u != v:
                neigh[u].add(int(v))
                neigh[v].add(int(u))
        a = cell_of[:-1, :]
        b = cell_of[1:, :]
        for u, v in np.unique(np.stack([a.ravel(), b.ravel()], axis=1), axis=0):
            if u != v:
                neigh[u].add(int(v))
                neigh[v].add(int(u))
        for cell in cells:
            cell.neighbors = sorted(neigh[cell.id])
        return cells

    # -- 2. Heightmap primitives -----------------------------------------

    def _init_heightmap(self, cells: list[TerrainCell], width: int, height: int) -> None:
        cx, cy = width / 2, height / 2
        diag = math.hypot(width, height)
        h = np.zeros(len(cells))
        centroids = np.array([[c.cx, c.cy] for c in cells])

        def add_hill(px: float, py: float, radius: float, amp: float) -> None:
            d2 = (centroids[:, 0] - px) ** 2 + (centroids[:, 1] - py) ** 2
            nonlocal h
            h = h + amp * np.exp(-d2 / (2 * radius * radius))

        # One broad central landmass...
        add_hill(cx + self._rng.fuzzy(0, width * 0.1),
                 cy + self._rng.fuzzy(0, height * 0.1),
                 radius=diag * 0.28, amp=1.0)
        # ...plus a handful of smaller hills/ranges for interest, kept inside
        # the central region so they don't push the coastline off the map edge.
        for _ in range(self._rng.randint(3, 6)):
            add_hill(self._rng.uniform(0.18, 0.82) * width,
                     self._rng.uniform(0.18, 0.82) * height,
                     radius=diag * self._rng.uniform(0.07, 0.16),
                     amp=self._rng.uniform(0.3, 0.7))

        # Radial edge falloff: push the map border below sea level so the map
        # reads as a continent ringed by sea (and gives real coastlines).
        d_edge = np.sqrt((centroids[:, 0] - cx) ** 2 + (centroids[:, 1] - cy) ** 2) / (diag / 2)
        h = h - np.clip((d_edge - 0.45) / 0.55, 0, 1) * 1.15

        # Normalise to 0..1.
        h = h - h.min()
        if h.max() > 0:
            h = h / h.max()
        for cell, hv in zip(cells, h):
            cell.height = float(hv)

    # -- 3. Planchon–Darboux depression fill -----------------------------

    @staticmethod
    def _fill_depressions(cells: list[TerrainCell], sea_level: float, epsilon: float = 1e-4) -> None:
        """Raise pits so every land cell has a downhill path to the sea."""
        INF = float("inf")
        for c in cells:
            # Water cells (and thus the sea) are fixed outlets at their height.
            c.filled = c.height if c.height < sea_level else INF
        changed = True
        # Iterate to a fixed point; small cell counts make this cheap.
        while changed:
            changed = False
            for c in cells:
                if c.filled == c.height:
                    continue
                lowest = min((cells[n].filled for n in c.neighbors), default=INF)
                candidate = max(c.height, lowest + epsilon)
                if candidate < c.filled:
                    c.filled = candidate
                    changed = True

    # -- 4. Flux + hydraulic erosion -------------------------------------

    @staticmethod
    def _compute_flux(cells: list[TerrainCell]) -> None:
        """Route flow downhill on filled heights and accumulate flux."""
        for c in cells:
            c.flux = 1.0
            c.downhill = -1
        land = [c for c in cells if c.height >= 0 and not c.is_water]
        for c in cells:
            if c.is_water:
                continue
            # Steepest-descent neighbour on the filled surface.
            best, best_h = -1, c.filled
            for n in c.neighbors:
                if cells[n].filled < best_h:
                    best_h, best = cells[n].filled, n
            c.downhill = best
        # Accumulate from high to low so upstream flux is ready first.
        for c in sorted(land, key=lambda c: c.filled, reverse=True):
            if c.downhill >= 0:
                cells[c.downhill].flux += c.flux

    def _erode(self, cells: list[TerrainCell], sea_level: float,
               river_factor: float = 0.06, creep_factor: float = 0.02,
               max_rate: float = 0.045) -> None:
        """Lower each land cell by hydraulic + creep erosion (capped)."""
        for c in cells:
            if c.is_water or c.downhill < 0:
                continue
            down = cells[c.downhill]
            dist = math.hypot(c.cx - down.cx, c.cy - down.cy) or 1.0
            slope = max(0.0, (c.filled - down.filled) / dist)
            erosion = river_factor * math.sqrt(c.flux) * slope + creep_factor * slope * slope
            c.height = max(sea_level * 0.5, c.height - min(erosion, max_rate))

    # -- 5. Rivers -------------------------------------------------------

    @staticmethod
    def _trace_rivers(cells: list[TerrainCell], threshold_frac: float) -> list[River]:
        """Trace downhill polylines from genuine high-flux river sources.

        Rivers must be *rare* — only the trunk streams that have gathered real
        drainage. We pick sources above a high flux quantile (with an absolute
        floor so small maps don't over-river), follow each to the sea, and only
        then mark the cells of kept rivers as ``is_river`` — so short, spurious
        paths never paint the interior blue.
        """
        land_flux = [c.flux for c in cells if not c.is_water]
        if not land_flux:
            return []
        # High bar for a "source"; floor scales with basin size.
        cutoff = max(float(np.quantile(land_flux, 0.90)), 0.04 * len(cells), 6.0)
        rivers: list[River] = []
        used: set[int] = set()
        sources = sorted(
            (c for c in cells if not c.is_water and c.flux >= cutoff),
            key=lambda c: c.flux, reverse=True,
        )
        for src in sources:
            if src.id in used:
                continue
            path, cur = [], src
            # Follow the trunk downhill to the sea (or into an existing river).
            while cur is not None and not cur.is_water:
                path.append(cur.id)
                if cur.id in used:
                    break  # merged into a previously traced river
                cur = cells[cur.downhill] if cur.downhill >= 0 else None
            if len(path) >= 2:
                for cid in path:
                    used.add(cid)
                    cells[cid].is_river = True
                rivers.append(River(cells=path, width=math.sqrt(src.flux)))
        return rivers

    # -- 6. Climate ------------------------------------------------------

    def _compute_climate(
        self, cells: list[TerrainCell], width: int, height: int, sea_level: float
    ) -> None:
        # Temperature: warm band at a randomly placed "equator" latitude, minus
        # an elevation lapse rate so peaks are cold.
        equator = self._rng.uniform(0.35, 0.65)
        for c in cells:
            lat = c.cy / max(1, height - 1)
            temp = 1.0 - 2.0 * abs(lat - equator)
            temp -= 0.6 * max(0.0, c.height - sea_level)  # lapse with elevation
            c.temperature = float(np.clip(temp + self._rng.fuzzy(0, 0.05), 0.0, 1.0))

        # Moisture: multi-source BFS hop-distance from water over the cell graph,
        # decaying inland; rivers add local moisture.
        dist = {c.id: math.inf for c in cells}
        q: deque[int] = deque()
        for c in cells:
            if c.is_water:
                dist[c.id] = 0
                q.append(c.id)
        while q:
            cid = q.popleft()
            for n in cells[cid].neighbors:
                if dist[n] > dist[cid] + 1:
                    dist[n] = dist[cid] + 1
                    q.append(n)
        scale = max(3.0, (width + height) / 12.0)
        for c in cells:
            base = math.exp(-dist[c.id] / scale)
            if c.is_river:
                base = min(1.0, base + 0.35)
            c.moisture = float(np.clip(base + self._rng.fuzzy(0, 0.05), 0.0, 1.0))

    # -- 7. Biome assignment ---------------------------------------------

    def _assign_biomes(self, cells: list[TerrainCell], sea_level: float) -> None:
        water_ids = {c.id for c in cells if c.is_water}
        for c in cells:
            if c.is_water:
                # Shoreline water (touching land) reads as coast/shallows.
                touches_land = any(n not in water_ids for n in c.neighbors)
                c.biome = Biome.COAST if touches_land else Biome.OCEAN
                continue
            if c.is_river:
                c.biome = Biome.RIVER
                continue
            c.biome = self._whittaker(c, sea_level, water_ids)

    @staticmethod
    def _whittaker(c: TerrainCell, sea_level: float, water_ids: set[int]) -> Biome:
        """Temperature×moisture×elevation → biome (Whittaker-style matrix)."""
        rel = (c.height - sea_level) / max(1e-6, 1 - sea_level)  # 0..1 above sea
        t, m = c.temperature, c.moisture

        # Elevation dominates at the extremes.
        if rel > 0.72:
            return Biome.SNOW if t < 0.35 else Biome.MOUNTAIN
        if rel > 0.48:
            return Biome.HILLS

        touches_sea = any(n in water_ids for n in c.neighbors)

        # Land just above the waterline that touches the sea → beach.
        if rel < 0.06 and touches_sea:
            return Biome.BEACH

        # Inland low-lying very wet ground → swamp. Gated off the coast so we
        # don't paint a swamp ring around every shoreline.
        if m > 0.82 and rel < 0.15 and not touches_sea:
            return Biome.SWAMP

        if t < 0.25:
            return Biome.TUNDRA
        if t > 0.7:
            return Biome.DESERT if m < 0.3 else Biome.FOREST
        # Temperate band.
        if m < 0.33:
            return Biome.PLAINS
        return Biome.FOREST if m > 0.5 else Biome.PLAINS


# ---------------------------------------------------------------------------
# Voronoi polygon reconstruction (for vector/SVG rendering).
#
# The cell graph stores only centroids + adjacency, so for vector output we
# rebuild each cell's convex polygon by clipping the map rectangle with the
# perpendicular bisector between the cell and each of its neighbours
# (Sutherland–Hodgman half-plane clipping). Pure Python, no scipy — exact for
# the relaxed seed sites we generate.
# ---------------------------------------------------------------------------

Point = tuple[float, float]


def _clip_halfplane(poly: list[Point], mx: float, my: float, ax: float, ay: float) -> list[Point]:
    """Keep the part of ``poly`` on the ``c`` side of a bisector.

    The half-plane is ``{p : (p - m)·a <= 0}`` where ``m`` is the bisector
    midpoint and ``a`` points from the cell toward its neighbour.
    """
    def inside(p: Point) -> bool:
        return (p[0] - mx) * ax + (p[1] - my) * ay <= 1e-9

    def intersect(a: Point, b: Point) -> Point:
        dx, dy = b[0] - a[0], b[1] - a[1]
        denom = dx * ax + dy * ay
        if abs(denom) < 1e-12:
            return a
        t = ((mx - a[0]) * ax + (my - a[1]) * ay) / denom
        return (a[0] + t * dx, a[1] + t * dy)

    out: list[Point] = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        a_in, b_in = inside(a), inside(b)
        if a_in:
            out.append(a)
        if a_in != b_in:
            out.append(intersect(a, b))
    return out


def compute_cell_polygons(
    cells: list[TerrainCell], width: int, height: int
) -> dict[int, list[Point]]:
    """Return a convex polygon (list of points) for each cell, clipped to the map."""
    rect: list[Point] = [(0.0, 0.0), (float(width), 0.0),
                         (float(width), float(height)), (0.0, float(height))]
    polys: dict[int, list[Point]] = {}
    for c in cells:
        poly = rect
        for nid in c.neighbors:
            n = cells[nid]
            mx, my = (c.cx + n.cx) / 2, (c.cy + n.cy) / 2
            poly = _clip_halfplane(poly, mx, my, n.cx - c.cx, n.cy - c.cy)
            if len(poly) < 3:
                break
        polys[c.id] = poly
    return polys
