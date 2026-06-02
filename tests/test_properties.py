"""Property-based tests (Hypothesis) over the pure cores and generators.

These complement the example-based suite: instead of fixed inputs, they assert
invariants over wide random input — config clamping never escapes bounds, the
geometry primitives stay well-formed, the graph routines stay structurally
valid, and generated worlds always round-trip and stay in range.
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mapwright import (
    Biome,
    DungeonGenerator,
    RegionalTerrainGenerator,
    SeededRNG,
    Settlement,
    SettlementConfig,
    SettlementGenerator,
    TerrainResult,
    WorldMapConfig,
)
from mapwright._geometry import (
    clip_halfplane,
    convex_hull,
    inset_convex,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
)
from mapwright._graph import astar, prim_mst
from mapwright.config import _SPEC as WORLD_SPEC
from mapwright.settlement import _FLAG_SPEC, _SPEC as SETTLEMENT_SPEC


# -- config clamping ----------------------------------------------------------

def _payload(spec, flags=()):
    fields = {}
    for name, typ, _lo, _hi, _desc in spec:
        fields[name] = (st.integers(-10_000, 10_000) if typ is int
                        else st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False))
    for name, _desc in flags:
        fields[name] = st.booleans()
    return st.fixed_dictionaries(fields)


class TestConfigClamping:
    @given(_payload(WORLD_SPEC))
    def test_world_config_within_bounds(self, payload):
        cfg = WorldMapConfig.from_dict(payload)
        for name, typ, lo, hi, _ in WORLD_SPEC:
            v = getattr(cfg, name)
            assert lo <= v <= hi
            assert isinstance(v, int if typ is int else float)

    @given(_payload(SETTLEMENT_SPEC, _FLAG_SPEC))
    def test_settlement_config_within_bounds(self, payload):
        cfg = SettlementConfig.from_dict(payload)
        for name, typ, lo, hi, _ in SETTLEMENT_SPEC:
            assert lo <= getattr(cfg, name) <= hi
        for name, _ in _FLAG_SPEC:
            assert isinstance(getattr(cfg, name), bool)


# -- geometry primitives ------------------------------------------------------

_coord = st.floats(-100, 100, allow_nan=False, allow_infinity=False)
_points = st.lists(st.tuples(_coord, _coord), min_size=1, max_size=40)


def _is_ccw_convex(poly, eps=1e-6):
    n = len(poly)
    if n < 3:
        return True
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        cx, cy = poly[(i + 2) % n]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if cross < -eps:  # a right turn → not convex CCW
            return False
    return True


class TestGeometryProperties:
    @given(_points)
    def test_convex_hull_is_convex_and_from_input(self, pts):
        hull = convex_hull(pts)
        inputs = {(float(x), float(y)) for x, y in pts}
        assert all(v in inputs for v in hull)   # vertices are real input points
        assert _is_ccw_convex(hull)

    @given(_points)
    def test_centroid_inside_its_convex_hull(self, pts):
        hull = convex_hull(pts)
        if len(hull) >= 3 and polygon_area(hull) > 1e-3:
            assert point_in_polygon(polygon_centroid(hull), hull)

    @given(_points, st.floats(0.1, 5.0))
    def test_inset_never_grows_area(self, pts, dist):
        hull = convex_hull(pts)
        if len(hull) < 3:
            return
        inset = inset_convex(hull, dist)
        if inset:  # non-empty inset
            assert polygon_area(inset) <= polygon_area(hull) + 1e-6
            assert _is_ccw_convex(inset)

    @given(_points, _coord, _coord, _coord, _coord)
    def test_clip_halfplane_yields_subset_of_halfplane(self, pts, mx, my, ax, ay):
        hull = convex_hull(pts)
        clipped = clip_halfplane(hull, mx, my, ax, ay)
        # Every surviving vertex satisfies the half-plane (with a small tolerance).
        for px, py in clipped:
            assert (px - mx) * ax + (py - my) * ay <= 1e-6


# -- graph routines -----------------------------------------------------------

class TestGraphProperties:
    @given(st.lists(st.tuples(st.integers(0, 50), st.integers(0, 50)),
                    min_size=2, max_size=12))
    def test_prim_mst_is_spanning_tree(self, pts):
        def dist2(i, j):
            (ax, ay), (bx, by) = pts[i], pts[j]
            return (ax - bx) ** 2 + (ay - by) ** 2

        edges = prim_mst(len(pts), dist2)
        assert len(edges) == len(pts) - 1
        assert {n for e in edges for n in e} == set(range(len(pts)))

    @given(st.integers(2, 7), st.integers(2, 7),
           st.sets(st.tuples(st.integers(0, 6), st.integers(0, 6)), max_size=12),
           st.integers(0, 10000))
    @settings(max_examples=60, suppress_health_check=[HealthCheck.filter_too_much])
    def test_astar_path_is_valid_or_empty(self, w, h, blocked, _seed):
        cells = {(x, y) for x in range(w) for y in range(h)} - blocked
        start, goal = (0, 0), (w - 1, h - 1)
        if start not in cells or goal not in cells:
            return

        def neighbors(n):
            x, y = n
            return [p for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)) if p in cells]

        path = astar(start, goal, neighbors, lambda a, b: 1.0,
                     lambda n: abs(n[0] - goal[0]) + abs(n[1] - goal[1]))
        if path:
            assert path[0] == start and path[-1] == goal
            assert all(b in neighbors(a) for a, b in zip(path, path[1:]))
            assert all(p not in blocked for p in path)


# -- generation invariants (small maps, capped examples) ----------------------

_SLOW = settings(max_examples=20, deadline=None,
                 suppress_health_check=[HealthCheck.too_slow])


class TestGenerationProperties:
    @given(st.integers(0, 10_000), _payload(WORLD_SPEC))
    @_SLOW
    def test_terrain_valid_and_round_trips(self, seed, cfg_payload):
        cfg = WorldMapConfig.from_dict(cfg_payload)
        t = RegionalTerrainGenerator(SeededRNG(seed)).generate(34, 26, config=cfg)
        valid = set(Biome)
        assert all(c.biome in valid for c in t.cells)
        assert int(t.cell_of.min()) >= 0 and int(t.cell_of.max()) < len(t.cells)
        assert all(0.0 <= c.moisture <= 1.0 and 0.0 <= c.temperature <= 1.0
                   for c in t.cells)
        reloaded = TerrainResult.from_json(t.to_json())
        assert reloaded.to_dict() == t.to_dict()

    @given(st.integers(0, 10_000), st.integers(20, 240), st.integers(20, 240))
    @_SLOW
    def test_dungeon_rooms_in_bounds_and_round_trip(self, seed, w, h):
        from mapwright import Dungeon
        w, h = max(12, w % 60 + 12), max(12, h % 60 + 12)  # keep modest
        d = DungeonGenerator(SeededRNG(seed)).generate(w, h)
        for r in d.rooms:
            assert 0 <= r.x and 0 <= r.y and r.x + r.w <= w and r.y + r.h <= h
        assert Dungeon.from_json(d.to_json()).ascii() == d.ascii()

    @given(st.integers(0, 10_000), _payload(SETTLEMENT_SPEC, _FLAG_SPEC))
    @_SLOW
    def test_settlement_round_trips_and_market_unique(self, seed, cfg_payload):
        cfg = SettlementConfig.from_dict(cfg_payload)
        town = SettlementGenerator(SeededRNG(seed)).generate(80, 80, cfg)
        assert [w.kind for w in town.wards].count("market") <= 1
        assert Settlement.from_dict(town.to_dict()).to_dict() == town.to_dict()
