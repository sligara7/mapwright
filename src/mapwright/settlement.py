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
from dataclasses import dataclass, field

from . import _graph, _serde
from ._geometry import (
    Point,
    clip_halfplane,
    convex_hull,
    inset_convex,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
    voronoi_cells,
)
from .names import NameGenerator
from .rng import SeededRNG


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


# Single source of truth for the numeric knobs: (name, type, min, max, description).
_SPEC: list[tuple] = [
    ("population", int, 20, 200_000,
     "Rough inhabitant count; drives the town's size and ward count."),
    ("irregularity", float, 0.0, 1.0,
     "0 = smooth round footprint; 1 = ragged, organic outline."),
    ("lot_size", float, 2.0, 60.0,
     "Target building-plot area (tiles²); smaller = denser, finer lots."),
]
# Boolean flags: (name, description). Kept separate from _SPEC (no clamping).
_FLAG_SPEC: list[tuple] = [
    ("walled", "Surround the town with a defensive wall (recorded now; drawn later)."),
    ("coastal", "Place the town on a coastline — adds a straight water edge."),
]

# Ward kinds. One central market, optional dockside ward when coastal, and a
# residential-heavy weighted mix for the rest.
_MARKET = "market"
_DOCKS = "docks"
_OTHER_KINDS: list[str] = (
    ["residential"] * 5 + ["craftsmen"] * 3 + ["noble"] * 2 + ["slums"] * 2
    + ["temple"] * 1 + ["garrison"] * 1
)

# Per-ward lot sizing: a multiplier on the base ``lot_size`` (bigger ⇒ larger,
# fewer plots), or ``None`` for an open ward with no buildings. Kinds absent from
# this map use the base size (factor 1.0).
_WARD_LOT_FACTOR: dict[str, float | None] = {
    "market": None,    # open market square — no buildings
    "noble": 3.0,      # large estates / manor grounds
    "garrison": 2.0,   # a few big structures
    "slums": 0.5,      # cramped, tiny plots
}


@dataclass
class SettlementConfig:
    """Knobs for :meth:`SettlementGenerator.generate`. Defaults = a small town."""

    population: int = 2000
    """Rough inhabitant count; drives size and ward count."""
    irregularity: float = 0.5
    """0..1 — how ragged the town outline is."""
    lot_size: float = 8.0
    """Target building-plot area in tiles² (smaller ⇒ denser, finer lots)."""
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

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "irregularity": self.irregularity,
            "lot_size": self.lot_size,
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
    """Generated town layout: footprint, wards, lots, streets, gates, wall, coast."""

    width: int
    height: int
    name: str
    footprint: list[Point]
    wards: list[Ward]
    lots: list[Lot] = field(default_factory=list)
    streets: list[Street] = field(default_factory=list)
    gates: list[Point] = field(default_factory=list)
    wall: Wall | None = None
    walled: bool = False
    coastal: bool = False
    water_edge: tuple[Point, Point] | None = None  # coastline segment, if coastal

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        """A JSON-safe mapping of the whole town, round-tripping via :meth:`from_dict`."""
        return {
            "schema": "mapwright/settlement@4",
            "width": self.width,
            "height": self.height,
            "name": self.name,
            "footprint": [[x, y] for x, y in self.footprint],
            "wards": [w.to_dict() for w in self.wards],
            "lots": [lot.to_dict() for lot in self.lots],
            "streets": [st.to_dict() for st in self.streets],
            "gates": [[x, y] for x, y in self.gates],
            "wall": None if self.wall is None else self.wall.to_dict(),
            "walled": self.walled,
            "coastal": self.coastal,
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
            walled=bool(data.get("walled", False)),
            coastal=bool(data.get("coastal", False)),
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
    ) -> Settlement:
        """Generate a town. ``config`` shapes it (population, walls, coast, outline);
        ``culture`` selects the namebase for the town and ward names."""
        cfg = config or SettlementConfig()
        cx, cy = width / 2, height / 2
        radius = self._radius(cfg.population, width, height)

        footprint = self._footprint(cx, cy, radius, cfg.irregularity)
        water_edge: tuple[Point, Point] | None = None
        if cfg.coastal:
            footprint, water_edge = self._apply_coast(footprint, cx, cy, radius)

        seeds = self._ward_seeds(footprint, cfg.population)
        polys = voronoi_cells(seeds, footprint)
        wards = self._build_wards(polys, cfg.coastal, water_edge, culture)
        lots = self._build_lots(wards, cfg)
        streets, gates = self._build_streets(wards, footprint, cfg.coastal, water_edge)
        wall = self._build_wall(footprint, cfg.walled, cfg.coastal, water_edge, gates)

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
            walled=cfg.walled,
            coastal=cfg.coastal,
            water_edge=water_edge,
        )

    # -- footprint -------------------------------------------------------

    @staticmethod
    def _radius(population: int, width: int, height: int) -> float:
        """Town radius (long axis): grows with √population, capped to leave the
        canvas a margin even once the footprint is elongated/lobed."""
        fit = 0.40 * min(width, height)
        return min(fit, max(6.0, math.sqrt(population) * 0.45))

    def _footprint(self, cx: float, cy: float, radius: float, irregularity: float) -> list[Point]:
        """An organic, convex town outline — clearly non-circular: elongated along a
        random axis, lopsided via low-frequency radial lobes, then convex-hulled so
        wards still clip cleanly. ``radius`` is the long-axis half-extent.

        (Low-frequency harmonics — not per-vertex jitter — are what survive the
        convex hull as real lobes; independent jitter just hulls back to a circle.)
        """
        theta = self._rng.uniform(0.0, 2.0 * math.pi)          # random orientation
        aspect = 1.0 + (0.25 + 0.45 * irregularity) * self._rng.random()  # elongation
        a1 = 0.16 * irregularity * self._rng.uniform(0.4, 1.0)  # lopsided (1st harmonic)
        a2 = 0.10 * irregularity * self._rng.uniform(0.4, 1.0)  # oval-ish (2nd harmonic)
        p1 = self._rng.uniform(0.0, 2.0 * math.pi)
        p2 = self._rng.uniform(0.0, 2.0 * math.pi)
        ct, st = math.cos(theta), math.sin(theta)

        k = 40
        pts: list[Point] = []
        for i in range(k):
            ang = 2.0 * math.pi * i / k
            rr = 1.0 + a1 * math.sin(ang + p1) + a2 * math.sin(2 * ang + p2)
            ux = rr * math.cos(ang)              # long axis (= radius)
            uy = rr * math.sin(ang) / aspect     # squash the short axis → ellipse
            x = radius * (ux * ct - uy * st)     # rotate into the random frame
            y = radius * (ux * st + uy * ct)
            pts.append((cx + x, cy + y))
        return convex_hull(pts)

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

    def _ward_seeds(self, footprint: list[Point], population: int) -> list[Point]:
        """Rejection-sample ward centres inside the footprint, then Lloyd-relax
        them a couple of passes so the wards come out evenly sized."""
        n = self._ward_count(population)
        xs = [p[0] for p in footprint]
        ys = [p[1] for p in footprint]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

        seeds: list[Point] = []
        attempts = 0
        cap = n * 400
        while len(seeds) < n and attempts < cap:
            attempts += 1
            p = (self._rng.uniform(x0, x1), self._rng.uniform(y0, y1))
            if point_in_polygon(p, footprint):
                seeds.append(p)

        for _ in range(2):  # Lloyd relaxation, clipped to the footprint
            cells = voronoi_cells(seeds, footprint)
            seeds = [polygon_centroid(c) if len(c) >= 3 else s for c, s in zip(cells, seeds)]
        return seeds

    def _build_wards(
        self,
        polys: list[list[Point]],
        coastal: bool,
        water_edge: tuple[Point, Point] | None,
        culture: str,
    ) -> list[Ward]:
        """Name + classify each non-degenerate ward (central = market, coastal
        nearest-water = docks, rest a residential-heavy mix)."""
        valid = [(i, p) for i, p in enumerate(polys) if len(p) >= 3]
        if not valid:
            return []
        centers = {i: polygon_centroid(p) for i, p in valid}

        mean_x = sum(c[0] for c in centers.values()) / len(centers)
        mean_y = sum(c[1] for c in centers.values()) / len(centers)
        market = min(centers, key=lambda i: (centers[i][0] - mean_x) ** 2
                     + (centers[i][1] - mean_y) ** 2)

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
        for new_id, (i, poly) in enumerate(valid):
            if i == market:
                kind = _MARKET
            elif i == docks:
                kind = _DOCKS
            else:
                kind = self._rng.choice(_OTHER_KINDS)
            wards.append(Ward(new_id, poly, centers[i], self._names.place(culture), kind))
        return wards

    # -- lots (recursive bisection of each ward) -------------------------

    def _build_lots(self, wards: list[Ward], cfg: SettlementConfig) -> list[Lot]:
        """Subdivide each buildable ward into building plots (insets become the
        building footprints, leaving gaps that read as alleys)."""
        lots: list[Lot] = []
        lot_id = 0
        for ward in wards:
            factor = _WARD_LOT_FACTOR.get(ward.kind, 1.0)
            if factor is None:  # open ward (e.g. market square) — no buildings
                continue
            target = cfg.lot_size * factor
            for parcel in self._subdivide(ward.polygon, target, cfg.irregularity):
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
        self, poly: list[Point], target_area: float, irregularity: float, depth: int = 0
    ) -> list[list[Point]]:
        """Recursively bisect a convex polygon until each piece is ≤ ``target_area``."""
        if depth >= 14 or len(poly) < 3 or polygon_area(poly) <= target_area:
            return [poly]
        halves = self._bisect(poly, irregularity)
        if halves is None:
            return [poly]
        a, b = halves
        return (self._subdivide(a, target_area, irregularity, depth + 1)
                + self._subdivide(b, target_area, irregularity, depth + 1))

    def _bisect(
        self, poly: list[Point], irregularity: float
    ) -> tuple[list[Point], list[Point]] | None:
        """Split a convex polygon across its longest axis (longest edge as proxy),
        near the middle with jitter. Returns the two halves, or None if it can't."""
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
        jitter = (0.12 + 0.22 * irregularity) * span
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
        coastal: bool,
        water_edge: tuple[Point, Point] | None,
    ) -> tuple[list[Street], list[Point]]:
        """A minor-road network (MST + a few loops) over adjacent wards, plus main
        roads from each town gate to the market."""
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

        # Main roads: market ↔ each gate.
        market = next((w.center for w in wards if w.kind == _MARKET), wards[0].center)
        gates = self._gates(footprint, coastal, water_edge)
        for gate in gates:
            streets.append(Street([market, gate], "main"))
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
