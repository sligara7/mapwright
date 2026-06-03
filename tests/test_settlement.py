"""Tests for the settlement tier (wards layer): config, generation, serialisation."""

import xml.etree.ElementTree as ET

from mapwright import (
    Lot,
    SeededRNG,
    Settlement,
    SettlementConfig,
    SettlementGenerator,
    SettlementSVGRenderer,
    Street,
    Wall,
    Ward,
)
from mapwright._geometry import point_in_polygon, polygon_area
from mapwright.settlement import _SPEC, _dedup_polygon, _two_farthest


def _town(seed=7, w=90, h=90, **cfg):
    config = SettlementConfig(**cfg) if cfg else None
    return SettlementGenerator(SeededRNG(seed)).generate(w, h, config)


class TestSettlementConfig:
    def test_defaults_and_flag_coercion(self):
        c = SettlementConfig(walled=1, coastal=0)  # truthy/falsy → bool
        assert c.walled is True and c.coastal is False

    def test_clamping(self):
        for name, _typ, lo, hi, _desc in _SPEC:
            assert getattr(SettlementConfig.from_dict({name: lo - 100}), name) >= lo
            assert getattr(SettlementConfig.from_dict({name: hi + 100}), name) <= hi

    def test_from_dict_ignores_unknown(self):
        c = SettlementConfig.from_dict({"population": 500, "bogus": 9})
        assert c.population == 500

    def test_presets_valid(self):
        for name in SettlementConfig.preset_names():
            c = SettlementConfig.preset(name)
            for fname, _typ, lo, hi, _desc in _SPEC:
                assert lo <= getattr(c, fname) <= hi

    def test_json_schema_covers_all_fields(self):
        schema = SettlementConfig.json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {
            "population", "irregularity", "lot_size", "wealth", "era",
            "layout", "purpose", "walled", "coastal"
        }
        assert schema["properties"]["walled"]["type"] == "boolean"
        assert schema["properties"]["lot_size"]["type"] == "number"
        assert schema["properties"]["layout"]["type"] == "string"
        assert schema["properties"]["layout"]["enum"] == ["organic", "grid"]
        assert schema["properties"]["purpose"]["type"] == "string"
        assert "fortress" in schema["properties"]["purpose"]["enum"]

    def test_layout_invalid_falls_back_to_default(self):
        assert SettlementConfig(layout="spiral").layout == "organic"
        assert SettlementConfig(layout="grid").layout == "grid"

    def test_config_round_trip(self):
        c = SettlementConfig(population=4200, irregularity=0.3, lot_size=12.0,
                             wealth=0.2, era=0.8, walled=True, coastal=True)
        assert SettlementConfig.from_dict(c.to_dict()) == c


class TestEraAndWealth:
    def test_neutral_is_byte_identical_to_default(self):
        # wealth=era=0.5 must reproduce the pre-feature output exactly.
        base = SettlementSVGRenderer(scale=7).render(_town(7))
        neutral = SettlementSVGRenderer(scale=7).render(_town(7, wealth=0.5, era=0.5))
        assert base == neutral

    def test_neutral_ward_pool_matches_canonical(self):
        from mapwright.settlement import _OTHER_KINDS, _ward_kind_pool
        assert _ward_kind_pool(0.5) == _OTHER_KINDS

    def test_neutral_shaping_factors_are_identity(self):
        from mapwright.settlement import _block_jitter_factor, _lot_size_factor
        assert _lot_size_factor(0.5) == 1.0
        assert _block_jitter_factor(0.5, 0.5) == 1.0

    def test_poor_is_denser_than_rich(self):
        # Same seed + population: low wealth → many small lots; high wealth → fewer.
        poor = _town(5, w=95, h=95, population=14000, wealth=0.08, era=0.3)
        rich = _town(5, w=95, h=95, population=14000, wealth=0.92, era=0.95)
        assert len(poor.lots) > 2 * len(rich.lots)

    def test_wealth_shifts_ward_mix(self):
        poor = _town(5, population=14000, wealth=0.05)
        rich = _town(5, population=14000, wealth=0.95)
        poor_slums = sum(1 for w in poor.wards if w.kind == "slums")
        rich_nobles = sum(1 for w in rich.wards if w.kind == "noble")
        assert poor_slums >= 1
        assert rich_nobles >= 1
        assert sum(1 for w in rich.wards if w.kind == "slums") < poor_slums

    def test_presets_exist_and_differ(self):
        assert {"shantytown", "metropolis"} <= set(SettlementConfig.preset_names())
        shanty = SettlementGenerator(SeededRNG(5)).generate(
            95, 95, SettlementConfig.preset("shantytown"))
        metro = SettlementGenerator(SeededRNG(5)).generate(
            95, 95, SettlementConfig.preset("metropolis"))
        assert len(shanty.lots) != len(metro.lots)

    def test_deterministic(self):
        a = _town(9, population=8000, wealth=0.2, era=0.7)
        b = _town(9, population=8000, wealth=0.2, era=0.7)
        assert [lot.polygon for lot in a.lots] == [lot.polygon for lot in b.lots]


class TestGridLayout:
    def test_default_layout_is_organic_and_byte_identical(self):
        base = SettlementSVGRenderer(scale=7).render(_town(7))
        organic = SettlementSVGRenderer(scale=7).render(_town(7, layout="organic"))
        assert base == organic

    def test_grid_differs_from_organic(self):
        organic = _town(7, population=8000, layout="organic")
        grid = _town(7, population=8000, layout="grid")
        assert [s.path for s in grid.streets] != [s.path for s in organic.streets]

    def test_grid_streets_are_straight_segments(self):
        grid = _town(7, population=8000, layout="grid")
        assert grid.streets
        # Every grid street is a single straight segment (two endpoints).
        assert all(len(s.path) == 2 for s in grid.streets)

    def test_grid_streets_lie_within_footprint(self):
        grid = _town(7, population=8000, layout="grid")
        for s in grid.streets:
            for x, y in s.path:
                assert point_in_polygon((x, y), grid.footprint) or _near_perimeter(
                    (x, y), grid.footprint)

    def test_grid_has_main_thoroughfares_and_gates(self):
        grid = _town(7, population=8000, layout="grid")
        assert any(s.kind == "main" for s in grid.streets)
        assert len(grid.gates) >= 2

    def test_grid_walled_puts_gates_on_the_wall(self):
        grid = _town(7, population=8000, layout="grid", walled=True)
        assert grid.wall is not None
        # Grid gates land mid-edge but must be spliced into the wall ring as gaps.
        assert len(grid.wall.gates) >= 1
        ring_keys = {(round(x, 3), round(y, 3)) for x, y in grid.wall.ring}
        for gx, gy in grid.wall.gates:
            assert (round(gx, 3), round(gy, 3)) in ring_keys

    def test_grid_coastal_walled_opens_and_renders(self):
        grid = _town(7, population=8000, layout="grid", walled=True, coastal=True)
        assert grid.wall is not None and grid.wall.closed is False
        # Renders without error.
        assert SettlementSVGRenderer(scale=7).render(grid).startswith("<svg")

    def test_grid_deterministic(self):
        a = _town(9, population=8000, layout="grid")
        b = _town(9, population=8000, layout="grid")
        assert [s.path for s in a.streets] == [s.path for s in b.streets]

    def test_grid_lots_align_to_axes_more_than_organic(self):
        # Grid lots should have edges that line up with the town's grid axes far
        # better than the organically-bisected ones.
        grid = _town(7, population=9000, layout="grid", era=0.9)
        organic = _town(7, population=9000, layout="organic", era=0.9)
        _, u, v = SettlementGenerator._principal_axis(grid.footprint)
        g = sum(_edge_align_error(lot.polygon, u, v) for lot in grid.lots) / len(grid.lots)
        o = sum(_edge_align_error(lot.polygon, u, v) for lot in organic.lots) / len(organic.lots)
        assert g < o


class TestPurpose:
    def test_default_purpose_is_general_and_byte_identical(self):
        base = SettlementSVGRenderer(scale=7).render(_town(7))
        general = SettlementSVGRenderer(scale=7).render(_town(7, purpose="general"))
        assert base == general

    def test_general_has_no_landmark(self):
        assert _town(7).landmark is None

    def test_invalid_purpose_falls_back(self):
        assert SettlementConfig(purpose="bogus").purpose == "general"

    def test_purpose_sets_central_landmark(self):
        town = _town(7, population=5000, purpose="fortress")
        assert town.landmark is not None
        assert town.landmark.kind == "citadel"
        # The landmark is a real ward, and that ward carries the landmark kind.
        ward = next(w for w in town.wards if w.id == town.landmark.ward)
        assert ward.kind == "citadel"
        assert ward.center == town.landmark.center

    def test_purpose_kinds_map(self):
        cases = {"trade": "market", "fortress": "citadel", "religious": "temple",
                 "extraction": "mine", "transit": "plaza"}
        for purpose, kind in cases.items():
            town = _town(7, population=5000, purpose=purpose)
            assert town.landmark is not None and town.landmark.kind == kind

    def test_no_plain_market_when_purpose_relabels_centre(self):
        # A fortress town's central ward is a citadel, not a market.
        town = _town(7, population=5000, purpose="fortress")
        assert all(w.kind != "market" for w in town.wards)

    def test_purpose_biases_ward_mix(self):
        from mapwright.settlement import _ward_kind_pool
        base = _ward_kind_pool(0.5, "general").count("garrison")
        forty = _ward_kind_pool(0.5, "fortress").count("garrison")
        assert forty > base

    def test_main_roads_focus_on_landmark(self):
        town = _town(7, population=5000, purpose="fortress")
        mains = [s for s in town.streets if s.kind == "main"]
        assert mains
        lc = town.landmark.center
        # Every main road touches the landmark centre at one end.
        assert all(lc in (s.path[0], s.path[-1]) for s in mains)

    def test_landmark_round_trips(self):
        town = _town(7, population=5000, purpose="religious")
        loaded = Settlement.from_dict(town.to_dict())
        assert loaded.purpose == "religious"
        assert loaded.landmark is not None
        assert loaded.landmark.to_dict() == town.landmark.to_dict()

    def test_renderer_draws_landmark_only_when_present(self):
        plain = SettlementSVGRenderer(scale=7).render(_town(7))
        marked = SettlementSVGRenderer(scale=7).render(
            _town(7, population=5000, purpose="fortress"))
        assert plain.count("polygon") < marked.count("polygon")

    def test_purpose_presets(self):
        for name in ("fortress_town", "pilgrimage_site", "mining_camp"):
            town = SettlementGenerator(SeededRNG(3)).generate(
                90, 90, SettlementConfig.preset(name))
            assert town.landmark is not None

    def test_grid_plus_purpose(self):
        town = _town(7, population=8000, layout="grid", purpose="fortress")
        assert town.landmark is not None and town.landmark.kind == "citadel"
        assert all(len(s.path) == 2 for s in town.streets)  # still a grid


def _edge_align_error(poly, u, v):
    import math
    errs = []
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        L = math.hypot(ex, ey)
        if L < 1e-9:
            continue
        ex, ey = ex / L, ey / L
        du = abs(ex * u[0] + ey * u[1])  # ~1 when parallel to u
        dv = abs(ex * v[0] + ey * v[1])  # ~1 when parallel to v
        errs.append(min(1.0 - du, 1.0 - dv))  # 0 when aligned to either axis
    return sum(errs) / len(errs) if errs else 0.0


def _near_perimeter(p, poly, tol=1e-6):
    from mapwright.settlement import _point_segment_dist
    m = len(poly)
    return any(_point_segment_dist(p, poly[i], poly[(i + 1) % m]) <= tol
               for i in range(m))


class TestGeneration:
    def test_produces_named_wards(self):
        town = _town()
        assert town.name
        assert len(town.wards) >= 3
        assert all(isinstance(w, Ward) and w.name and w.kind for w in town.wards)

    def test_exactly_one_market(self):
        kinds = [w.kind for w in _town().wards]
        assert kinds.count("market") == 1

    def test_ward_centers_inside_footprint(self):
        town = _town()
        for w in town.wards:
            assert point_in_polygon(w.center, town.footprint)

    def test_ward_polygons_inside_footprint(self):
        # Every ward vertex lies within (or on) the convex footprint.
        town = _town()
        for w in town.wards:
            for vx, vy in w.polygon:
                # tiny epsilon for floating-point boundary points
                assert point_in_polygon((vx, vy), town.footprint) or \
                    _near_boundary((vx, vy), town.footprint)

    def test_deterministic(self):
        a, b = _town(seed=11), _town(seed=11)
        assert a.to_dict() == b.to_dict()

    def test_different_seeds_differ(self):
        assert _town(seed=1).to_dict() != _town(seed=2).to_dict()

    def test_coastal_adds_water_edge_and_docks(self):
        town = _town(seed=5, coastal=True, population=9000)
        assert town.coastal and town.water_edge is not None
        assert any(w.kind == "docks" for w in town.wards)

    def test_small_coastal_town_always_has_distinct_docks(self):
        # Regression: when the nearest-water ward is also the central market ward
        # (common for small coastal towns), docks must still be assigned to a
        # different ward — not silently dropped.
        for seed in range(25):
            town = _town(seed=seed, coastal=True, population=300, w=70, h=70)
            if town.water_edge is None:
                continue
            kinds = [w.kind for w in town.wards]
            assert kinds.count("market") == 1
            assert "docks" in kinds, f"seed {seed}: coastal town has no docks ward"
            assert kinds.count("docks") == 1

    def test_walled_flag_recorded(self):
        assert _town(walled=True).walled is True

    def test_population_scales_ward_count(self):
        small = _town(population=120)
        big = _town(population=20000)
        assert len(big.wards) > len(small.wards)


class TestLots:
    def test_town_has_lots(self):
        town = _town()
        assert len(town.lots) > len(town.wards)  # each buildable ward → several lots
        assert all(isinstance(lot, Lot) and len(lot.polygon) >= 3 for lot in town.lots)

    def test_lots_reference_valid_wards(self):
        town = _town()
        ward_ids = {w.id for w in town.wards}
        assert all(lot.ward in ward_ids for lot in town.lots)

    def test_market_ward_is_open(self):
        # The market ward is an open square — no lots reference it.
        town = _town()
        market = next(w for w in town.wards if w.kind == "market")
        assert all(lot.ward != market.id for lot in town.lots)

    def test_lots_lie_within_their_ward(self):
        town = _town()
        wards = {w.id: w for w in town.wards}
        for lot in town.lots:
            ward = wards[lot.ward]
            for vx, vy in lot.polygon:
                assert point_in_polygon((vx, vy), ward.polygon) or \
                    _near_boundary((vx, vy), ward.polygon, eps=1e-4)

    def test_smaller_lot_size_makes_more_lots(self):
        fine = _town(lot_size=4.0)
        coarse = _town(lot_size=40.0)
        assert len(fine.lots) > len(coarse.lots)

    def test_lots_are_smaller_than_target_plus_slack(self):
        # After subdivision every parcel is at/below target; the inset building is
        # smaller still — so no building greatly exceeds the target lot size.
        town = _town(lot_size=8.0)
        assert all(polygon_area(lot.polygon) <= 8.0 * 3.0 for lot in town.lots)

    def test_deterministic_lots(self):
        assert _town(seed=4).to_dict()["lots"] == _town(seed=4).to_dict()["lots"]


class TestStreets:
    def test_town_has_streets_and_gates(self):
        town = _town()
        assert town.streets and town.gates
        assert all(isinstance(st, Street) and len(st.path) >= 2 for st in town.streets)

    def test_has_main_and_minor(self):
        kinds = {st.kind for st in _town().streets}
        assert "minor" in kinds and "main" in kinds

    def test_main_roads_run_from_market_to_gates(self):
        town = _town()
        market = next(w.center for w in town.wards if w.kind == "market")
        mains = [st for st in town.streets if st.kind == "main"]
        assert len(mains) == len(town.gates)
        for st in mains:
            assert st.path[0] == market
            assert st.path[-1] in town.gates

    def test_minor_network_connects_all_wards(self):
        # The MST over ward adjacency must reach every ward (validates that
        # adjacency detection works). Union-find over minor-street endpoints.
        town = _town()
        centers = {w.center: w.id for w in town.wards}
        parent = {w.id: w.id for w in town.wards}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for st in town.streets:
            if st.kind != "minor":
                continue
            i, j = centers.get(st.path[0]), centers.get(st.path[-1])
            if i is not None and j is not None:
                parent[find(i)] = find(j)
        roots = {find(w.id) for w in town.wards}
        assert len(roots) == 1, "minor streets do not connect all wards"

    def test_coastal_has_harbour_gate(self):
        town = _town(seed=5, coastal=True, population=9000)
        # One extra gate (the harbour) sits on the water edge.
        assert len(town.gates) == 4  # 3 perimeter + 1 harbour

    def test_deterministic_streets(self):
        assert _town(seed=4).to_dict()["streets"] == _town(seed=4).to_dict()["streets"]


class TestWalls:
    def test_no_wall_when_unwalled(self):
        assert _town(walled=False).wall is None

    def test_wall_present_when_walled(self):
        town = _town(walled=True)
        assert isinstance(town.wall, Wall)
        assert town.wall.closed is True            # non-coastal → closed loop
        assert len(town.wall.ring) >= 3
        assert town.wall.gates                     # has gate gaps

    def test_wall_ring_matches_footprint_when_inland(self):
        town = _town(walled=True, coastal=False)
        assert set(town.wall.ring) == set(town.footprint)

    def test_wall_gates_are_on_the_ring(self):
        town = _town(walled=True)
        ring = set(town.wall.ring)
        assert all(g in ring for g in town.wall.gates)

    def test_coastal_wall_is_open_and_skips_harbour(self):
        town = _town(seed=5, walled=True, coastal=True, population=9000)
        assert town.wall is not None
        assert town.wall.closed is False           # opened along the coast
        # The harbour gate (water-edge midpoint) is not a wall gate.
        harbour = town.gates[-1]
        assert harbour not in town.wall.gates

    def test_deterministic_wall(self):
        assert _town(seed=4, walled=True).to_dict()["wall"] == \
            _town(seed=4, walled=True).to_dict()["wall"]


class TestReviewFixes:
    """Regressions for the code-review findings on the settlement tier."""

    # -- #1: degenerate coast clip → wall over water / zero-length water_edge --

    def test_dedup_polygon_drops_near_coincident_vertices(self):
        poly = [(0.0, 0.0), (0.0, 2e-7), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        out = _dedup_polygon(poly)
        assert (0.0, 2e-7) not in out  # collapsed into (0,0)
        assert len(out) == 4

    def test_dedup_polygon_handles_wraparound(self):
        poly = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (1e-9, 1e-9)]
        assert len(_dedup_polygon(poly)) == 3  # last ≈ first → dropped

    def test_two_farthest_picks_real_endpoints_past_duplicate(self):
        # on-line points with a near-duplicate at one end: the coast edge must be
        # the two distinct extremes, never the coincident pair.
        a, b = _two_farthest([(0.0, 0.0), (1e-7, 0.0), (10.0, 0.0)])
        assert {a, b} == {(0.0, 0.0), (10.0, 0.0)} or {a, b} == {(1e-7, 0.0), (10.0, 0.0)}

    def test_coastal_water_edge_always_nondegenerate(self):
        # Across many coastal seeds the coast edge has two distinct endpoints.
        for seed in range(60):
            town = _town(seed=seed, coastal=True, population=4000)
            if town.water_edge is None:
                continue
            (ax, ay), (bx, by) = town.water_edge
            assert (ax - bx) ** 2 + (ay - by) ** 2 > 1e-6

    def test_coastal_walled_town_opens_wall_at_harbour(self):
        for seed in range(40):
            town = _town(seed=seed, coastal=True, walled=True, population=6000)
            if town.wall is not None and town.water_edge is not None:
                assert town.wall.closed is False  # never a closed loop over water

    # -- #4: _pull must not overshoot the midpoint on a short edge --

    def test_pull_clamped_to_half_segment(self):
        pull = SettlementSVGRenderer._pull
        # Pull from (0,0) toward (2,0) by an over-large distance (5): clamp to 1.
        px, py = pull((0.0, 0.0), (2.0, 0.0), 5.0)
        assert abs(px - 1.0) < 1e-9 and abs(py) < 1e-9  # midpoint, not past it


class TestSerialisation:
    def test_dict_round_trip(self):
        town = _town(coastal=True, walled=True)
        loaded = Settlement.from_dict(town.to_dict())
        assert loaded.to_dict() == town.to_dict()

    def test_json_round_trip(self):
        town = _town()
        assert Settlement.from_json(town.to_json()).to_dict() == town.to_dict()

    def test_to_dict_is_json_safe(self):
        import json
        json.dumps(_town(coastal=True).to_dict())

    def test_schema_tag(self):
        assert _town().to_dict()["schema"] == "mapwright/settlement@5"

    def test_old_payload_without_new_keys_loads(self):
        # Back-compat: an older payload missing lots/streets/gates/wall/landmark/
        # purpose still loads.
        d = _town(walled=True).to_dict()
        for key in ("lots", "streets", "gates", "wall", "landmark", "purpose"):
            del d[key]
        loaded = Settlement.from_dict(d)
        assert loaded.lots == [] and loaded.streets == [] and loaded.gates == []
        assert loaded.wall is None and loaded.landmark is None
        assert loaded.purpose == "general"

    def test_wall_round_trip(self):
        town = _town(walled=True, coastal=True, seed=5, population=9000)
        loaded = Settlement.from_dict(town.to_dict())
        assert loaded.to_dict()["wall"] == town.to_dict()["wall"]
        assert isinstance(loaded.wall, Wall)

    def test_ward_round_trip_preserves_tuples(self):
        w = _town().wards[0]
        loaded = Ward.from_dict(w.to_dict())
        assert loaded.polygon == w.polygon
        assert isinstance(loaded.center, tuple)


class TestRenderer:
    def test_well_formed_xml(self):
        svg = SettlementSVGRenderer().render(_town())
        assert ET.fromstring(svg).tag.endswith("svg")

    def test_dimensions(self):
        root = ET.fromstring(SettlementSVGRenderer(scale=8).render(_town(w=90, h=90)))
        assert root.attrib["width"] == "720"   # 90 * 8

    def test_has_ward_polygons(self):
        svg = SettlementSVGRenderer().render(_town())
        assert svg.count("<polygon") >= len(_town().wards)

    def test_coastal_renders_water(self):
        svg = SettlementSVGRenderer().render(_town(seed=5, coastal=True))
        assert "#2f6d8f" in svg  # _WATER colour

    def test_noncoastal_has_no_water(self):
        svg = SettlementSVGRenderer().render(_town(seed=5, coastal=False))
        assert "#2f6d8f" not in svg

    def test_degenerate_water_edge_does_not_flood(self):
        # Regression: a zero-length water_edge (e.g. from deserialized data) must
        # not paint the whole canvas with sea.
        town = _town(seed=5, coastal=True)
        town.water_edge = ((5.0, 5.0), (5.0, 5.0))  # coincident endpoints
        svg = SettlementSVGRenderer().render(town)
        assert "#2f6d8f" not in svg  # no sea polygon emitted for a degenerate edge

    def test_streets_rendered_and_toggleable(self):
        town = _town()
        assert "#e4d9bc" in SettlementSVGRenderer().render(town, show_streets=True)
        assert "#e4d9bc" not in SettlementSVGRenderer().render(town, show_streets=False)

    def test_walled_town_renders_towers(self):
        # Towers are the only <circle> the renderer emits; gatehouses are <rect>.
        walled = SettlementSVGRenderer().render(_town(walled=True))
        plain = SettlementSVGRenderer().render(_town(walled=False))
        assert "<circle" in walled
        assert "<circle" not in plain

    def test_label_modes(self):
        town = _town()
        assert "<text" in SettlementSVGRenderer().render(town, label="kind")
        assert "<text" not in SettlementSVGRenderer().render(
            town, label="none", show_title=False
        )

    def test_title_escaped(self):
        town = _town()
        town.name = "Ale & Oak"
        svg = SettlementSVGRenderer().render(town)
        assert "&amp;" in svg and "Ale & Oak" not in svg


def _near_boundary(pt, poly, eps=1e-6):
    """True if pt is within eps of any polygon edge (boundary tolerance)."""
    x, y = pt
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        # distance from pt to segment ab
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg2))
        px, py = ax + t * dx, ay + t * dy
        if (x - px) ** 2 + (y - py) ** 2 <= eps:
            return True
    return False
