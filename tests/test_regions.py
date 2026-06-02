"""Tests for region/faction assignment (graph-Voronoi territories over land)."""

from mapwright import (
    Region,
    RegionGenerator,
    RegionalSVGRenderer,
    RegionalTerrainGenerator,
    SeededRNG,
    WorldMapConfig,
)


def _terrain(seed=7, w=60, h=44, **cfg):
    config = WorldMapConfig(**cfg) if cfg else None
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h, config=config)


def _regions(seed=7, count=None, **cfg):
    t = _terrain(seed, **cfg)
    return t, RegionGenerator(SeededRNG(seed)).generate(t, count)


class TestRegions:
    def test_requested_count(self):
        t, regions = _regions(count=5)
        assert len(regions) == 5
        assert all(isinstance(r, Region) and r.name for r in regions)

    def test_regions_partition_reachable_land(self):
        # Every region cell is land; no land cell belongs to two regions.
        t, regions = _regions(count=6)
        seen = set()
        for r in regions:
            for cid in r.cells:
                assert not t.cells[cid].is_water
                assert cid not in seen, "cell in two regions"
                seen.add(cid)

    def test_capital_is_a_member_land_cell(self):
        t, regions = _regions(count=4)
        for r in regions:
            assert r.capital in r.cells
            assert not t.cells[r.capital].is_water

    def test_regions_are_connected(self):
        # Each region is a single connected blob over the land graph (flood fill).
        t, regions = _regions(count=5)
        member_of = {cid: r.id for r in regions for cid in r.cells}
        for r in regions:
            reached = {r.capital}
            stack = [r.capital]
            while stack:
                cur = stack.pop()
                for n in t.cells[cur].neighbors:
                    if n not in reached and member_of.get(n) == r.id:
                        reached.add(n)
                        stack.append(n)
            assert reached == set(r.cells)

    def test_auto_count_scales_with_land(self):
        _, small = _regions(seed=3, w=34, h=28)
        _, big = _regions(seed=3, w=90, h=70)
        assert len(big) >= len(small)

    def test_zero_or_negative_count_yields_no_regions(self):
        # Regression: count<=0 must mean "no regions", not silently coerce to 1.
        t = _terrain()
        assert RegionGenerator(SeededRNG(7)).generate(t, 0) == []
        assert RegionGenerator(SeededRNG(7)).generate(t, -5) == []

    def test_single_continent_is_fully_covered(self):
        # Completeness: on one connected landmass, every land cell joins a region
        # (capitals all sit on the one continent → flood reaches all of it).
        cfg = dict(continents=1, sea_level=0.28, edge_falloff=1.4)
        t, regions = _regions(seed=7, count=5, **cfg)
        land_ids = {c.id for c in t.cells if not c.is_water}
        covered = {cid for r in regions for cid in r.cells}
        # Allow a hair of slack for any stray detached cell, but essentially total.
        assert len(covered) >= 0.98 * len(land_ids)

    def test_no_land_no_regions(self):
        # An all-ocean world yields no regions.
        t = _terrain(seed=2, sea_level=0.9, edge_falloff=2.0)
        if all(c.is_water for c in t.cells):
            assert RegionGenerator(SeededRNG(2)).generate(t) == []

    def test_deterministic(self):
        _, a = _regions(seed=11, count=5)
        t = _terrain(11)
        b = RegionGenerator(SeededRNG(11)).generate(t, 5)
        assert [(r.name, r.capital, r.cells) for r in a] == \
            [(r.name, r.capital, r.cells) for r in b]

    def test_round_trip(self):
        r = Region(id=1, name="The Vale", capital=4, cells=[4, 5, 9])
        assert Region.from_dict(r.to_dict()) == r


class TestRegionRendering:
    def test_render_with_regions_draws_borders(self):
        t, regions = _regions(count=5)
        svg = RegionalSVGRenderer().render(t, regions=regions)
        assert "#78242c" in svg  # _REGION_BORDER colour
        assert "#78242c" not in RegionalSVGRenderer().render(t)
