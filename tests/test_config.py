"""Tests for WorldMapConfig and its effect on generated worlds."""

import statistics

import pytest

from mapwright import (
    Biome,
    RegionalTerrainGenerator,
    SeededRNG,
    WorldMapConfig,
)
from mapwright.config import PRESETS


def _world(cfg=None, seed=7, w=44, h=30):
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h, config=cfg)


def _land(world):
    return [c for c in world.cells if not c.is_water]


def _water_fraction(world):
    return sum(c.is_water for c in world.cells) / len(world.cells)


def _mean(values):
    return statistics.fmean(values)


class TestConfigObject:
    def test_defaults_are_balanced(self):
        cfg = WorldMapConfig()
        assert cfg.continents == 1 and cfg.sea_level == 0.32

    def test_from_dict_ignores_unknown_and_is_partial(self):
        cfg = WorldMapConfig.from_dict({"sea_level": 0.6, "bogus": 99})
        assert cfg.sea_level == 0.6 and cfg.continents == 1

    def test_from_dict_clamps_out_of_range(self):
        # An LLM emitting junk must not produce a broken world.
        cfg = WorldMapConfig.from_dict({"temperature": 5, "continents": -3,
                                        "sea_level": 2.0})
        assert -1.0 <= cfg.temperature <= 1.0
        assert cfg.continents >= 1
        assert 0.0 < cfg.sea_level <= 0.9

    def test_round_trip_dict(self):
        cfg = WorldMapConfig(continents=4, temperature=-0.3)
        assert WorldMapConfig.from_dict(cfg.to_dict()) == cfg

    def test_presets_resolve(self):
        assert "desert" in WorldMapConfig.preset_names()
        assert isinstance(WorldMapConfig.preset("archipelago"), WorldMapConfig)
        with pytest.raises(KeyError):
            WorldMapConfig.preset("atlantis")

    def test_all_presets_generate(self):
        for name in PRESETS:
            world = _world(WorldMapConfig.preset(name))
            assert _land(world) or _water_fraction(world) == 1.0  # produces *a* world


class TestDeterminism:
    def test_same_seed_and_config_reproduce(self):
        cfg = WorldMapConfig.preset("desert")
        a = [c.biome for c in _world(cfg).cells]
        b = [c.biome for c in _world(cfg).cells]
        assert a == b


class TestClimateBias:
    def test_desert_is_hotter_and_drier_than_default(self):
        default = _world(WorldMapConfig())
        desert = _world(WorldMapConfig.preset("desert"))
        assert _mean([c.temperature for c in _land(desert)]) > \
            _mean([c.temperature for c in _land(default)])
        assert _mean([c.moisture for c in _land(desert)]) < \
            _mean([c.moisture for c in _land(default)])

    def test_arctic_is_colder_than_default(self):
        default = _world(WorldMapConfig())
        arctic = _world(WorldMapConfig.preset("arctic"))
        assert _mean([c.temperature for c in _land(arctic)]) < \
            _mean([c.temperature for c in _land(default)])

    def test_temperature_bias_is_monotonic(self):
        cold = _mean([c.temperature for c in _land(_world(WorldMapConfig(temperature=-0.8)))])
        hot = _mean([c.temperature for c in _land(_world(WorldMapConfig(temperature=0.8)))])
        assert hot > cold

    def test_desert_world_has_more_desert_than_default(self):
        default = [c.biome for c in _land(_world(WorldMapConfig()))]
        desert = [c.biome for c in _land(_world(WorldMapConfig.preset("desert")))]
        assert desert.count(Biome.DESERT) > default.count(Biome.DESERT)
        # ...and no frozen biomes in a scorching world.
        assert Biome.SNOW not in desert and Biome.TUNDRA not in desert


class TestTopology:
    def test_archipelago_has_more_water_than_single_continent(self):
        single = _water_fraction(_world(WorldMapConfig(continents=1)))
        arch = _water_fraction(_world(WorldMapConfig.preset("archipelago")))
        assert arch > single

    def test_more_continents_makes_more_landmasses(self):
        # A crude landmass count via connected-component flood fill on land cells.
        def landmasses(world):
            land = {c.id for c in world.cells if not c.is_water}
            seen, count = set(), 0
            for start in land:
                if start in seen:
                    continue
                count += 1
                stack = [start]
                while stack:
                    cid = stack.pop()
                    if cid in seen:
                        continue
                    seen.add(cid)
                    stack.extend(n for n in world.cells[cid].neighbors
                                 if n in land and n not in seen)
            return count
        # Archipelago separation is seed-dependent, so compare totals over a few
        # seeds: many continents + high sea level fragments the land in aggregate.
        arch = WorldMapConfig(continents=8, sea_level=0.6, continent_spread=0.9)
        single = sum(landmasses(_world(WorldMapConfig(continents=1), seed=s, w=72, h=52))
                     for s in range(6))
        many = sum(landmasses(_world(arch, seed=s, w=72, h=52)) for s in range(6))
        assert many > single


class TestHydrology:
    def test_higher_river_density_traces_more_rivers(self):
        # Aggregate over seeds so the comparison isn't hostage to one map.
        few = sum(len(_world(WorldMapConfig(river_density=0.05), seed=s, w=60, h=44).rivers)
                  for s in range(8))
        many = sum(len(_world(WorldMapConfig(river_density=0.95), seed=s, w=60, h=44).rivers)
                   for s in range(8))
        assert many > few
