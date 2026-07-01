"""Unit tests for the numpy-only tectonic-world engine (_tectonics.py)."""

import numpy as np
import pytest

from mapwright import _tectonics as T
from mapwright.rng import SeededRNG


def _sphere_points(n: int, seed: int = 0) -> np.ndarray:
    gen = np.random.default_rng(seed)
    p = gen.normal(size=(n, 3))
    return p / np.linalg.norm(p, axis=1, keepdims=True)


class TestRadiusNeighbours:
    """The numpy spatial hash must match an exact O(n^2) radius search."""

    @pytest.mark.parametrize("seed", [0, 1, 2])
    @pytest.mark.parametrize("r", [0.2, 0.35, 0.6])
    def test_matches_brute_force(self, seed, r):
        p = _sphere_points(250, seed)
        s, d, _ = T._radius_neighbours(p, r)
        got = set(zip(s.tolist(), d.tolist()))
        d2 = ((p[:, None, :] - p[None, :, :]) ** 2).sum(-1)
        bi, bj = np.nonzero((d2 < r * r) & (d2 > 0))
        assert got == set(zip(bi.tolist(), bj.tolist()))

    def test_excludes_self(self):
        p = _sphere_points(100)
        s, d, _ = T._radius_neighbours(p, 0.5)
        assert not np.any(s == d)

    def test_degenerate_small_input(self):
        s, d, q = T._radius_neighbours(np.zeros((1, 3)), 0.5)
        assert len(s) == len(d) == len(q) == 0


class TestNearestForeign:
    def test_picks_nearest_across_plates(self):
        # Nodes 0,1 are plate 0; node 2 is plate 1. Both 0 and 1 have exactly one
        # foreign neighbour (node 2); the (0,1) edge is same-plate, so ignored.
        src = np.array([0, 0, 1])
        dst = np.array([1, 2, 2])
        d2 = np.array([0.04, 0.01, 0.09])
        plate = np.array([0, 0, 1])
        gi, gj, nd = T._nearest_foreign(src, dst, d2, plate)
        assert gi.tolist() == [0, 1]
        assert gj.tolist() == [2, 2]
        assert np.allclose(nd, [0.1, 0.3])


class TestSimulateWorld:
    def _world(self, seed=7, **kw):
        params = dict(res=48, steps=20, nlon=96, nlat=48)
        params.update(kw)
        return T.simulate_tectonic_world(SeededRNG(seed), **params)

    def test_shape_and_finite(self):
        a = self._world()
        assert a.shape == (48, 96)
        assert np.isfinite(a).all()

    def test_deterministic(self):
        assert np.array_equal(self._world(), self._world())

    def test_has_land_and_sea(self):
        a = self._world()
        assert (a > 0).any() and (a < 0).any()

    def test_unknown_world_raises(self):
        with pytest.raises(ValueError):
            self._world(world="atlantis")

    def test_land_override_raises_land_fraction(self):
        dry = self._world(land=0.6)
        wet = self._world(land=0.15)
        assert np.mean(dry > 0) > np.mean(wet > 0)

    def test_high_ground_forms_belts_not_speckle(self):
        # Regression guard for the old fragmentation: high cells should mostly
        # touch other high cells (coherent belts), not sit isolated.
        a = self._world(res=64, steps=30, nlon=128, nlat=64)
        hi = a > np.quantile(a, 0.90)
        neigh = (np.roll(hi, 1, 1) | np.roll(hi, -1, 1)
                 | np.roll(hi, 1, 0) | np.roll(hi, -1, 0))
        clustered = (hi & neigh).sum() / max(int(hi.sum()), 1)
        assert clustered > 0.6

    def test_more_steps_changes_morphology(self):
        # land_age -> steps: drifting the plates further must alter the world.
        young = self._world(steps=8)
        old = self._world(steps=60)
        assert not np.array_equal(young, old)


class TestTerrainIntegration:
    """`generate(tectonic=...)` wiring into the full terrain pipeline."""

    def _gen(self, seed=7, tectonic=True, **cfgkw):
        from mapwright import RegionalTerrainGenerator, SeededRNG, WorldMapConfig
        cfg = WorldMapConfig(**{"continents": 6, "sea_level": 0.6, **cfgkw})
        return RegionalTerrainGenerator(SeededRNG(seed)).generate(
            72, 44, cfg, tectonic=tectonic)

    def test_end_to_end_produces_a_world(self):
        t = self._gen()
        assert any(not c.is_water for c in t.cells)   # land
        assert any(c.is_water for c in t.cells)        # sea
        assert len({c.biome for c in t.cells}) >= 3    # varied biomes
        assert len(t.rivers) >= 1

    def test_deterministic(self):
        a = self._gen()
        b = self._gen()
        assert [c.height for c in a.cells] == [c.height for c in b.cells]

    def test_world_style_string(self):
        t = self._gen(tectonic="archipelago")
        assert any(not c.is_water for c in t.cells)

    def test_unknown_style_raises(self):
        import pytest
        with pytest.raises(ValueError):
            self._gen(tectonic="atlantis")

    def test_land_age_changes_the_world(self):
        # land_age drives sim steps, so young vs old worlds differ.
        young = self._gen(land_age=0.1)
        old = self._gen(land_age=0.9)
        assert [c.height for c in young.cells] != [c.height for c in old.cells]
