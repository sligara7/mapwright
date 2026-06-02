"""Serialisation round-trip tests.

A serialised world/dungeon must reload to a bit-identical object — same cells,
same numpy rasters, same rivers — so saving and reloading never changes a map.
"""

import json

import numpy as np

from mapwright import (
    Dungeon,
    DungeonGenerator,
    Marker,
    RegionalSVGRenderer,
    RegionalTerrainGenerator,
    River,
    SeededRNG,
    TerrainCell,
    TerrainResult,
    WorldMapConfig,
)


def _terrain(seed=7, preset=None):
    cfg = WorldMapConfig.preset(preset) if preset else None
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(40, 30, config=cfg)


def _assert_cells_equal(a: TerrainCell, b: TerrainCell):
    assert a.id == b.id
    assert a.cx == b.cx and a.cy == b.cy
    assert a.neighbors == b.neighbors
    assert a.height == b.height
    assert a.filled == b.filled
    assert a.flux == b.flux
    assert a.downhill == b.downhill
    assert a.is_water == b.is_water
    assert a.is_lake == b.is_lake
    assert a.is_river == b.is_river
    assert a.temperature == b.temperature
    assert a.moisture == b.moisture
    assert a.biome == b.biome
    assert isinstance(b.biome, type(a.biome))


def _assert_terrain_equal(a: TerrainResult, b: TerrainResult):
    assert (a.width, a.height, a.sea_level) == (b.width, b.height, b.sea_level)
    assert len(a.cells) == len(b.cells)
    for ca, cb in zip(a.cells, b.cells):
        _assert_cells_equal(ca, cb)
    assert np.array_equal(a.cell_of, b.cell_of)
    assert a.cell_of.dtype == b.cell_of.dtype
    assert len(a.rivers) == len(b.rivers)
    for ra, rb in zip(a.rivers, b.rivers):
        assert ra.cells == rb.cells and ra.width == rb.width


class TestTerrainRoundTrip:
    def test_dict_round_trip(self):
        t = _terrain()
        _assert_terrain_equal(t, TerrainResult.from_dict(t.to_dict()))

    def test_json_round_trip(self):
        t = _terrain()
        _assert_terrain_equal(t, TerrainResult.from_json(t.to_json()))

    def test_to_dict_is_json_safe(self):
        # No numpy/enum leaks: the dict must serialise with the stdlib encoder.
        json.dumps(_terrain().to_dict())

    def test_schema_tag_present(self):
        assert _terrain().to_dict()["schema"] == "mapwright/terrain@2"

    def test_reload_renders_identically(self):
        # The real guarantee: a reloaded world produces byte-identical SVG.
        t = _terrain(preset="archipelago")
        markers = [Marker("Eldmoor", 20, 15, "settlement_city")]
        renderer = RegionalSVGRenderer()
        before = renderer.render(t, markers)
        after = renderer.render(TerrainResult.from_json(t.to_json()), markers)
        assert before == after

    def test_biome_reconstructs_as_enum(self):
        t = _terrain()
        loaded = TerrainResult.from_dict(t.to_dict())
        assert all(isinstance(c.biome, type(t.cells[0].biome)) for c in loaded.cells)

    def test_unknown_keys_ignored_on_load(self):
        d = _terrain().to_dict()
        d["future_field"] = 123
        d["cells"][0]["future_field"] = "x"
        TerrainResult.from_dict(d)  # must not raise


class TestRiverAndCellRoundTrip:
    def test_river_round_trip(self):
        r = River(cells=[1, 2, 3], width=2.5)
        assert River.from_dict(r.to_dict()) == r

    def test_cell_round_trip(self):
        c = _terrain().cells[0]
        _assert_cells_equal(c, TerrainCell.from_dict(c.to_dict()))


class TestDungeonRoundTrip:
    def _dungeon(self, seed=3):
        return DungeonGenerator(SeededRNG(seed)).generate(48, 32)

    def test_dict_round_trip(self):
        d = self._dungeon()
        loaded = Dungeon.from_dict(d.to_dict())
        assert (loaded.width, loaded.height) == (d.width, d.height)
        assert loaded.rooms == d.rooms
        assert loaded.corridors == d.corridors
        assert loaded.edges == d.edges
        assert np.array_equal(loaded.grid, d.grid)
        assert loaded.grid.dtype == d.grid.dtype

    def test_json_round_trip_preserves_ascii(self):
        d = self._dungeon()
        loaded = Dungeon.from_json(d.to_json())
        assert loaded.ascii() == d.ascii()

    def test_to_dict_is_json_safe(self):
        json.dumps(self._dungeon().to_dict())

    def test_schema_tag_present(self):
        assert self._dungeon().to_dict()["schema"] == "mapwright/dungeon@1"

    def test_corridors_are_tuples(self):
        loaded = Dungeon.from_dict(self._dungeon().to_dict())
        assert all(isinstance(c, tuple) and len(c) == 2 for c in loaded.corridors)


class TestMarkerRoundTrip:
    def test_round_trip(self):
        m = Marker("Eldmoor", 12.5, 7.0, "settlement_city")
        assert Marker.from_dict(m.to_dict()) == m
        assert Marker.from_json(m.to_json()) == m

    def test_unknown_keys_ignored(self):
        assert Marker.from_dict({"name": "X", "x": 1, "y": 2, "extra": 9}).name == "X"
