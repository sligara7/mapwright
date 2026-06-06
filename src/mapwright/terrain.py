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
  2. **Heightmap** — an organic land/sea *mask* (a radial island bias near each
     continent centre + fractal value noise, so coastlines follow noise contours
     and come out irregular) combined with a separate, smooth *elevation* field
     (graph distance-to-coast + mountain relief + low-frequency valleys). Splitting
     shape from elevation keeps the coast organic without pitting the drainage.
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
from typing import Callable, Sequence, Union

import numpy as np

from . import _geometry, _serde
from .config import WorldMapConfig
from .rng import SeededRNG

# A caller-supplied macro shape for the land: either a coarse 2D grid of
# elevations (rows = north→south, cols = west→east; any numeric range — only the
# relative ordering matters) bilinearly sampled across the map, or a callable
# ``f(x_norm, y_norm) -> elevation`` taking normalised coords in [0, 1].
ElevationHint = Union[Sequence[Sequence[float]], np.ndarray, Callable[[float, float], float]]


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


# Heightmap templates — clean-room from the *idea* in Azgaar's FMG: a heightmap is
# composed from a few elevation ops, and named templates produce recognizable
# continent archetypes. Each op (see RegionalTerrainGenerator._template_raw):
#   ("hill"/"pit",  count(min,max), power(min,max), spread, x_range, y_range)
#   ("range"/"trough", count(min,max), power(min,max), from(x0,x1,y0,y1), to(...))
#   ("strait", width_fraction, vertical?)
# Positions/ranges are fractions of width/height; `spread` 0..1 = small..broad.
# A template sets the *pattern* of high/low ground; `sea_level` (config) still
# decides how much of it is under water (percentile), so combine e.g.
# template="archipelago" with a high sea_level.
TERRAIN_TEMPLATES: dict[str, list[tuple]] = {
    "continents": [
        ("hill", (1, 2), (0.7, 0.95), 0.85, (0.22, 0.5), (0.3, 0.7)),
        ("hill", (1, 2), (0.7, 0.95), 0.85, (0.55, 0.82), (0.3, 0.7)),
        ("hill", (4, 6), (0.25, 0.45), 0.6, (0.2, 0.8), (0.2, 0.8)),
        ("range", (1, 2), (0.6, 0.9), (0.25, 0.4, 0.3, 0.7), (0.6, 0.78, 0.3, 0.7)),
        ("pit", (2, 3), (0.3, 0.5), 0.55, (0.25, 0.75), (0.25, 0.75)),
    ],
    "archipelago": [
        ("hill", (12, 18), (0.4, 0.7), 0.42, (0.1, 0.9), (0.1, 0.9)),
        ("range", (1, 2), (0.4, 0.6), (0.2, 0.4, 0.2, 0.8), (0.6, 0.8, 0.2, 0.8)),
        ("strait", 0.12, True),
        ("strait", 0.10, False),
    ],
    "peninsula": [
        ("hill", (3, 5), (0.5, 0.85), 0.62, (0.3, 0.7), (0.05, 0.45)),
        ("range", (1, 1), (0.6, 0.9), (0.45, 0.55, 0.1, 0.2), (0.45, 0.55, 0.6, 0.92)),
        ("hill", (4, 7), (0.3, 0.5), 0.5, (0.35, 0.65), (0.4, 0.95)),
    ],
    "isthmus": [
        ("hill", (3, 5), (0.6, 0.9), 0.62, (0.1, 0.4), (0.2, 0.8)),
        ("hill", (3, 5), (0.6, 0.9), 0.62, (0.6, 0.9), (0.2, 0.8)),
        ("range", (1, 1), (0.45, 0.65), (0.35, 0.45, 0.45, 0.55), (0.55, 0.65, 0.45, 0.55)),
        ("strait", 0.16, False),
    ],
    "volcano": [
        ("hill", (1, 1), (0.95, 1.1), 0.72, (0.45, 0.55), (0.45, 0.55)),
        ("hill", (3, 5), (0.25, 0.4), 0.5, (0.3, 0.7), (0.3, 0.7)),
        ("pit", (1, 1), (0.5, 0.7), 0.34, (0.47, 0.53), (0.47, 0.53)),
    ],
    "atoll": [
        ("hill", (1, 1), (0.7, 0.9), 0.8, (0.45, 0.55), (0.45, 0.55)),
        ("pit", (1, 1), (0.65, 0.85), 0.5, (0.45, 0.55), (0.45, 0.55)),
        ("hill", (5, 8), (0.2, 0.35), 0.4, (0.25, 0.75), (0.25, 0.75)),
    ],
}


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
        template: str = "",
        elevation_hint: ElevationHint | None = None,
    ) -> TerrainResult:
        """Run the full pipeline and return terrain for a ``width×height`` grid.

        ``config`` (a :class:`WorldMapConfig`) shapes the world — sea level,
        number of continents, climate, mountains, rivers. ``cell_area`` and
        ``relax_iterations`` are quality/detail dials independent of the world's
        character. With no config a balanced single-continent world is produced.

        ``template`` (a name in :data:`TERRAIN_TEMPLATES`, e.g. ``"archipelago"``,
        ``"volcano"``) selects a composed-op heightmap *archetype* instead of the
        default tectonic-plate auto-generation; ``config`` still drives sea level,
        climate, rivers, etc. on top of it.

        ``elevation_hint`` lets a host (or an LLM) **art-direct the macro shape** —
        where land, sea and high ground sit — while mapwright fills in organic
        coastlines, erosion, rivers and climate. Pass a coarse 2D grid of
        elevations (e.g. a 16×16 nested list; rows north→south, cols west→east) or
        a callable ``f(x_norm, y_norm) -> elevation`` over normalised [0, 1] coords.
        Only the *relative* ordering of the values matters; ``sea_level`` still sets
        how much floods (the lowest ``sea_level`` fraction of the hinted surface
        becomes water). It takes precedence over ``template``. Set
        ``edge_falloff=0`` in the config to let the hint place land at the borders.
        """
        cfg = config or WorldMapConfig()
        sea_level = cfg.sea_level
        erosion_passes = max(1, round(1 + cfg.roughness * 4))

        n_cells = int(np.clip(round(width * height / cell_area), 16, 1500))
        seeds = _geometry.jittered_grid_seeds(self._rng, width, height, n_cells)
        cell_of, seeds = _geometry.voronoi_grid(width, height, seeds, relax_iterations)
        cells = self._build_cells(seeds, cell_of)

        self._init_heightmap(cells, width, height, cfg, template=template,
                             elevation_hint=elevation_hint)
        for cell in cells:
            cell.is_water = cell.height < sea_level

        for _ in range(erosion_passes):
            self._fill_depressions(cells, sea_level)
            self._compute_flux(cells)
            self._erode(cells, sea_level)
            for cell in cells:
                cell.is_water = cell.height < sea_level

        # Weathering: old land (age > 0.5) is worn smooth — rounded peaks, gentler
        # relief; young/default land keeps its sharp edges (0 passes ⇒ unchanged).
        self._weather(cells, round(max(0.0, cfg.land_age - 0.5) * 6))
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

    # -- noise helpers (organic coastlines) ------------------------------

    def _value_noise(self, centroids: np.ndarray, width: int, height: int, res: int) -> np.ndarray:
        """Smooth value noise in [0,1): a ``res×res`` random lattice (seeded),
        smoothstep-interpolated at each centroid."""
        lattice = self._np.random((res + 1, res + 1))
        u = np.clip(centroids[:, 0] / max(1, width), 0, 1) * res
        v = np.clip(centroids[:, 1] / max(1, height), 0, 1) * res
        x0 = np.floor(u).astype(int)
        y0 = np.floor(v).astype(int)
        x1 = np.minimum(x0 + 1, res)
        y1 = np.minimum(y0 + 1, res)
        fx, fy = u - x0, v - y0
        sx = fx * fx * (3 - 2 * fx)  # smoothstep → organic (non-blocky) gradients
        sy = fy * fy * (3 - 2 * fy)
        top = lattice[y0, x0] * (1 - sx) + lattice[y0, x1] * sx
        bot = lattice[y1, x0] * (1 - sx) + lattice[y1, x1] * sx
        return top * (1 - sy) + bot * sy

    def _fbm(self, centroids: np.ndarray, width: int, height: int,
             octaves: int = 5, base_res: int = 3) -> np.ndarray:
        """Fractal (summed-octave) value noise in [0,1)."""
        total = np.zeros(len(centroids))
        amp, norm, res = 1.0, 0.0, base_res
        for _ in range(octaves):
            total += amp * self._value_noise(centroids, width, height, res)
            norm += amp
            amp *= 0.5
            res *= 2
        return total / norm

    def _sample_hint(self, hint: ElevationHint, centroids: np.ndarray,
                     width: int, height: int) -> np.ndarray:
        """Sample a caller's elevation hint at each cell centroid → an array of
        per-cell elevations. ``hint`` is a callable over normalised [0,1] coords,
        or a 2D grid (rows north→south, cols west→east) bilinearly interpolated."""
        xn = np.clip(centroids[:, 0] / max(1, width), 0.0, 1.0)
        yn = np.clip(centroids[:, 1] / max(1, height), 0.0, 1.0)
        if callable(hint):
            sampled = np.array([float(hint(float(x), float(y))) for x, y in zip(xn, yn)])
        else:
            grid = np.asarray(hint, dtype=float)
            if grid.ndim != 2 or grid.size == 0:
                raise ValueError("elevation_hint grid must be a non-empty 2D array")
            gh, gw = grid.shape
            u, v = xn * (gw - 1), yn * (gh - 1)  # fractional grid coords
            x0, y0 = np.floor(u).astype(int), np.floor(v).astype(int)
            x1, y1 = np.minimum(x0 + 1, gw - 1), np.minimum(y0 + 1, gh - 1)
            fx, fy = u - x0, v - y0
            top = grid[y0, x0] * (1 - fx) + grid[y0, x1] * fx
            bot = grid[y1, x0] * (1 - fx) + grid[y1, x1] * fx
            sampled = top * (1 - fy) + bot * fy
        # A non-finite hint (NaN/inf from a buggy callable or grid) would silently
        # poison every height; fail loudly instead.
        if not np.all(np.isfinite(sampled)):
            raise ValueError("elevation_hint produced non-finite values (NaN/inf)")
        return sampled

    def _init_heightmap(
        self, cells: list[TerrainCell], width: int, height: int, cfg: WorldMapConfig,
        template: str = "", elevation_hint: ElevationHint | None = None,
    ) -> None:
        cx, cy = width / 2, height / 2
        centroids = np.array([[c.cx, c.cy] for c in cells])
        n_cells = len(cells)

        # Hint mode (caller art-directs the macro shape) — highest precedence. The
        # hint sets *where* land/high ground sit; mapwright still adds organic
        # coastline detail (fbm) and runs the full erosion/hydrology/climate
        # pipeline. Normalised to [0,1] so any input range works; sea level + the
        # land_age gamma in _finalize_heights then do their usual job.
        if elevation_hint is not None:
            h = self._sample_hint(elevation_hint, centroids, width, height)
            hmin, hmax = float(h.min()), float(h.max())
            h = (h - hmin) / max(1e-9, hmax - hmin)
            coast = (self._fbm(centroids, width, height, octaves=5, base_res=3) - 0.5) * 2.0
            raw = (h - 0.5) * 2.0 + 0.25 * coast
            self._finalize_heights(cells, raw, self._radial_frame(centroids, width, height, cfg), cfg)
            return

        # Template mode (Azgaar-style composed ops) — an alternative to the default
        # tectonic auto-generation, for controllable continent archetypes.
        if template in TERRAIN_TEMPLATES:
            raw = self._template_raw(cells, centroids, width, height, template)
            self._finalize_heights(cells, raw, self._radial_frame(centroids, width, height, cfg), cfg)
            return

        # --- Tectonic plates ------------------------------------------------
        # A simple plate model (clean-room from the *idea* in Nortantis): the map is
        # tiled into continental + oceanic plates; each drifts; where plates push
        # together (convergent boundaries) crust piles up into mountain RANGES. The
        # plate regions are irregular (a Voronoi over scattered seeds) and there is
        # no centre bias, so coastlines come out organic rather than circular.

        # Continental plate seeds reuse the `continents` knob (1 central, or N on a
        # ring) so the knob keeps meaning. For multiple continents, OCEANIC seeds are
        # interleaved *between* them on the ring (+ one in the centre) so plate
        # boundaries fall in open water → the land fragments into separate islands
        # rather than fusing into one blob.
        n = cfg.continents
        cont_seeds: list[tuple[float, float]] = []
        ocean_seeds: list[tuple[float, float]] = []
        if n == 1:
            # A real continent is a fragment shoved against its ocean, not a disk
            # centred on the map. Pick the world's ocean-facing direction, offset the
            # landmass toward the *far* (passive, rifted) side away from that ocean,
            # and seed it with TWO cratonic sub-plates so the mass is an irregular
            # union (with a collision range where they meet) — not one Voronoi blob.
            self._ocean_dir = self._rng.uniform(0.0, 2.0 * math.pi)
            ox, oy = math.cos(self._ocean_dir), math.sin(self._ocean_dir)
            fx, fy = cx - ox * width * 0.20, cy - oy * height * 0.20
            cont_seeds.append((fx + self._rng.fuzzy(0, width * 0.10),
                               fy + self._rng.fuzzy(0, height * 0.10)))
            cont_seeds.append((fx + self._rng.fuzzy(0, width * 0.18),
                               fy + self._rng.fuzzy(0, height * 0.18)))
            # One big oceanic plate on the facing side + scattered minor ones.
            ocean_seeds.append((cx + ox * width * 0.42, cy + oy * height * 0.42))
            ocean_seeds += [(self._rng.uniform(0, width), self._rng.uniform(0, height))
                            for _ in range(4)]
        else:
            ring = cfg.continent_spread * min(width, height) * 0.42

            def ring_pt(angle: float) -> tuple[float, float]:
                return (cx + ring * math.cos(angle) + self._rng.fuzzy(0, width * 0.05),
                        cy + ring * math.sin(angle) + self._rng.fuzzy(0, height * 0.05))

            for i in range(n):
                cont_seeds.append(ring_pt(2 * math.pi * i / n + self._rng.fuzzy(0, 0.4)))
                ocean_seeds.append(ring_pt(2 * math.pi * (i + 0.5) / n + self._rng.fuzzy(0, 0.4)))
            ocean_seeds.append((cx + self._rng.fuzzy(0, width * 0.1),
                                cy + self._rng.fuzzy(0, height * 0.1)))  # inner sea
            ocean_seeds += [(self._rng.uniform(0, width), self._rng.uniform(0, height))
                            for _ in range(2)]
        seeds = cont_seeds + ocean_seeds
        is_continental = [True] * len(cont_seeds) + [False] * len(ocean_seeds)
        n_plates = len(seeds)
        seed_arr = np.array(seeds)

        # Assign every cell to its nearest plate seed (plate Voronoi).
        d2p = ((centroids[:, None, 0] - seed_arr[None, :, 0]) ** 2
               + (centroids[:, None, 1] - seed_arr[None, :, 1]) ** 2)
        plate = d2p.argmin(axis=1)

        # A random drift direction (+ speed) per plate.
        drift = np.array([
            (math.cos(a) * s, math.sin(a) * s)
            for a, s in ((self._rng.uniform(0, 2 * math.pi), self._rng.uniform(0.4, 1.0))
                         for _ in range(n_plates))
        ])

        # Base elevation by plate type: continental crust rides high, oceanic low.
        base = np.array([0.55 if is_continental[plate[i]] else -0.65 for i in range(n_cells)])

        # Convergent-boundary uplift: at a cell bordering another plate, project the
        # plates' relative drift onto the boundary normal; pushing together → uplift,
        # scaled by what's colliding (continent–continent ranges > arcs).
        boundary = np.zeros(n_cells)
        for c in cells:
            pi = plate[c.id]
            for nb in c.neighbors:
                pj = plate[nb]
                if pj == pi:
                    continue
                dx, dy = cells[nb].cx - c.cx, cells[nb].cy - c.cy
                length = math.hypot(dx, dy) or 1.0
                conv = ((drift[pi, 0] - drift[pj, 0]) * dx
                        + (drift[pi, 1] - drift[pj, 1]) * dy) / length
                if conv <= 0:
                    continue
                ci, cj = is_continental[pi], is_continental[pj]
                mag = 1.0 if (ci and cj) else (0.6 if (ci or cj) else 0.35)
                boundary[c.id] = max(boundary[c.id], conv * mag)

        # Spread uplift inland so ranges have width (neighbour-max with decay).
        uplift = boundary.copy()
        for _ in range(3):
            spread = uplift.copy()
            for c in cells:
                for nb in c.neighbors:
                    decayed = uplift[nb] * 0.6
                    if decayed > spread[c.id]:
                        spread[c.id] = decayed
            uplift = spread
        uplift = uplift * (0.5 + 1.2 * cfg.mountain_density)

        # Fractal coastline detail breaks the straight plate edges into organic
        # bays/capes; a radial edge term frames the map in sea (works *with* the
        # percentile sea level below — it just pushes border cells to the low end).
        coast = (self._fbm(centroids, width, height, octaves=5, base_res=3) - 0.5) * 2.0
        # A single continent gets the directional sea-frame (it faces one ocean) and
        # a touch more coastline noise for a raggeder shore. Multi-continent worlds
        # keep the symmetric radial frame, which already scatters them into islands;
        # a directional bias there would drown one whole flank of the ring.
        if n == 1:
            raw = base + uplift + 0.55 * coast
            frame = self._sea_frame(centroids, width, height, cfg, dir_amp=1.0)
        else:
            raw = base + uplift + 0.45 * coast
            frame = self._radial_frame(centroids, width, height, cfg)
        self._finalize_heights(cells, raw, frame, cfg)

    def _radial_frame(self, centroids, width: int, height: int,
                      cfg: WorldMapConfig) -> np.ndarray:
        """Legacy symmetric sea-frame: distance from map centre, drowning the rim.

        Used for multi-continent worlds (it scatters a ring of plates into islands)
        and for template/hint modes (which place their own land and want a neutral,
        undirected frame). Single continents use the directional :meth:`_sea_frame`
        instead, since a radial disk is what made one continent come out circular.
        """
        cx, cy = width / 2, height / 2
        diag = math.hypot(width, height)
        d_true = np.sqrt((centroids[:, 0] - cx) ** 2
                         + (centroids[:, 1] - cy) ** 2) / (diag / 2)
        return np.clip((d_true - 0.58) / 0.42, 0, 1) * 1.8 * cfg.edge_falloff

    def _sea_frame(self, centroids, width: int, height: int,
                   cfg: WorldMapConfig, dir_amp: float) -> np.ndarray:
        """The amount each cell is pushed toward sea to *frame the map in water*.

        Replaces the old radial disk (``edge_falloff`` × distance-from-centre),
        which forced continents into circles. Instead the frame is:

        * **box-distance to the nearest border**, so it follows the rectangular
          map, not a circle;
        * **noise-warped**, so the resulting coastline is wavy (bays/capes) rather
          than a clean edge; and
        * **directional** (``dir_amp``): the world's ocean-facing side is drowned
          hard while the opposite (passive) side is barely framed, so land sits
          off-centre and can run to one edge — like a real coastline.

        Returns a non-negative array (0 = untouched) scaled by ``edge_falloff``.
        """
        if cfg.edge_falloff <= 0:
            return np.zeros(len(centroids))
        cx, cy = width / 2, height / 2
        x, y = centroids[:, 0], centroids[:, 1]
        # 0 at any border → ~1 toward the middle (box distance, not radial).
        edge = np.minimum(np.minimum(x, width - x) / (width / 2),
                          np.minimum(y, height - y) / (height / 2))
        warp = self._fbm(centroids, width, height, octaves=4, base_res=2) - 0.5
        edge_w = edge + 0.45 * warp
        band = np.clip((0.6 - edge_w) / 0.42, 0.0, 1.0)  # 1 at border → 0 inland
        # Directional bias: +1 on the ocean-facing flank, -1 on the far flank.
        ox, oy = math.cos(self._ocean_dir), math.sin(self._ocean_dir)
        proj = ((x - cx) / (width / 2)) * ox + ((y - cy) / (height / 2)) * oy
        side = np.clip(0.5 + 0.5 * proj, 0.0, 1.0)       # 0 far → 1 ocean side
        strength = (1.0 - dir_amp) + dir_amp * (0.3 + 1.6 * side)
        return band * strength * 1.5 * cfg.edge_falloff

    def _finalize_heights(self, cells, raw, frame, cfg: WorldMapConfig) -> None:
        """Frame the map in sea and set per-cell heights via a **percentile** sea
        level (the fwmg idea): exactly ``sea_level`` of cells become water, so a
        higher sea level floods more by construction; land scales into
        (sea_level, 1] and sea into [0, sea_level). Shared by both heightmap modes.
        """
        raw = raw - frame
        sea = cfg.sea_level
        thr = float(np.quantile(raw, sea))
        rmin, rmax = float(raw.min()), float(raw.max())
        # land_age shapes the hypsometric curve via a gamma on land elevation:
        # young (γ<1) lifts the land high → many sharp peaks/mountains; old (γ>1)
        # compresses it low → mostly worn hills and plains. (Percentile sea level
        # rescales away absolute height, so the *distribution shape* is the lever.)
        gamma = 1.0 + (cfg.land_age - 0.5) * 1.3
        for i, cell in enumerate(cells):
            r = float(raw[i])
            if r <= thr:
                cell.height = float(sea * (r - rmin) / max(1e-6, thr - rmin))
            else:
                rel = (r - thr) / max(1e-6, rmax - thr)
                cell.height = float(sea + (1.0 - sea) * rel ** gamma)

    # -- 2b. Heightmap templates (composable elevation ops) --------------

    def _template_raw(self, cells, centroids, width: int, height: int, template: str) -> np.ndarray:
        """Build a raw heightmap by running a template's ops (clean-room from the
        idea in Azgaar's FMG): each op spreads elevation over the cell graph, and
        named templates compose them into continent archetypes."""
        raw = np.zeros(len(cells))
        for op in TERRAIN_TEMPLATES[template]:
            kind = op[0]
            if kind == "hill":
                self._op_blobs(raw, cells, centroids, width, height, *op[1:], sign=+1)
            elif kind == "pit":
                self._op_blobs(raw, cells, centroids, width, height, *op[1:], sign=-1)
            elif kind == "range":
                self._op_range(raw, cells, centroids, width, height, *op[1:], sign=+1)
            elif kind == "trough":
                self._op_range(raw, cells, centroids, width, height, *op[1:], sign=-1)
            elif kind == "strait":
                self._op_strait(raw, cells, centroids, width, height, *op[1:])
        return raw

    @staticmethod
    def _nearest_cell(centroids: np.ndarray, x: float, y: float) -> int:
        return int(((centroids[:, 0] - x) ** 2 + (centroids[:, 1] - y) ** 2).argmin())

    def _blob(self, raw, cells, start: int, power: float, decay: float, sign: int) -> None:
        """Spread an elevation blob from ``start`` outward ring-by-ring over the
        cell graph (organic because the graph is irregular), decaying each ring."""
        used = {start}
        frontier = [start]
        raw[start] += sign * power
        p = power
        while frontier and p * decay > 0.02:
            p *= decay
            nxt = []
            for cur in frontier:
                for nb in cells[cur].neighbors:
                    if nb not in used:
                        used.add(nb)
                        raw[nb] += sign * p * (0.85 + 0.3 * self._rng.random())
                        nxt.append(nb)
            frontier = nxt

    def _op_blobs(self, raw, cells, centroids, width, height,
                  count, power_rng, spread, x_rng, y_rng, *, sign) -> None:
        decay = 0.5 + 0.42 * spread  # spread 0 ⇒ small blob, 1 ⇒ broad landmass
        for _ in range(self._rng.randint(count[0], count[1])):
            x = width * self._rng.uniform(*x_rng)
            y = height * self._rng.uniform(*y_rng)
            power = self._rng.uniform(*power_rng)
            self._blob(raw, cells, self._nearest_cell(centroids, x, y), power, decay, sign)

    def _op_range(self, raw, cells, centroids, width, height,
                  count, power_rng, frm, to, *, sign) -> None:
        """Trace a path from a `frm` region to a `to` region, raising/lowering a
        thin ridge along it → a linear mountain range (or rift trough)."""
        for _ in range(self._rng.randint(count[0], count[1])):
            start = self._nearest_cell(centroids, width * self._rng.uniform(frm[0], frm[1]),
                                       height * self._rng.uniform(frm[2], frm[3]))
            goal = self._nearest_cell(centroids, width * self._rng.uniform(to[0], to[1]),
                                      height * self._rng.uniform(to[2], to[3]))
            power = self._rng.uniform(*power_rng)
            cur, path, seen = start, [start], {start}
            while cur != goal and len(path) < len(cells):
                gx, gy = centroids[goal]
                nbs = cells[cur].neighbors
                cur = min(nbs, key=lambda n: ((centroids[n][0] - gx) ** 2 + (centroids[n][1] - gy) ** 2)
                          - self._rng.random() * (width * height) * 0.02)
                if cur in seen:
                    break
                seen.add(cur)
                path.append(cur)
            for cid in path:
                self._blob(raw, cells, cid, power * (0.6 + 0.4 * self._rng.random()), 0.4, sign)

    def _op_strait(self, raw, cells, centroids, width, height, width_frac, vertical) -> None:
        """Carve a low water channel across the map (lowers a band) to split land."""
        if vertical:
            centre = width * self._rng.uniform(0.35, 0.65)
            half = max(1.0, width * width_frac / 2)
            coord = centroids[:, 0]
        else:
            centre = height * self._rng.uniform(0.35, 0.65)
            half = max(1.0, height * width_frac / 2)
            coord = centroids[:, 1]
        depth = np.clip(1.0 - np.abs(coord - centre) / half, 0.0, 1.0)
        raw -= depth * 1.0

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

    @staticmethod
    def _weather(cells: list[TerrainCell], passes: int) -> None:
        """Round terrain by pulling each land cell's height toward its land
        neighbours' mean — the "old, worn-down" look. ``passes`` (from land_age)
        controls how heavily; 0 leaves young/sharp terrain untouched."""
        for _ in range(max(0, passes)):
            updated: dict[int, float] = {}
            for c in cells:
                if c.is_water:
                    continue
                heights = [cells[n].height for n in c.neighbors if not cells[n].is_water]
                if heights:
                    updated[c.id] = 0.6 * c.height + 0.4 * (sum(heights) / len(heights))
            for cid, h in updated.items():
                cells[cid].height = h

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
        # Sources are the highest-flux land cells: keep the top fraction (3%..13%
        # of land, scaling with river_density) via a flux *quantile*. Adapting to
        # the actual flux distribution — rather than an absolute threshold — makes
        # rivers form reliably on gentle and steep terrain alike. A small absolute
        # floor still stops flat maps from over-rivering.
        land_flux = np.array([c.flux for c in cells if not c.is_water and not c.is_lake])
        if land_flux.size == 0:
            return []
        keep = 0.03 + 0.10 * river_density
        cutoff = max(5.0, float(np.quantile(land_flux, 1.0 - keep)))
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
