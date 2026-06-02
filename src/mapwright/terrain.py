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
from dataclasses import asdict, dataclass, field
from enum import IntEnum

import numpy as np

from . import _geometry, _serde
from .config import WorldMapConfig
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
    LAKE = 12


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
    is_water: bool = False       # below sea level (ocean)
    is_lake: bool = False        # inland standing water (a filled basin)
    is_river: bool = False
    temperature: float = 0.5     # 0 (frozen) .. 1 (hot)
    moisture: float = 0.5        # 0 (arid) .. 1 (wet)
    biome: Biome = Biome.PLAINS

    def to_dict(self) -> dict:
        """JSON-safe mapping (``biome`` as its int value)."""
        d = asdict(self)
        d["biome"] = int(self.biome)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TerrainCell":
        d = _serde.only_known(cls, data)
        if "biome" in d:
            d["biome"] = Biome(d["biome"])
        return cls(**d)


@dataclass
class River:
    """A traced river as a polyline of cell centroids."""

    cells: list[int]
    width: float

    def to_dict(self) -> dict:
        return {"cells": list(self.cells), "width": self.width}

    @classmethod
    def from_dict(cls, data: dict) -> "River":
        return cls(cells=[int(c) for c in data["cells"]], width=float(data["width"]))


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

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        """A JSON-safe mapping of the whole world.

        Round-trips losslessly through :meth:`from_dict`: floats keep full
        precision and the ``cell_of`` raster is stored as nested lists, so a
        saved world reloads bit-identical (and renders identically).
        """
        return {
            "schema": "mapwright/terrain@2",
            "width": self.width,
            "height": self.height,
            "sea_level": self.sea_level,
            "cells": [c.to_dict() for c in self.cells],
            "cell_of": self.cell_of.tolist(),
            "rivers": [r.to_dict() for r in self.rivers],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TerrainResult":
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            cells=[TerrainCell.from_dict(c) for c in data["cells"]],
            cell_of=np.asarray(data["cell_of"], dtype=np.int32),
            rivers=[River.from_dict(r) for r in data["rivers"]],
            sea_level=float(data["sea_level"]),
        )

    def to_json(self, **kwargs) -> str:
        """Serialise to a JSON string (``kwargs`` pass to :func:`json.dumps`)."""
        return _serde.to_json(self, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "TerrainResult":
        return _serde.from_json(cls, text)


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
        config: WorldMapConfig | None = None,
        *,
        cell_area: float = 6.0,
        relax_iterations: int = 2,
    ) -> TerrainResult:
        """Run the full pipeline and return terrain for a ``width×height`` grid.

        ``config`` (a :class:`WorldMapConfig`) shapes the world — sea level,
        number of continents, climate, mountains, rivers. ``cell_area`` and
        ``relax_iterations`` are quality/detail dials independent of the world's
        character. With no config a balanced single-continent world is produced.
        """
        cfg = config or WorldMapConfig()
        sea_level = cfg.sea_level
        erosion_passes = max(1, round(1 + cfg.roughness * 4))

        n_cells = int(np.clip(round(width * height / cell_area), 16, 1500))
        seeds = _geometry.jittered_grid_seeds(self._rng, width, height, n_cells)
        cell_of, seeds = _geometry.voronoi_grid(width, height, seeds, relax_iterations)
        cells = self._build_cells(seeds, cell_of)

        self._init_heightmap(cells, width, height, cfg)
        for cell in cells:
            cell.is_water = cell.height < sea_level

        for _ in range(erosion_passes):
            self._fill_depressions(cells, sea_level)
            self._compute_flux(cells)
            self._erode(cells, sea_level)
            for cell in cells:
                cell.is_water = cell.height < sea_level

        # Final hydrology pass for stable rivers, then lakes, climate + biomes.
        self._fill_depressions(cells, sea_level)
        self._compute_flux(cells)
        self._assign_lakes(cells, sea_level, cfg.lake_density)
        rivers = self._trace_rivers(cells, cfg.river_density)
        self._compute_climate(cells, width, height, sea_level, cfg)
        self._assign_biomes(cells, sea_level)

        return TerrainResult(
            width=width,
            height=height,
            cells=cells,
            cell_of=cell_of,
            rivers=rivers,
            sea_level=sea_level,
        )

    # -- 1. Voronoi cells (shared geometry) ------------------------------

    def _build_cells(self, seeds: np.ndarray, cell_of: np.ndarray) -> list[TerrainCell]:
        """Construct cells with centroids and grid-adjacency neighbours."""
        cells = [TerrainCell(id=i, cx=float(s[0]), cy=float(s[1])) for i, s in enumerate(seeds)]
        adjacency = _geometry.grid_adjacency(cell_of, len(seeds))
        for cell in cells:
            cell.neighbors = adjacency[cell.id]
        return cells

    # -- 2. Heightmap primitives -----------------------------------------

    def _init_heightmap(
        self, cells: list[TerrainCell], width: int, height: int, cfg: WorldMapConfig
    ) -> None:
        cx, cy = width / 2, height / 2
        diag = math.hypot(width, height)
        centroids = np.array([[c.cx, c.cy] for c in cells])

        def gaussian(px: float, py: float, radius: float, amp: float) -> np.ndarray:
            d2 = (centroids[:, 0] - px) ** 2 + (centroids[:, 1] - py) ** 2
            return amp * np.exp(-d2 / (2 * radius * radius))

        # Major landmasses. One ⇒ a central continent; several ⇒ blobs on a ring.
        n = cfg.continents
        major_radius = diag * 0.28 / (1 + 0.45 * (n - 1))
        centers: list[tuple[float, float]] = []
        if n == 1:
            centers.append((cx + self._rng.fuzzy(0, width * 0.1),
                            cy + self._rng.fuzzy(0, height * 0.1)))
        else:
            ring = cfg.continent_spread * min(width, height) * 0.42
            for i in range(n):
                ang = 2 * math.pi * i / n + self._rng.fuzzy(0, 0.5)
                centers.append((cx + ring * math.cos(ang) + self._rng.fuzzy(0, width * 0.06),
                                cy + ring * math.sin(ang) + self._rng.fuzzy(0, height * 0.06)))

        # Combine landmasses with MAX (not sum) so adjacent continents don't form
        # additive land-bridges between them — that's what makes islands islands.
        h = np.zeros(len(cells))
        for px, py in centers:
            amp = 1.0 if n == 1 else self._rng.uniform(0.8, 1.0)
            h = np.maximum(h, gaussian(px, py, major_radius, amp))

        # Hills/ranges — count and height scale with mountain_density. Anchored
        # to a landmass and *added* on top, so they decorate continents.
        n_small = round(2 + cfg.mountain_density * 8)
        spread = major_radius * 0.55  # keep ranges on their landmass (no bridging)
        for _ in range(n_small):
            bx, by = self._rng.choice(centers)
            h = h + gaussian(bx + self._rng.fuzzy(0, spread),
                             by + self._rng.fuzzy(0, spread),
                             radius=diag * self._rng.uniform(0.06, 0.16),
                             amp=self._rng.uniform(0.2, 0.35 + cfg.mountain_density * 0.5))

        # Radial edge falloff: push the map border below sea level so the map
        # reads as land ringed by sea (and gives real coastlines).
        d_edge = np.sqrt((centroids[:, 0] - cx) ** 2 + (centroids[:, 1] - cy) ** 2) / (diag / 2)
        h = h - np.clip((d_edge - 0.45) / 0.55, 0, 1) * (1.15 * cfg.edge_falloff)

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
    def _trace_rivers(cells: list[TerrainCell], river_density: float) -> list[River]:
        """Trace downhill polylines from genuine high-flux river sources.

        Rivers are *rare* — only trunk streams that gathered real drainage. We
        pick sources above a flux quantile (with an absolute floor so small maps
        don't over-river), follow each to the sea, and only then mark the cells
        of kept rivers as ``is_river`` — so short, spurious paths never paint the
        interior blue. ``river_density`` (0..1) lowers both the quantile and the
        floor so more, smaller rivers appear.
        """
        land = [c for c in cells if not c.is_water]
        if not land:
            return []
        # A source needs accumulated flux above a threshold that scales with map
        # size (so big maps aren't flooded) and shrinks with river_density, so
        # higher density ⇒ lower bar ⇒ more (and smaller) rivers.
        cutoff = max(5.0, (0.10 - 0.075 * river_density) * len(cells))
        rivers: list[River] = []
        used: set[int] = set()
        sources = sorted(
            (c for c in cells if not c.is_water and not c.is_lake and c.flux >= cutoff),
            key=lambda c: c.flux, reverse=True,
        )
        for src in sources:
            if src.id in used:
                continue
            path, cur = [], src
            # Follow the trunk downhill to the sea or into a lake (or an existing river).
            while cur is not None and not cur.is_water and not cur.is_lake:
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

    # -- 5b. Lakes -------------------------------------------------------

    @staticmethod
    def _assign_lakes(cells: list[TerrainCell], sea_level: float, lake_density: float) -> None:
        """Flag inland *hollows* as lakes — interior land cells that sit lower than
        most of their neighbours and collect real flow, so water would pool there.

        (The depression-fill leaves basins nearly flat, so a fill-depth test finds
        almost nothing; ranking hollows by accumulated flux is the reliable signal.)
        ``lake_density`` scales how many of the ranked hollows fill."""
        if lake_density <= 0:
            return
        interior = [
            c for c in cells
            if not c.is_water and c.height >= sea_level
            and not any(cells[n].is_water for n in c.neighbors)
        ]
        hollows = [
            c for c in interior
            if c.neighbors
            and sum(1 for n in c.neighbors if cells[n].height > c.height + 1e-6)
            >= 0.6 * len(c.neighbors)
            and c.flux >= 3.0
        ]
        hollows.sort(key=lambda c: c.flux, reverse=True)
        k = round(lake_density * len(interior) * 0.06)
        for c in hollows[:k]:
            c.is_lake = True

    # -- 6. Climate ------------------------------------------------------

    def _compute_climate(
        self, cells: list[TerrainCell], width: int, height: int, sea_level: float,
        cfg: WorldMapConfig,
    ) -> None:
        # Temperature: warm band at a randomly placed "equator" latitude, minus
        # an elevation lapse rate so peaks are cold, plus a global config bias.
        equator = self._rng.uniform(0.35, 0.65)
        for c in cells:
            lat = c.cy / max(1, height - 1)
            temp = 1.0 - 2.0 * abs(lat - equator)
            temp -= 0.6 * max(0.0, c.height - sea_level)  # lapse with elevation
            temp += cfg.temperature                        # global bias
            c.temperature = float(np.clip(temp + self._rng.fuzzy(0, 0.05), 0.0, 1.0))

        # Moisture: multi-source BFS hop-distance from water (sea, lakes, rivers
        # feed the air) over the cell graph, decaying inland.
        dist = {c.id: math.inf for c in cells}
        q: deque[int] = deque()
        for c in cells:
            if c.is_water or c.is_lake:
                dist[c.id] = 0
                q.append(c.id)
        while q:
            cid = q.popleft()
            for n in cells[cid].neighbors:
                if dist[n] > dist[cid] + 1:
                    dist[n] = dist[cid] + 1
                    q.append(n)

        # Rain shadow: a prevailing wind carries moisture inland; air rising over
        # high terrain precipitates (wet windward slopes) and arrives dry on the
        # lee, so each cell's "exposure" is the moisture still carried by the air
        # reaching it. Swept upwind→downwind over the cell graph.
        exposure = self._rain_shadow(cells)

        scale = max(3.0, (width + height) / 12.0)
        for c in cells:
            base = math.exp(-dist[c.id] / scale)
            if c.is_river:
                base = min(1.0, base + 0.35)
            base *= 0.55 + 0.45 * exposure[c.id]  # attenuate in the rain shadow
            base += cfg.moisture * 0.5            # global bias
            c.moisture = float(np.clip(base + self._rng.fuzzy(0, 0.05), 0.0, 1.0))

    def _rain_shadow(self, cells: list[TerrainCell]) -> dict[int, float]:
        """Per-cell air moisture (0..1) after orographic loss, swept along a
        prevailing wind. Water cells start saturated; land cells inherit the air
        from their wettest upwind neighbour minus what rising terrain wrings out."""
        wind = self._rng.uniform(0.0, 2.0 * math.pi)
        wx, wy = math.cos(wind), math.sin(wind)
        proj = {c.id: c.cx * wx + c.cy * wy for c in cells}
        exposure: dict[int, float] = {}
        carried: dict[int, float] = {}
        for c in sorted(cells, key=lambda c: proj[c.id]):  # upwind → downwind
            if c.is_water or c.is_lake:
                exposure[c.id] = carried[c.id] = 1.0
                continue
            # Air arrives from the wettest strictly-upwind neighbour.
            air, src_h = 0.5, c.height
            for nid in c.neighbors:
                if proj[nid] < proj[c.id] and carried.get(nid, -1.0) > air:
                    air, src_h = carried[nid], cells[nid].height
            rise = max(0.0, c.height - src_h)         # uplift across this step
            precip = air * min(1.0, 0.12 + rise * 6.0)  # mountains wring out more
            exposure[c.id] = air
            carried[c.id] = max(0.0, air - precip)
        return exposure

    # -- 7. Biome assignment ---------------------------------------------

    def _assign_biomes(self, cells: list[TerrainCell], sea_level: float) -> None:
        water_ids = {c.id for c in cells if c.is_water}
        for c in cells:
            if c.is_water:
                # Shoreline water (touching land) reads as coast/shallows.
                touches_land = any(n not in water_ids for n in c.neighbors)
                c.biome = Biome.COAST if touches_land else Biome.OCEAN
                continue
            if c.is_lake:
                c.biome = Biome.LAKE
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
# Cells store only centroids + adjacency, so vector output rebuilds each cell's
# convex polygon by half-plane clipping (see :mod:`mapwright._geometry`). This is
# a thin, terrain-typed wrapper over the shared primitive so the public API and
# the dungeon/settlement tiers share one implementation.
# ---------------------------------------------------------------------------

Point = tuple[float, float]


def compute_cell_polygons(
    cells: list[TerrainCell], width: int, height: int
) -> dict[int, list[Point]]:
    """Return a convex polygon (list of points) for each cell, clipped to the map."""
    centroids = {c.id: (c.cx, c.cy) for c in cells}
    neighbors = {c.id: c.neighbors for c in cells}
    return _geometry.voronoi_polygons(centroids, neighbors, width, height)
