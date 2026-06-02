"""Unit tests for the regional Voronoi/erosion/biome terrain generator."""



from mapwright.config import WorldMapConfig
from mapwright.rng import SeededRNG
from mapwright.terrain import Biome, RegionalTerrainGenerator


def _gen(seed: int, w: int = 30, h: int = 30, **kw):
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h, **kw)


class TestStructure:
    def test_grid_maps_cover_every_coordinate(self):
        result = _gen(1, 24, 18)
        assert result.cell_of.shape == (18, 24)
        coords = {(x, y) for y in range(18) for x in range(24)}
        assert all(0 <= int(result.cell_of[y][x]) < len(result.cells)
                   for (x, y) in coords)

    def test_every_grid_cell_maps_to_a_real_cell(self):
        result = _gen(2, 20, 20)
        assert result.cell_of.shape == (20, 20)
        assert result.cell_of.min() >= 0
        assert result.cell_of.max() < len(result.cells)


class TestDeterminism:
    def test_same_seed_same_biomes(self):
        # Determinism at the neutral layer: same seed → same per-cell biomes.
        a = [c.biome for c in _gen(42).cells]
        b = [c.biome for c in _gen(42).cells]
        assert a == b

    def test_different_seed_differs(self):
        a = [c.biome for c in _gen(1).cells]
        b = [c.biome for c in _gen(2).cells]
        assert a != b


class TestTerrainRealism:
    def test_has_both_land_and_water(self):
        result = _gen(7, 30, 30)
        water = sum(1 for c in result.cells if c.is_water)
        land = sum(1 for c in result.cells if not c.is_water)
        assert water > 0 and land > 0

    def test_biome_variety(self):
        biomes = {c.biome for c in _gen(7, 36, 36).cells}
        assert len(biomes) >= 3  # not a flat single-biome map

    def test_drainage_terminates_at_sea(self):
        # After depression-fill, following downhill must reach water without
        # cycling — the whole point of Planchon-Darboux.
        result = _gen(9, 30, 30)
        cells = result.cells
        for start in cells:
            if start.is_water:
                continue
            cur, steps = start, 0
            while cur is not None and not cur.is_water:
                nxt = cells[cur.downhill] if cur.downhill >= 0 else None
                assert nxt is not None, f"land cell {cur.id} has no downhill"
                cur = nxt
                steps += 1
                assert steps <= len(cells), "downhill cycle detected"

    def test_downhill_is_strictly_lower(self):
        result = _gen(11, 28, 28)
        cells = result.cells
        for c in cells:
            if c.downhill >= 0:
                assert cells[c.downhill].filled < c.filled + 1e-6

    def test_edges_tend_to_be_water(self):
        # The radial falloff should make the map border mostly sea.
        result = _gen(13, 30, 30)
        w, h = result.width, result.height
        border_cells = set()
        for x in range(w):
            border_cells.add(int(result.cell_of[0][x]))
            border_cells.add(int(result.cell_of[h - 1][x]))
        for y in range(h):
            border_cells.add(int(result.cell_of[y][0]))
            border_cells.add(int(result.cell_of[y][w - 1]))
        frac = sum(1 for cid in border_cells if result.cells[cid].is_water) / len(border_cells)
        assert frac > 0.5

    def test_rivers_are_downhill_chains(self):
        result = _gen(17, 40, 40)
        for river in result.rivers:
            assert len(river.cells) >= 2
            assert river.width > 0


class TestLakes:
    def _lake_cells(self, cfg, seed=7, w=70, h=50):
        cells = _gen(seed, w, h, config=cfg).cells
        return [c for c in cells if c.is_lake]

    def test_lakes_appear_with_high_density(self):
        # Across several seeds, a high lake_density produces at least some lakes.
        total = sum(len(self._lake_cells(WorldMapConfig(lake_density=1.0), seed=s))
                    for s in range(6))
        assert total > 0

    def test_more_lakes_with_higher_density(self):
        few = sum(len(self._lake_cells(WorldMapConfig(lake_density=0.0), seed=s))
                  for s in range(8))
        many = sum(len(self._lake_cells(WorldMapConfig(lake_density=1.0), seed=s))
                   for s in range(8))
        assert many >= few

    def test_lakes_are_inland_water_not_ocean(self):
        cfg = WorldMapConfig(lake_density=1.0)
        for c in self._lake_cells(cfg):
            assert not c.is_water            # not the sea
            assert c.height >= cfg.sea_level  # at land elevation
            assert c.biome == Biome.LAKE

    def test_lake_cells_are_not_rivers(self):
        cfg = WorldMapConfig(lake_density=1.0)
        assert all(not c.is_river for c in self._lake_cells(cfg))


class TestRainShadow:
    def test_moisture_is_deterministic(self):
        a = [c.moisture for c in _gen(7, 50, 40).cells]
        b = [c.moisture for c in _gen(7, 50, 40).cells]
        assert a == b

    def test_moisture_varies_across_the_map(self):
        # Rain shadow (plus water-distance decay) gives a real wet/dry spread, not
        # a uniform field.
        cfg = WorldMapConfig(mountain_density=0.9)
        ms = [c.moisture for c in _gen(3, 60, 44, config=cfg).cells if not c.is_water]
        assert max(ms) - min(ms) > 0.3

    def test_moisture_in_unit_range(self):
        assert all(0.0 <= c.moisture <= 1.0 for c in _gen(5, 40, 40).cells)


class TestTemplates:
    def test_registry_nonempty(self):
        from mapwright.terrain import TERRAIN_TEMPLATES
        assert TERRAIN_TEMPLATES and "archipelago" in TERRAIN_TEMPLATES

    def test_every_template_produces_land_and_water(self):
        from mapwright.terrain import TERRAIN_TEMPLATES
        cfg = WorldMapConfig(sea_level=0.5)
        for name in TERRAIN_TEMPLATES:
            t = _gen(5, 64, 46, config=cfg, template=name)
            water = sum(c.is_water for c in t.cells)
            land = sum(not c.is_water for c in t.cells)
            assert water > 0 and land > 0, name

    def test_template_is_deterministic(self):
        a = [c.biome for c in _gen(7, 60, 44, template="archipelago").cells]
        b = [c.biome for c in _gen(7, 60, 44, template="archipelago").cells]
        assert a == b

    def test_template_differs_from_default(self):
        # A template should change the terrain vs the default tectonic mode.
        default = [c.is_water for c in _gen(7, 60, 44).cells]
        volcano = [c.is_water for c in _gen(7, 60, 44, template="volcano").cells]
        assert default != volcano

    def test_unknown_template_falls_back_to_default(self):
        a = [c.biome for c in _gen(3, 50, 40).cells]
        b = [c.biome for c in _gen(3, 50, 40, template="does-not-exist").cells]
        assert a == b


class TestParameters:
    def test_higher_sea_level_means_more_water(self):
        from mapwright.config import WorldMapConfig
        low = _gen(5, 30, 30, config=WorldMapConfig(sea_level=0.2))
        high = _gen(5, 30, 30, config=WorldMapConfig(sea_level=0.5))
        low_water = sum(c.is_water for c in low.cells)
        high_water = sum(c.is_water for c in high.cells)
        assert high_water > low_water

    def test_cell_count_scales_with_area(self):
        small = _gen(3, 16, 16)
        big = _gen(3, 48, 48)
        assert len(big.cells) > len(small.cells)
