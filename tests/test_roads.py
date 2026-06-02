"""Tests for regional road generation (A* trade routes between settlements)."""

from mapwright import (
    RegionalRoadGenerator,
    RegionalTerrainGenerator,
    Road,
    SeededRNG,
    WorldMapConfig,
)


def _terrain(seed=7, w=60, h=44, **cfg):
    config = WorldMapConfig(**cfg) if cfg else None
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h, config=config)


def _land_sites(terrain, n=4):
    """A few spread-out land-cell centroids to use as settlement sites."""
    land = [c for c in terrain.cells if not c.is_water]
    picks = land[:: max(1, len(land) // n)][:n]
    return [(c.cx, c.cy) for c in picks]


class TestRoads:
    def test_connects_all_sites_minimally(self):
        t = _terrain()
        sites = _land_sites(t, 5)
        roads = RegionalRoadGenerator().generate(t, sites)
        assert len(roads) == len(sites) - 1  # MST → n-1 roads
        assert all(isinstance(r, Road) and len(r.cells) >= 2 for r in roads)

    def test_fewer_than_two_sites_yields_no_roads(self):
        t = _terrain()
        assert RegionalRoadGenerator().generate(t, []) == []
        assert RegionalRoadGenerator().generate(t, _land_sites(t, 1)) == []

    def test_road_endpoints_are_land(self):
        t = _terrain()
        roads = RegionalRoadGenerator().generate(t, _land_sites(t, 4))
        for r in roads:
            assert not t.cells[r.cells[0]].is_water
            assert not t.cells[r.cells[-1]].is_water

    def test_roads_are_connected_cell_chains(self):
        # Consecutive road cells must be graph neighbours (a real routed path).
        t = _terrain()
        roads = RegionalRoadGenerator().generate(t, _land_sites(t, 4))
        for r in roads:
            for a, b in zip(r.cells, r.cells[1:]):
                assert b in t.cells[a].neighbors

    def test_roads_prefer_land_over_sea(self):
        # A routed road should be mostly on land (sea carries a heavy penalty).
        t = _terrain()
        roads = RegionalRoadGenerator().generate(t, _land_sites(t, 5))
        total = sum(len(r.cells) for r in roads)
        sea = sum(1 for r in roads for cid in r.cells if t.cells[cid].is_water)
        assert sea <= 0.15 * total

    def test_deterministic(self):
        t = _terrain()
        sites = _land_sites(t, 5)
        a = RegionalRoadGenerator().generate(t, sites)
        b = RegionalRoadGenerator().generate(t, sites)
        assert [r.cells for r in a] == [r.cells for r in b]

    def test_road_round_trip(self):
        r = Road(cells=[3, 7, 12])
        assert Road.from_dict(r.to_dict()) == r


class TestRoadRendering:
    def test_render_with_roads_adds_path(self):
        from mapwright import RegionalSVGRenderer
        t = _terrain()
        roads = RegionalRoadGenerator().generate(t, _land_sites(t, 4))
        svg = RegionalSVGRenderer().render(t, roads=roads)
        assert "#6e4e32" in svg  # _ROAD_COLOR
        # No roads param → no road colour.
        assert "#6e4e32" not in RegionalSVGRenderer().render(t)
