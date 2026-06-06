"""Procedural settlement (town) generation: Voronoi wards over an organic footprint.

The first slice of the settlement tier. It produces a town *layout* as neutral
data — an organic footprint polygon divided into named Voronoi **wards**, with an
optional coastline. Lots, streets, and defensive walls follow in later versions
(``walled`` is recorded now but not yet drawn).

Domain-neutral and **self-contained**: a town is generated on its own
``width×height`` canvas with no terrain dependency; pass ``coastal=True`` for a
synthetic shoreline. Reuses the shared :class:`~mapwright.rng.SeededRNG`, the
geometry primitives in :mod:`mapwright._geometry`, and the Markov namebases.

Clean-room from the *ideas* of Watabou's TownGeneratorOS (GPLv3) — concept only,
no code copied; see NOTICE. Fully seed-deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Union

from . import _graph, _serde
from ._geometry import (
    Point,
    clip_halfplane,
    clip_line_to_convex,
    convex_hull,
    inset_convex,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
    voronoi_cells,
)
from .names import NameGenerator
from .rng import SeededRNG

# A terrain field for shaping a settlement: either a callable over **normalised**
# canvas coordinates ``(xn, yn) in [0,1]`` returning an elevation (negative ⇒
# water/unbuildable), or a 2D grid (rows north→south, cols west→east) sampled the
# same way. See :func:`world_terrain_field` to build one from a generated world.
TerrainField = Union[Callable[[float, float], float], Sequence[Sequence[float]]]


def world_terrain_field(
    terrain, region: tuple[float, float, float, float] | None = None
) -> Callable[[float, float], float]:
    """Build a settlement ``terrain`` field from a generated world.

    Returns a callable over normalised canvas coords that reports the world's
    elevation **relative to sea level** (so ocean/lakes come out negative and read
    as unbuildable water) at the corresponding world location. ``region = (x0, y0,
    w, h)`` is the world rectangle the town occupies (defaults to the whole map);
    shrink it to seat a town on a chosen stretch of coast or valley.

    Duck-typed: any object exposing ``cell_of`` (2D int grid of cell ids),
    ``cells`` (each with ``.height``), ``sea_level``, ``width`` and ``height``
    works — i.e. a :class:`~mapwright.terrain.RegionalTerrain`."""
    cell_of = terrain.cell_of
    cells = terrain.cells
    sea = float(terrain.sea_level)
    ww, wh = int(terrain.width), int(terrain.height)
    x0, y0, rw, rh = region if region is not None else (0.0, 0.0, ww, wh)

    def field(xn: float, yn: float) -> float:
        wx = x0 + min(max(xn, 0.0), 1.0) * rw
        wy = y0 + min(max(yn, 0.0), 1.0) * rh
        ix = min(max(int(wx), 0), ww - 1)
        iy = min(max(int(wy), 0), wh - 1)
        return float(cells[int(cell_of[iy][ix])].height) - sea

    return field


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _two_farthest(points: list[Point]) -> tuple[Point, Point]:
    """The two points that are farthest apart (the endpoints of a shared edge)."""
    best = -1.0
    pair = (points[0], points[-1])
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            d = (points[i][0] - points[j][0]) ** 2 + (points[i][1] - points[j][1]) ** 2
            if d > best:
                best, pair = d, (points[i], points[j])
    return pair


def _dedup_polygon(poly: list[Point], eps: float = 1e-6) -> list[Point]:
    """Drop consecutive (and wrap-around) near-coincident vertices."""
    out: list[Point] = []
    for p in poly:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


def _index_of(ring: list[Point], pt: Point, eps: float = 1e-6) -> int | None:
    """Index of the ring vertex matching ``pt`` (within ``eps``), or None."""
    for i, p in enumerate(ring):
        if abs(p[0] - pt[0]) <= eps and abs(p[1] - pt[1]) <= eps:
            return i
    return None


def _open_ring_excluding_edge(
    ring: list[Point], i0: int, i1: int
) -> list[Point] | None:
    """Reorder ``ring`` into an open polyline that omits the edge between the two
    given (adjacent) vertices — used to leave the coast side unwalled. Returns
    None if the vertices aren't adjacent."""
    m = len(ring)
    if (i0 + 1) % m == i1:
        a, b = i0, i1
    elif (i1 + 1) % m == i0:
        a, b = i1, i0
    else:
        return None  # not adjacent (unexpected) — caller keeps the closed ring
    # Walk from b around to a, so the omitted edge is a→b (the coast).
    out: list[Point] = []
    k = b
    for _ in range(m):
        out.append(ring[k])
        if k == a:
            break
        k = (k + 1) % m
    return out


def _point_segment_dist(p: Point, a: Point, b: Point) -> float:
    """Distance from point ``p`` to segment ``a``–``b``."""
    ax, ay = a
    bx, by = b
    ex, ey = bx - ax, by - ay
    length2 = ex * ex + ey * ey
    if length2 < 1e-18:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * ex + (p[1] - ay) * ey) / length2))
    return math.hypot(p[0] - (ax + t * ex), p[1] - (ay + t * ey))


def _insert_on_ring(ring: list[Point], pt: Point, eps: float = 1e-6) -> list[Point]:
    """Insert ``pt`` into ``ring`` on the perimeter edge it lies on, so a mid-edge
    point (e.g. a grid gate) becomes a real ring vertex. No-op when ``pt`` already
    coincides with a vertex, or doesn't actually sit on the perimeter."""
    if _index_of(ring, pt, eps) is not None:
        return ring
    m = len(ring)
    best_i, best_d = -1, float("inf")
    for i in range(m):
        d = _point_segment_dist(pt, ring[i], ring[(i + 1) % m])
        if d < best_d:
            best_d, best_i = d, i
    if best_i < 0 or best_d > 1e-6:
        return ring  # not on the perimeter — leave the ring alone
    return ring[:best_i + 1] + [pt] + ring[best_i + 1:]


# Single source of truth for the numeric knobs: (name, type, min, max, description).
_SPEC: list[tuple] = [
    ("population", int, 20, 200_000,
     "Rough inhabitant count; drives the town's size and ward count."),
    ("irregularity", float, 0.0, 1.0,
     "0 = smooth round footprint; 1 = ragged, organic outline."),
    ("lot_size", float, 2.0, 60.0,
     "Target building-plot area (tiles²); smaller = denser, finer lots."),
    ("wealth", float, 0.0, 1.0,
     "0 = destitute shanty (cramped, slum-heavy lots); 1 = rich (large estates, "
     "noble/temple wards). 0.5 is neutral."),
    ("era", float, 0.0, 1.0,
     "0 = ancient/medieval (organic, winding blocks); 1 = modern (regular, "
     "grid-like blocks). 0.5 is neutral."),
]
# Boolean flags: (name, description). Kept separate from _SPEC (no clamping).
_FLAG_SPEC: list[tuple] = [
    ("walled", "Surround the town with a defensive wall (recorded now; drawn later)."),
    ("coastal", "Place the town on a coastline — adds a straight water edge."),
]
# Enumerated (string-choice) fields: (name, choices, default, description). An
# invalid value falls back to the default. Kept separate from the numeric _SPEC.
_ENUM_SPEC: list[tuple] = [
    ("layout", ("organic", "grid"), "organic",
     "Street pattern: 'organic' = winding ward-to-ward roads (the classic look); "
     "'grid' = a geometric street grid aligned to the town's long axis."),
    ("purpose", ("general", "trade", "fortress", "religious", "harbor",
                 "extraction", "transit"), "general",
     "What the town exists for. Anything but 'general' seeds a central landmark "
     "(a special ward the main roads focus on) and biases the ward-kind mix: "
     "trade→market, fortress→citadel, religious→temple, harbor→docks, "
     "extraction→mine, transit→plaza."),
]

# Ward kinds. One central market, optional dockside ward when coastal, and a
# residential-heavy weighted mix for the rest.
_MARKET = "market"
_DOCKS = "docks"
_OTHER_KINDS: list[str] = (
    ["residential"] * 5 + ["craftsmen"] * 3 + ["noble"] * 2 + ["slums"] * 2
    + ["temple"] * 1 + ["garrison"] * 1
)

# Town purpose → the kind of the central landmark ward (absent ⇒ no landmark, the
# central ward stays a plain market). Some reuse existing ward kinds (market,
# temple, docks); citadel/mine/plaza are landmark-only kinds.
_LANDMARK_KIND: dict[str, str] = {
    "trade": "market",
    "fortress": "citadel",
    "religious": "temple",
    "harbor": "docks",
    "extraction": "mine",
    "transit": "plaza",
}

# Town purpose → extra ward kinds folded into the weighted bag (biases the mix
# toward what the town is for). "general" has no entry ⇒ the bag is unchanged.
_PURPOSE_WARD_BIAS: dict[str, list[str]] = {
    "trade": ["craftsmen"] * 3,
    "fortress": ["garrison"] * 3,
    "religious": ["temple"] * 3,
    "harbor": ["craftsmen"] * 2,
    "extraction": ["slums"] * 2 + ["craftsmen"] * 2,
    "transit": ["residential"] * 2 + ["craftsmen"] * 1,
}

# Per-ward lot sizing: a multiplier on the base ``lot_size`` (bigger ⇒ larger,
# fewer plots), or ``None`` for an open ward with no buildings. Kinds absent from
# this map use the base size (factor 1.0).
_WARD_LOT_FACTOR: dict[str, float | None] = {
    "market": None,    # open market square — no buildings
    "noble": 3.0,      # large estates / manor grounds
    "garrison": 2.0,   # a few big structures
    "slums": 0.5,      # cramped, tiny plots
}


# --- era / wealth shaping (all identity at the neutral 0.5, so defaults are
# byte-identical to the pre-era output) ------------------------------------

def _lot_size_factor(wealth: float) -> float:
    """Wealth scales plot size: poor ⇒ cramped (smaller plots), rich ⇒ large
    estates / blocks. Returns 1.0 at ``wealth == 0.5``."""
    return 1.0 + (wealth - 0.5) * 1.2


def _block_jitter_factor(era: float, wealth: float) -> float:
    """Block regularity, mostly from ``era`` (a little from ``wealth``): a modern,
    planned town gets near-centred, grid-like splits (low jitter); an ancient or
    poor one sprawls (high jitter). Returns 1.0 at ``era == wealth == 0.5``."""
    order = 0.7 * (era - 0.5) + 0.5 * (wealth - 0.5)  # 0 at neutral
    return _clamp(1.0 - 1.3 * order, 0.25, 1.7)


def _ward_kind_pool(wealth: float, purpose: str = "general") -> list[str]:
    """The weighted ward-kind bag, shifted by wealth (poor ⇒ more slums, rich ⇒
    more noble/temple) and biased by ``purpose``. Equals ``_OTHER_KINDS`` exactly
    at ``wealth == 0.5`` and ``purpose == "general"``."""
    noble = max(0, round(2 + (wealth - 0.5) * 5))
    temple = max(0, round(1 + (wealth - 0.5) * 2))
    slums = max(0, round(2 - (wealth - 0.5) * 6))
    return (["residential"] * 5 + ["craftsmen"] * 3 + ["noble"] * noble
            + ["slums"] * slums + ["temple"] * temple + ["garrison"] * 1
            + _PURPOSE_WARD_BIAS.get(purpose, []))


@dataclass
class SettlementConfig:
    """Knobs for :meth:`SettlementGenerator.generate`. Defaults = a small town."""

    population: int = 2000
    """Rough inhabitant count; drives size and ward count."""
    irregularity: float = 0.5
    """0..1 — how ragged the town outline is."""
    lot_size: float = 8.0
    """Target building-plot area in tiles² (smaller ⇒ denser, finer lots)."""
    wealth: float = 0.5
    """0..1 — destitute shanty ⇄ rich town (lot size + ward-kind mix)."""
    era: float = 0.5
    """0..1 — ancient/organic ⇄ modern/grid-regular blocks."""
    layout: str = "organic"
    """Street pattern: 'organic' (winding ward roads) or 'grid' (geometric grid)."""
    purpose: str = "general"
    """What the town is for; non-'general' seeds a central landmark + biases wards."""
    walled: bool = False
    """Whether the town has a wall (stored now, rendered in a later version)."""
    coastal: bool = False
    """Whether the town sits on a coast (adds a synthetic water edge)."""

    def __post_init__(self) -> None:
        for name, typ, lo, hi, _desc in _SPEC:
            value = _clamp(getattr(self, name), lo, hi)
            setattr(self, name, int(value) if typ is int else float(value))
        for name, _desc in _FLAG_SPEC:
            setattr(self, name, bool(getattr(self, name)))
        for name, choices, default, _desc in _ENUM_SPEC:
            value = getattr(self, name)
            setattr(self, name, value if value in choices else default)

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "irregularity": self.irregularity,
            "lot_size": self.lot_size,
            "wealth": self.wealth,
            "era": self.era,
            "layout": self.layout,
            "purpose": self.purpose,
            "walled": self.walled,
            "coastal": self.coastal,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SettlementConfig":
        """Build from a (possibly partial / noisy) mapping; unknown keys ignored."""
        return cls(**_serde.only_known(cls, data))

    @classmethod
    def json_schema(cls) -> dict:
        """A JSON Schema (draft 2020-12) describing this config — the machine-readable
        contract a host or LLM can populate, then feed through :meth:`from_dict`."""
        defaults = cls()
        properties: dict = {
            name: {
                "type": "integer" if typ is int else "number",
                "minimum": lo,
                "maximum": hi,
                "default": getattr(defaults, name),
                "description": desc,
            }
            for name, typ, lo, hi, desc in _SPEC
        }
        for name, desc in _FLAG_SPEC:
            properties[name] = {
                "type": "boolean",
                "default": getattr(defaults, name),
                "description": desc,
            }
        for name, choices, _default, desc in _ENUM_SPEC:
            properties[name] = {
                "type": "string",
                "enum": list(choices),
                "default": getattr(defaults, name),
                "description": desc,
            }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "SettlementConfig",
            "description": "Parameters that shape a mapwright settlement.",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        }

    @classmethod
    def preset(cls, name: str) -> "SettlementConfig":
        """A named starting point. Raises KeyError for an unknown preset."""
        return cls.from_dict(dict(SETTLEMENT_PRESETS[name]))

    @staticmethod
    def preset_names() -> list[str]:
        return sorted(SETTLEMENT_PRESETS.keys())


SETTLEMENT_PRESETS: dict[str, dict] = {
    "hamlet": {"population": 120, "irregularity": 0.7},
    "village": {"population": 600, "irregularity": 0.6},
    "town": {},  # the balanced default
    "city": {"population": 18000, "irregularity": 0.35},
    "port": {"population": 9000, "coastal": True, "irregularity": 0.4},
    "citadel": {"population": 5000, "walled": True, "irregularity": 0.2},
    "shantytown": {"population": 4000, "wealth": 0.08, "era": 0.3, "irregularity": 0.8},
    "metropolis": {"population": 30000, "wealth": 0.92, "era": 0.95, "irregularity": 0.18},
    "grid_city": {"population": 16000, "wealth": 0.7, "era": 0.95,
                  "layout": "grid", "irregularity": 0.25},
    "fortress_town": {"population": 5000, "purpose": "fortress", "walled": True,
                      "irregularity": 0.3},
    "pilgrimage_site": {"population": 3000, "purpose": "religious",
                        "irregularity": 0.5},
    "mining_camp": {"population": 1500, "purpose": "extraction", "wealth": 0.2,
                    "irregularity": 0.7},
}


@dataclass
class Ward:
    """One town ward (district): a Voronoi polygon with a name and a kind."""

    id: int
    polygon: list[Point]
    center: Point
    name: str
    kind: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "polygon": [[x, y] for x, y in self.polygon],
            "center": [self.center[0], self.center[1]],
            "name": self.name,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Ward":
        return cls(
            id=int(data["id"]),
            polygon=[(float(x), float(y)) for x, y in data["polygon"]],
            center=(float(data["center"][0]), float(data["center"][1])),
            name=data["name"],
            kind=data["kind"],
        )


@dataclass
class Landmark:
    """The town's defining feature (set by a non-'general' ``purpose``): the
    central ward promoted to a special ``kind`` (citadel/temple/mine/…), which the
    main roads focus on. ``ward`` is the id of that ward."""

    ward: int
    kind: str
    center: Point
    name: str

    def to_dict(self) -> dict:
        return {
            "ward": self.ward,
            "kind": self.kind,
            "center": [self.center[0], self.center[1]],
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Landmark":
        return cls(
            ward=int(data["ward"]),
            kind=data["kind"],
            center=(float(data["center"][0]), float(data["center"][1])),
            name=data["name"],
        )


@dataclass
class Lot:
    """A building plot inside a ward (a convex polygon, the building footprint)."""

    id: int
    polygon: list[Point]
    ward: int  # id of the ward this lot belongs to

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "polygon": [[x, y] for x, y in self.polygon],
            "ward": self.ward,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Lot":
        return cls(
            id=int(data["id"]),
            polygon=[(float(x), float(y)) for x, y in data["polygon"]],
            ward=int(data["ward"]),
        )


@dataclass
class Street:
    """A street as a polyline. ``kind`` is ``"main"`` (gate↔market thoroughfare)
    or ``"minor"`` (ward-to-ward road)."""

    path: list[Point]
    kind: str = "minor"

    def to_dict(self) -> dict:
        return {"path": [[x, y] for x, y in self.path], "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict) -> "Street":
        return cls(path=[(float(x), float(y)) for x, y in data["path"]],
                   kind=data["kind"])


@dataclass
class Wall:
    """A defensive wall: an ordered ring of corner points (towers sit at each),
    ``closed`` whether it loops (open at a harbour when coastal), and ``gates``
    (gap positions where roads pass through)."""

    ring: list[Point]
    closed: bool = True
    gates: list[Point] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ring": [[x, y] for x, y in self.ring],
            "closed": self.closed,
            "gates": [[x, y] for x, y in self.gates],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Wall":
        return cls(
            ring=[(float(x), float(y)) for x, y in data["ring"]],
            closed=bool(data["closed"]),
            gates=[(float(x), float(y)) for x, y in data.get("gates", [])],
        )


@dataclass
class Settlement:
    """Generated town layout: footprint, wards, lots, streets, gates, wall,
    landmark, coast."""

    width: int
    height: int
    name: str
    footprint: list[Point]
    wards: list[Ward]
    lots: list[Lot] = field(default_factory=list)
    streets: list[Street] = field(default_factory=list)
    gates: list[Point] = field(default_factory=list)
    wall: Wall | None = None
    landmark: Landmark | None = None  # set by a non-'general' purpose
    walled: bool = False
    coastal: bool = False
    purpose: str = "general"
    water_edge: tuple[Point, Point] | None = None  # coastline segment, if coastal

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        """A JSON-safe mapping of the whole town, round-tripping via :meth:`from_dict`."""
        return {
            "schema": "mapwright/settlement@5",
            "width": self.width,
            "height": self.height,
            "name": self.name,
            "footprint": [[x, y] for x, y in self.footprint],
            "wards": [w.to_dict() for w in self.wards],
            "lots": [lot.to_dict() for lot in self.lots],
            "streets": [st.to_dict() for st in self.streets],
            "gates": [[x, y] for x, y in self.gates],
            "wall": None if self.wall is None else self.wall.to_dict(),
            "landmark": None if self.landmark is None else self.landmark.to_dict(),
            "walled": self.walled,
            "coastal": self.coastal,
            "purpose": self.purpose,
            "water_edge": (
                None if self.water_edge is None
                else [[self.water_edge[0][0], self.water_edge[0][1]],
                      [self.water_edge[1][0], self.water_edge[1][1]]]
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Settlement":
        we = data.get("water_edge")
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            name=data["name"],
            footprint=[(float(x), float(y)) for x, y in data["footprint"]],
            wards=[Ward.from_dict(w) for w in data["wards"]],
            lots=[Lot.from_dict(lot) for lot in data.get("lots", [])],
            streets=[Street.from_dict(st) for st in data.get("streets", [])],
            gates=[(float(x), float(y)) for x, y in data.get("gates", [])],
            wall=(Wall.from_dict(data["wall"]) if data.get("wall") else None),
            landmark=(Landmark.from_dict(data["landmark"])
                      if data.get("landmark") else None),
            walled=bool(data.get("walled", False)),
            coastal=bool(data.get("coastal", False)),
            purpose=data.get("purpose", "general"),
            water_edge=(None if we is None
                        else ((float(we[0][0]), float(we[0][1])),
                              (float(we[1][0]), float(we[1][1])))),
        )

    def to_json(self, **kwargs) -> str:
        """Serialise to a JSON string (``kwargs`` pass to :func:`json.dumps`)."""
        return _serde.to_json(self, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "Settlement":
        return _serde.from_json(cls, text)


class SettlementGenerator:
    """Builds a :class:`Settlement` for a ``width×height`` canvas."""

    def __init__(self, rng: SeededRNG):
        # Derived sub-stream so settlement draws stay decoupled from other tiers
        # sharing the same root seed; names get their own sub-stream too.
        self._rng = rng.derive("settlement")
        self._names = NameGenerator(self._rng.derive("names"))

    def generate(
        self,
        width: int,
        height: int,
        config: SettlementConfig | None = None,
        *,
        culture: str = "generic",
        terrain: "TerrainField | None" = None,
    ) -> Settlement:
        """Generate a town. ``config`` shapes it (population, walls, coast, outline);
        ``culture`` selects the namebase for the town and ward names.

        ``terrain`` (optional) makes the town **take the shape of its ground**: a
        callable ``(x, y) -> elevation`` over canvas coordinates (negative =
        water/unbuildable), or a 2D grid sampled the same way. When given, the
        footprint is grown out from the core until it meets water or ground too high
        to build — so a coastal town hugs its shore, a town between lakes grows
        fingers, and a town on open flats spreads round. Without it, the outline is
        the procedural organic/grid shape (and ``config.coastal`` adds a synthetic
        straight shore as before). See :func:`world_terrain_field` to derive one
        from a generated :class:`~mapwright.terrain.RegionalTerrain`."""
        cfg = config or SettlementConfig()
        cx, cy = width / 2, height / 2
        radius = self._radius(cfg.population, width, height)

        # Organic towns get a concave, lobed outline; planned (grid) towns stay
        # convex. The convex hull drives the internal Voronoi/relaxation math
        # (which needs a convex bound); wards are then clipped back to the outline
        # so the visible fill matches the concave silhouette.
        water_edge: tuple[Point, Point] | None = None
        coastal = cfg.coastal
        sample = self._make_sampler(terrain, width, height) if terrain is not None else None
        if sample is not None:
            footprint, water_edge = self._terrain_footprint(
                cx, cy, radius, cfg, sample, width, height)
            coastal = water_edge is not None
        if sample is None or footprint is None:
            footprint = self._footprint(cx, cy, radius, cfg.irregularity,
                                        organic=cfg.layout != "grid")
            if cfg.coastal:
                footprint, water_edge = self._apply_coast(footprint, cx, cy, radius)
                coastal = cfg.coastal
        clip_hull = convex_hull(footprint)

        seeds = self._ward_seeds(footprint, clip_hull, cfg.population)
        polys = [self._clip_to_outline(c, footprint)
                 for c in voronoi_cells(seeds, clip_hull)]
        wards, hub_id = self._build_wards(polys, coastal, water_edge, culture,
                                          _ward_kind_pool(cfg.wealth, cfg.purpose),
                                          cfg.purpose)
        # The hub (central ward) is what main roads focus on — a landmark when the
        # purpose set one, otherwise the plain market.
        hub = wards[hub_id].center if wards else (cx, cy)
        landmark = None
        if wards and cfg.purpose in _LANDMARK_KIND:
            w = wards[hub_id]
            landmark = Landmark(ward=w.id, kind=w.kind, center=w.center, name=w.name)
        # A grid town aligns both its lots and its streets to the footprint's axes.
        # Terrain-shaped towns follow the ground, so they are never a clean grid.
        planned = cfg.layout == "grid" and sample is None
        grid_axes = None
        if planned:
            _, u, v = self._principal_axis(footprint)
            grid_axes = (u, v)
        lots = self._build_lots(wards, cfg, grid_axes)
        streets, gates = self._build_streets(wards, footprint, cfg, water_edge, hub,
                                             planned, coastal)
        wall = self._build_wall(footprint, cfg.walled, coastal, water_edge, gates)

        return Settlement(
            width=width,
            height=height,
            name=self._names.settlement(culture),
            footprint=footprint,
            wards=wards,
            lots=lots,
            streets=streets,
            gates=gates,
            wall=wall,
            landmark=landmark,
            purpose=cfg.purpose,
            walled=cfg.walled,
            coastal=coastal,
            water_edge=water_edge,
        )

    # -- footprint -------------------------------------------------------

    @staticmethod
    def _radius(population: int, width: int, height: int) -> float:
        """Town radius (long axis): grows with √population, capped to leave the
        canvas a margin even once the footprint is elongated/lobed."""
        fit = 0.40 * min(width, height)
        return min(fit, max(6.0, math.sqrt(population) * 0.45))

    def _footprint(self, cx: float, cy: float, radius: float, irregularity: float,
                   organic: bool = True) -> list[Point]:
        """A town outline as a **polar radius curve** ``r(θ)`` about the core.

        An organic town accretes around a centre and reaches out along roads and
        valleys, so its edge is lobed and *concave* — arms with bays between them,
        not a smooth oval. We sum several angular harmonics (1st = lopsided, 2nd =
        bilobed, higher = ragged arms); because ``r(θ)`` stays strictly positive the
        curve is always a simple, star-shaped polygon (every district visible from
        the core) — so wards can still be clipped to it cleanly.

        ``organic=False`` returns the convex hull instead: a *planned* (grid) town
        is deliberately regular, so a tidy convex footprint is the honest shape.
        ``radius`` is the long-axis half-extent.
        """
        theta = self._rng.uniform(0.0, 2.0 * math.pi)          # random orientation
        aspect = 1.0 + (0.30 + 0.55 * irregularity) * self._rng.random()  # elongation
        ct, st = math.cos(theta), math.sin(theta)
        # Per-harmonic amplitude (× a random factor) and phase. Higher harmonics
        # carry the arms/bays that make the outline concave; scale with irregularity.
        base = (0.22, 0.20, 0.16, 0.12, 0.08)
        amps = [b * irregularity * self._rng.uniform(0.45, 1.0) for b in base]
        phases = [self._rng.uniform(0.0, 2.0 * math.pi) for _ in base]

        k = 64
        pts: list[Point] = []
        for i in range(k):
            ang = 2.0 * math.pi * i / k
            rr = 1.0 + sum(a * math.sin((h + 1) * ang + p)
                           for h, (a, p) in enumerate(zip(amps, phases)))
            rr = max(0.42, min(1.30, rr))        # keep r>0 (simple curve) & on-canvas
            ux = rr * math.cos(ang)              # long axis (= radius)
            uy = rr * math.sin(ang) / aspect     # squash the short axis → ellipse
            x = radius * (ux * ct - uy * st)     # rotate into the random frame
            y = radius * (ux * st + uy * ct)
            pts.append((cx + x, cy + y))
        return pts if organic else convex_hull(pts)

    @staticmethod
    def _clip_to_outline(ward: list[Point], outline: list[Point]) -> list[Point]:
        """Intersect a convex ``ward`` (a Voronoi cell over the convex hull) with the
        concave ``outline``, so the ward fill never spills into the outline's bays.

        Clips ``outline`` by each of the ward's edge half-planes (interior side).
        Each step is a single half-plane cut, which is valid on the concave
        ``outline`` subject — the result is ``outline ∩ ward``."""
        if len(ward) < 3:
            return []
        wcx, wcy = polygon_centroid(ward)
        poly = list(outline)
        n = len(ward)
        for i in range(n):
            ax, ay = ward[i]
            bx, by = ward[(i + 1) % n]
            nx, ny = by - ay, ax - bx          # a normal to the edge
            if (wcx - ax) * nx + (wcy - ay) * ny > 0:   # make it point *outward*
                nx, ny = -nx, -ny
            poly = clip_halfplane(poly, ax, ay, nx, ny)  # keep the interior side
            if len(poly) < 3:
                return []
        return poly

    # -- terrain-shaped footprint ----------------------------------------

    @staticmethod
    def _make_sampler(terrain: TerrainField, width: int, height: int,
                      res: int = 56) -> Callable[[float, float], float]:
        """Bake a terrain field into a fast bilinear ``sample(x, y) -> elevation``
        over **canvas** coords (negative ⇒ water). A callable field is rasterised
        once onto a ``res × res`` grid (so a costly world lookup runs a bounded
        number of times); a passed 2D grid is used directly."""
        if callable(terrain):
            gw = gh = res
            grid = [[float(terrain(c / (gw - 1), r / (gh - 1)))
                     for c in range(gw)] for r in range(gh)]
        else:
            grid = [[float(v) for v in row] for row in terrain]
            gh = len(grid)
            gw = len(grid[0]) if gh else 0
        if gh < 2 or gw < 2:
            flat = grid[0][0] if (gh and gw) else 0.0
            return lambda x, y: flat

        def sample(x: float, y: float) -> float:
            u = min(max(x / width, 0.0), 1.0) * (gw - 1)
            v = min(max(y / height, 0.0), 1.0) * (gh - 1)
            x0, y0 = int(u), int(v)
            x1, y1 = min(x0 + 1, gw - 1), min(y0 + 1, gh - 1)
            fx, fy = u - x0, v - y0
            top = grid[y0][x0] * (1 - fx) + grid[y0][x1] * fx
            bot = grid[y1][x0] * (1 - fx) + grid[y1][x1] * fx
            return top * (1 - fy) + bot * fy

        return sample

    @staticmethod
    def _nearest_land(cx: float, cy: float, radius: float,
                      sample: Callable[[float, float], float]) -> Point | None:
        """The core, nudged onto land if it fell in water — scan rings outward."""
        if sample(cx, cy) >= 0.0:
            return (cx, cy)
        for ri in range(1, 21):
            d = radius * ri / 20.0
            for ai in range(12):
                a = 2.0 * math.pi * ai / 12.0
                x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
                if sample(x, y) >= 0.0:
                    return (x, y)
        return None

    def _terrain_footprint(
        self, cx: float, cy: float, radius: float, cfg: SettlementConfig,
        sample: Callable[[float, float], float], width: int, height: int,
    ) -> tuple[list[Point] | None, tuple[Point, Point] | None]:
        """Grow a star-shaped outline from the core: each ray marches out until it
        meets water, ground too high to build, or the canvas edge — so the town
        takes the shape of its terrain (round on flats, coast-hugging by water,
        fingered between lakes). Returns ``(footprint, water_edge)``; footprint is
        ``None`` when the core has no land nearby (caller falls back to procedural).
        """
        core = self._nearest_land(cx, cy, radius, sample)
        if core is None:
            return None, None
        ccx, ccy = core
        core_e = sample(ccx, ccy)
        # How far above the core the town will still climb before a ray stops — a
        # flat town spreads freely; a valley town won't crawl up the ridge. Planned
        # (regular) towns tolerate a touch more climb (terracing/earthworks).
        ceil = core_e + 0.10 + 0.20 * (1.0 - cfg.irregularity)
        irr = cfg.irregularity
        amps = [b * irr * self._rng.uniform(0.4, 1.0) for b in (0.10, 0.08, 0.06)]
        phases = [self._rng.uniform(0.0, 2.0 * math.pi) for _ in amps]
        twist = self._rng.uniform(0.0, 2.0 * math.pi)

        k = 72
        step = max(0.6, radius / 26.0)
        pts: list[Point] = []
        water: list[bool] = []
        for i in range(k):
            ang = 2.0 * math.pi * i / k
            dx, dy = math.cos(ang), math.sin(ang)
            r, bound, hit_water = radius, False, False
            d = step
            while d <= radius:
                x, y = ccx + dx * d, ccy + dy * d
                if x < 0 or y < 0 or x > width or y > height:
                    r, bound = max(step, d - step), True
                    break
                e = sample(x, y)
                if e < 0.0:                       # water — stop just shy of it
                    r, bound, hit_water = max(step, d - step), True, True
                    break
                if e > ceil:                      # too high/steep to build on
                    r, bound = max(step, d - step), True
                    break
                d += step
            if not bound:                         # free (inland) edge — ragged it up
                wob = 1.0 + sum(a * math.sin((h + 1) * (ang + twist) + p)
                                for h, (a, p) in enumerate(zip(amps, phases)))
                r *= max(0.6, min(1.0, wob))
            if not hit_water:                     # keep a minimum core size, but
                r = max(r, radius * 0.28)         # never push past the shoreline
            pts.append((ccx + dx * r, ccy + dy * r))
            water.append(hit_water)
        return pts, self._coast_edge(pts, water)

    @staticmethod
    def _coast_edge(pts: list[Point],
                    water: list[bool]) -> tuple[Point, Point] | None:
        """The town's shoreline as a single chord: endpoints of the longest run of
        consecutive water-bounded rays. ``None`` if the town meets no water (inland)
        or is ringed by it (an islet has no one coast to open the wall along)."""
        n = len(water)
        if not any(water) or all(water):
            return None
        start0 = next(i for i in range(n) if not water[i])  # begin off the water
        order = [(start0 + j) % n for j in range(n)]
        best_len, best = 0, (0, 0)
        j = 0
        while j < n:
            if water[order[j]]:
                end = j
                while end < n and water[order[end]]:
                    end += 1
                if end - j > best_len:
                    best_len, best = end - j, (order[j], order[end - 1])
                j = end
            else:
                j += 1
        if best_len < 2:
            return None
        return (pts[best[0]], pts[best[1]])

    def _apply_coast(
        self, footprint: list[Point], cx: float, cy: float, radius: float
    ) -> tuple[list[Point], tuple[Point, Point] | None]:
        """Cut the footprint with a straight coastline; return (land, water_edge)."""
        ang = self._rng.uniform(0.0, 2.0 * math.pi)
        nx, ny = math.cos(ang), math.sin(ang)  # outward (toward-water) normal
        offset = radius * self._rng.uniform(-0.15, 0.25)
        mx, my = cx + nx * offset, cy + ny * offset
        # Dedup near-coincident vertices the clip can emit, so the coast edge has
        # two distinct, ring-adjacent endpoints (a near-duplicate would otherwise
        # collapse water_edge to a point and leave the wall closed over the sea).
        land = _dedup_polygon(clip_halfplane(footprint, mx, my, nx, ny))
        if len(land) < 3:
            return footprint, None
        on_line = [p for p in land if abs((p[0] - mx) * nx + (p[1] - my) * ny) < 1e-6]
        # The coast edge is the two *farthest-apart* on-line points (its true
        # endpoints), not just the first two.
        edge = _two_farthest(on_line) if len(on_line) >= 2 else None
        return land, edge

    # -- wards -----------------------------------------------------------

    @staticmethod
    def _ward_count(population: int) -> int:
        return max(3, min(60, round(population / 180)))

    def _ward_seeds(self, outline: list[Point], clip_hull: list[Point],
                    population: int) -> list[Point]:
        """Rejection-sample ward centres inside the (possibly concave) ``outline``,
        then Lloyd-relax them against the convex ``clip_hull`` so the wards come out
        evenly sized. Relaxation uses the hull because :func:`voronoi_cells` needs a
        convex bound; a centroid that drifts into a bay just yields a ward that
        clips away to nothing later, which is harmless."""
        n = self._ward_count(population)
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

        seeds: list[Point] = []
        attempts = 0
        cap = n * 400
        while len(seeds) < n and attempts < cap:
            attempts += 1
            p = (self._rng.uniform(x0, x1), self._rng.uniform(y0, y1))
            if point_in_polygon(p, outline):
                seeds.append(p)

        for _ in range(2):  # Lloyd relaxation, clipped to the convex hull
            cells = voronoi_cells(seeds, clip_hull)
            seeds = [polygon_centroid(c) if len(c) >= 3 else s for c, s in zip(cells, seeds)]
        return seeds

    def _build_wards(
        self,
        polys: list[list[Point]],
        coastal: bool,
        water_edge: tuple[Point, Point] | None,
        culture: str,
        kind_pool: list[str],
        purpose: str = "general",
    ) -> tuple[list[Ward], int]:
        """Name + classify each non-degenerate ward (central = market, or a purpose
        landmark; coastal nearest-water = docks; rest drawn from the weighted
        ``kind_pool``). Returns the wards and the *hub* ward's id (the central
        ward — a landmark when ``purpose`` set one, else the market)."""
        valid = [(i, p) for i, p in enumerate(polys) if len(p) >= 3]
        if not valid:
            return [], 0
        centers = {i: polygon_centroid(p) for i, p in valid}

        mean_x = sum(c[0] for c in centers.values()) / len(centers)
        mean_y = sum(c[1] for c in centers.values()) / len(centers)
        market = min(centers, key=lambda i: (centers[i][0] - mean_x) ** 2
                     + (centers[i][1] - mean_y) ** 2)
        hub_kind = _LANDMARK_KIND.get(purpose, _MARKET)  # central ward's kind

        docks = None
        if coastal and water_edge is not None:
            wx = (water_edge[0][0] + water_edge[1][0]) / 2
            wy = (water_edge[0][1] + water_edge[1][1]) / 2
            # Exclude the market ward so a small coastal town (where the central
            # ward is also the one nearest the water) still gets a distinct docks.
            candidates = [i for i in centers if i != market]
            if candidates:
                docks = min(candidates, key=lambda i: (centers[i][0] - wx) ** 2
                            + (centers[i][1] - wy) ** 2)

        wards: list[Ward] = []
        hub_id = 0
        for new_id, (i, poly) in enumerate(valid):
            if i == market:
                kind = hub_kind
                hub_id = new_id
            elif i == docks:
                kind = _DOCKS
            else:
                kind = self._rng.choice(kind_pool)
            wards.append(Ward(new_id, poly, centers[i], self._names.place(culture), kind))
        return wards, hub_id

    # -- lots (recursive bisection of each ward) -------------------------

    def _build_lots(
        self,
        wards: list[Ward],
        cfg: SettlementConfig,
        grid_axes: tuple[Point, Point] | None = None,
    ) -> list[Lot]:
        """Subdivide each buildable ward into building plots (insets become the
        building footprints, leaving gaps that read as alleys). When ``grid_axes``
        is given (grid layout) the bisection splits along the town's grid axes, so
        lots come out rectangular and aligned to the streets."""
        lots: list[Lot] = []
        lot_id = 0
        size_factor = _lot_size_factor(cfg.wealth)       # wealth ⇒ plot size
        jitter_factor = _block_jitter_factor(cfg.era, cfg.wealth)  # era ⇒ regularity
        for ward in wards:
            factor = _WARD_LOT_FACTOR.get(ward.kind, 1.0)
            if factor is None:  # open ward (e.g. market square) — no buildings
                continue
            target = cfg.lot_size * factor * size_factor
            for parcel in self._subdivide(ward.polygon, target, cfg.irregularity,
                                          jitter_factor, grid_axes=grid_axes):
                building = inset_convex(parcel, self._building_margin(parcel))
                if len(building) >= 3:
                    lots.append(Lot(lot_id, building, ward.id))
                    lot_id += 1
        return lots

    @staticmethod
    def _building_margin(parcel: list[Point]) -> float:
        """Gap inset for a parcel — proportional to its size, so tiny slivers
        collapse to nothing (and get dropped) rather than becoming buildings."""
        return min(0.6, 0.16 * math.sqrt(max(0.0, polygon_area(parcel))))

    def _subdivide(
        self, poly: list[Point], target_area: float, irregularity: float,
        jitter_factor: float = 1.0, depth: int = 0,
        grid_axes: tuple[Point, Point] | None = None,
    ) -> list[list[Point]]:
        """Recursively bisect a convex polygon until each piece is ≤ ``target_area``."""
        if depth >= 14 or len(poly) < 3 or polygon_area(poly) <= target_area:
            return [poly]
        halves = self._bisect(poly, irregularity, jitter_factor, grid_axes)
        if halves is None:
            return [poly]
        a, b = halves
        return (self._subdivide(a, target_area, irregularity, jitter_factor,
                                depth + 1, grid_axes)
                + self._subdivide(b, target_area, irregularity, jitter_factor,
                                  depth + 1, grid_axes))

    def _bisect(
        self, poly: list[Point], irregularity: float, jitter_factor: float = 1.0,
        grid_axes: tuple[Point, Point] | None = None,
    ) -> tuple[list[Point], list[Point]] | None:
        """Split a convex polygon near the middle with jitter. The split axis is the
        polygon's longest axis (longest edge as proxy) — or, when ``grid_axes`` is
        given, whichever of the two grid axes the polygon spans furthest along, so
        the cuts stay aligned to the street grid. Returns the halves, or None."""
        if grid_axes is not None:
            ux, uy = self._grid_split_axis(poly, grid_axes)
        else:
            n = len(poly)
            best = -1.0
            dx = dy = 0.0
            for i in range(n):
                ax, ay = poly[i]
                bx, by = poly[(i + 1) % n]
                length2 = (bx - ax) ** 2 + (by - ay) ** 2
                if length2 > best:
                    best, dx, dy = length2, bx - ax, by - ay
            dlen = math.hypot(dx, dy)
            if dlen < 1e-9:
                return None
            ux, uy = dx / dlen, dy / dlen  # unit vector along the split axis

        proj = [x * ux + y * uy for x, y in poly]
        lo, hi = min(proj), max(proj)
        span = hi - lo
        if span < 1e-9:
            return None
        jitter = (0.12 + 0.22 * irregularity) * span * jitter_factor
        t = (lo + hi) / 2 + self._rng.fuzzy(0.0, jitter)
        t = max(lo + 0.2 * span, min(hi - 0.2 * span, t))  # keep splits off the slivers

        mx, my = ux * t, uy * t
        a = clip_halfplane(poly, mx, my, ux, uy)     # keep proj·u <= t
        b = clip_halfplane(poly, mx, my, -ux, -uy)   # keep proj·u >= t
        if len(a) < 3 or len(b) < 3:
            return None
        return a, b

    # -- streets (road network over ward adjacency) ----------------------

    def _build_streets(
        self,
        wards: list[Ward],
        footprint: list[Point],
        cfg: SettlementConfig,
        water_edge: tuple[Point, Point] | None,
        hub: Point | None = None,
        planned: bool = False,
        coastal: bool = False,
    ) -> tuple[list[Street], list[Point]]:
        """Build the street network. A ``planned`` (grid) town lays a geometric grid
        aligned to the town's long axis; otherwise the classic organic network (MST
        + a few loops over adjacent wards, plus main roads from each gate to the
        ``hub`` — the central landmark/market). ``coastal`` adds a harbour gate."""
        if planned:
            return self._build_grid_streets(wards, footprint, cfg, water_edge)
        if len(wards) < 2:
            return [], []
        adj, mids = self._ward_adjacency(wards)
        centers = {w.id: w.center for w in wards}
        n = len(wards)  # ward ids are a contiguous 0..n-1 range (see _build_wards)

        inf = float("inf")

        def dist2(i: int, j: int) -> float:
            if j not in adj[i]:
                return inf  # only adjacent wards can share a street
            (ax, ay), (bx, by) = centers[i], centers[j]
            return (ax - bx) ** 2 + (ay - by) ** 2

        edges = _graph.prim_mst(n, dist2)
        chosen = {frozenset(e) for e in edges}
        # A few extra adjacency edges → loops (less tree-like).
        for i in range(n):
            for j in adj[i]:
                key = frozenset((i, j))
                if j > i and key not in chosen and self._rng.chance(0.15):
                    edges.append((i, j))
                    chosen.add(key)

        streets: list[Street] = []
        for i, j in edges:
            if j not in adj[i]:
                continue  # disconnected fallback: skip a non-adjacent MST edge
            mid = mids.get(frozenset((i, j)))
            path = [centers[i], mid, centers[j]] if mid else [centers[i], centers[j]]
            streets.append(Street(path, "minor"))

        # Main roads: the hub (central landmark/market) ↔ each gate.
        focus = hub
        if focus is None:
            focus = next((w.center for w in wards if w.kind == _MARKET),
                         wards[0].center)
        gates = self._gates(footprint, coastal, water_edge)
        for gate in gates:
            streets.append(Street([focus, gate], "main"))
        return streets, gates

    @staticmethod
    def _grid_split_axis(
        poly: list[Point], grid_axes: tuple[Point, Point]
    ) -> Point:
        """Of the two grid axes, the unit axis the polygon spans furthest along —
        cutting across it divides the block's long dimension while keeping the cut
        parallel to a street."""
        (a1x, a1y), (a2x, a2y) = grid_axes
        p1 = [x * a1x + y * a1y for x, y in poly]
        p2 = [x * a2x + y * a2y for x, y in poly]
        s1, s2 = max(p1) - min(p1), max(p2) - min(p2)
        return (a1x, a1y) if s1 >= s2 else (a2x, a2y)

    # -- grid streets (geometric grid aligned to the long axis) ----------

    @staticmethod
    def _principal_axis(poly: list[Point]) -> tuple[Point, Point, Point]:
        """Centroid + (major, minor) unit axes of a polygon, from the vertex
        covariance. The major axis follows the footprint's elongation, so a grid
        laid along it reads as a deliberately planned town that fits its shape."""
        cx, cy = polygon_centroid(poly)
        sxx = sxy = syy = 0.0
        for x, y in poly:
            dx, dy = x - cx, y - cy
            sxx += dx * dx
            sxy += dx * dy
            syy += dy * dy
        theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)  # major-axis orientation
        u = (math.cos(theta), math.sin(theta))
        v = (-u[1], u[0])
        return (cx, cy), u, v

    def _build_grid_streets(
        self,
        wards: list[Ward],
        footprint: list[Point],
        cfg: SettlementConfig,
        water_edge: tuple[Point, Point] | None,
    ) -> tuple[list[Street], list[Point]]:
        """Two families of parallel avenues — along the long axis and across it —
        clipped to the footprint. The central line of each family is a ``"main"``
        thoroughfare; gates sit where the two mains exit the footprint (plus a
        harbour gate when coastal)."""
        if len(footprint) < 3:
            return [], []
        (cx, cy), u, v = self._principal_axis(footprint)
        # Block spacing from the plot size, widened a touch for a modern (high-era)
        # planned town. Deterministic — a grid needs no jitter.
        spacing = max(4.0, math.sqrt(cfg.lot_size) * 2.6 * (1.0 + 0.3 * (cfg.era - 0.5)))

        # Project vertices onto each axis to learn how many lines fit, centred on
        # the centroid so the grid is symmetric about the middle of the town.
        def _extent(axis: Point) -> tuple[float, float]:
            proj = [(x - cx) * axis[0] + (y - cy) * axis[1] for x, y in footprint]
            return min(proj), max(proj)

        streets: list[Street] = []

        def _family(direction: Point, offset_axis: Point) -> list[Street]:
            """Lines running along ``direction``, stepped along ``offset_axis``."""
            lo, hi = _extent(offset_axis)
            k_lo, k_hi = math.ceil(lo / spacing), math.floor(hi / spacing)
            out: list[Street] = []
            for k in range(k_lo, k_hi + 1):
                off = k * spacing
                px, py = cx + off * offset_axis[0], cy + off * offset_axis[1]
                seg = clip_line_to_convex(footprint, px, py, direction[0], direction[1])
                if seg is not None:
                    # k == 0 is the central thoroughfare → "main".
                    out.append(Street([seg[0], seg[1]], "main" if k == 0 else "minor"))
            return out

        avenues = _family(u, v)   # along the long axis
        crosses = _family(v, u)   # across it
        streets.extend(avenues)
        streets.extend(crosses)

        # Gates: the endpoints of the two central thoroughfares (where they pierce
        # the perimeter), plus a harbour gate at the coastline midpoint.
        gates: list[Point] = []
        for fam in (avenues, crosses):
            main = next((st for st in fam if st.kind == "main"), None)
            if main is not None:
                gates.extend([main.path[0], main.path[-1]])
        if cfg.coastal and water_edge is not None:
            gates.append(((water_edge[0][0] + water_edge[1][0]) / 2,
                          (water_edge[0][1] + water_edge[1][1]) / 2))
        return streets, gates

    @staticmethod
    def _ward_adjacency(
        wards: list[Ward],
    ) -> tuple[dict[int, set[int]], dict[frozenset, Point]]:
        """Wards are adjacent when their polygons share an edge (≥2 near-identical
        vertices). Returns adjacency sets and the shared-edge midpoint per pair."""
        eps2 = 1e-3 ** 2
        adj: dict[int, set[int]] = {w.id: set() for w in wards}
        mids: dict[frozenset, Point] = {}
        for a in range(len(wards)):
            for b in range(a + 1, len(wards)):
                wa, wb = wards[a], wards[b]
                shared: list[Point] = []
                for pa in wa.polygon:
                    for pb in wb.polygon:
                        if (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2 <= eps2:
                            shared.append(pa)
                            break
                if len(shared) >= 2:
                    adj[wa.id].add(wb.id)
                    adj[wb.id].add(wa.id)
                    p, q = _two_farthest(shared)
                    mids[frozenset((wa.id, wb.id))] = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        return adj, mids

    def _gates(
        self,
        footprint: list[Point],
        coastal: bool,
        water_edge: tuple[Point, Point] | None,
    ) -> list[Point]:
        """A few entrances on the footprint perimeter (evenly spaced), plus a
        harbour gate at the coastline midpoint when coastal."""
        cx, cy = polygon_centroid(footprint)
        verts = sorted(footprint, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
        gates: list[Point] = []
        k = 3
        if verts:
            for i in range(k):
                gates.append(verts[(i * len(verts)) // k])
        if coastal and water_edge is not None:
            gates.append(((water_edge[0][0] + water_edge[1][0]) / 2,
                          (water_edge[0][1] + water_edge[1][1]) / 2))
        return gates

    # -- wall ------------------------------------------------------------

    def _build_wall(
        self,
        footprint: list[Point],
        walled: bool,
        coastal: bool,
        water_edge: tuple[Point, Point] | None,
        gates: list[Point],
    ) -> "Wall | None":
        """A wall ring around the footprint (towers at each corner). When coastal
        the ring is opened along the coast edge (a harbour, no wall over water).
        Gate gaps sit at the perimeter gates."""
        if not walled or len(footprint) < 3:
            return None
        ring = list(footprint)
        # Grid gates land mid-edge, not on a footprint vertex; splice them into the
        # ring so the gate-gap + gatehouse logic below treats them as corners (as it
        # already does for organic gates, which are footprint vertices). Skip any
        # gate on the coast edge — that side is opened as a harbour, and inserting a
        # vertex there would break the "omit the coast edge" adjacency check.
        for g in gates:
            if (coastal and water_edge is not None
                    and _point_segment_dist(g, water_edge[0], water_edge[1]) <= 1e-6):
                continue
            ring = _insert_on_ring(ring, g)
        closed = True
        if coastal and water_edge is not None:
            i0 = _index_of(ring, water_edge[0])
            i1 = _index_of(ring, water_edge[1])
            if i0 is not None and i1 is not None:
                opened = _open_ring_excluding_edge(ring, i0, i1)
                if opened is not None:
                    ring, closed = opened, False
        # Wall gates = gates that coincide with a ring vertex (the harbour gate,
        # being the coast-edge midpoint, is not on the ring and is skipped).
        ring_keys = {(round(x, 3), round(y, 3)) for x, y in ring}
        wall_gates = [g for g in gates if (round(g[0], 3), round(g[1], 3)) in ring_keys]
        return Wall(ring=ring, closed=closed, gates=wall_gates)
