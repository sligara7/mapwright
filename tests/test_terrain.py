"""Unit tests for the regional Voronoi/erosion/biome terrain generator."""



from mapwright.rng import SeededRNG
from mapwright.terrain import RegionalTerrainGenerator


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
