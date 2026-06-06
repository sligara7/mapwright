"""API contract tests — the public surface other code may rely on.

These pin the public API so a breaking change fails loudly (and CI catches it).
If a change here is intentional, update the expected sets *and* the version /
CHANGELOG per semver.
"""

import dataclasses
import inspect

import mapwright
from mapwright import WorldMapConfig
from mapwright.config import _SPEC


# The frozen public surface (mapwright.__all__). Adding is a minor bump;
# removing/renaming is a breaking (major, pre-1.0: minor) change.
EXPECTED_PUBLIC = {
    "SeededRNG",
    "WorldMapConfig",
    "PRESETS",
    "CellSummary",
    "environment_affordances",
    "summarize_cells",
    "NameGenerator",
    "MarkovNameGenerator",
    "NAMEBASES",
    "Biome",
    "River",
    "TerrainCell",
    "TerrainResult",
    "RegionalTerrainGenerator",
    "TERRAIN_TEMPLATES",
    "compute_cell_polygons",
    "Marker",
    "RegionalSVGRenderer",
    "Theme",
    "THEMES",
    "ArtPack",
    "AtlasRenderer",
    "Road",
    "RegionalRoadGenerator",
    "Region",
    "RegionGenerator",
    "Dungeon",
    "DungeonConfig",
    "DungeonGenerator",
    "DungeonSVGRenderer",
    "Rect",
    "Settlement",
    "SettlementConfig",
    "SettlementGenerator",
    "SettlementSVGRenderer",
    "Ward",
    "Lot",
    "Street",
    "Wall",
    "Landmark",
    "TerrainField",
    "world_terrain_field",
    "SETTLEMENT_PRESETS",
}


# The frozen field layout of every exported dataclass. Field NAMES *and ORDER*
# are part of the public contract: positional construction, attribute access,
# and the keys emitted by ``to_dict`` all depend on them. Appending a trailing
# optional field is a minor bump; renaming/removing/reordering is breaking.
EXPECTED_FIELDS = {
    "WorldMapConfig": ("sea_level", "continents", "continent_spread",
                       "edge_falloff", "mountain_density", "roughness",
                       "land_age", "temperature", "moisture", "river_density",
                       "lake_density", "polar_cold"),
    "CellSummary": ("dominant_biome", "temperature", "moisture", "mean_height",
                    "has_river", "has_lake", "water_fraction", "cell_count",
                    "affordances"),
    "TerrainCell": ("id", "cx", "cy", "neighbors", "height", "filled", "flux",
                    "downhill", "is_water", "is_lake", "is_river", "temperature",
                    "moisture", "biome"),
    "River": ("cells", "width"),
    "TerrainResult": ("width", "height", "cells", "cell_of", "rivers",
                      "sea_level"),
    "Marker": ("name", "x", "y", "kind"),
    "Theme": ("name", "biomes", "ocean_bg", "coastline", "river", "road",
              "road_casing", "region_border", "region_label", "settlement_fill",
              "settlement_stroke", "label_fill", "label_halo", "biome_names",
              "settlement", "dungeon"),
    "ArtPack": ("slots", "colors", "name"),
    "Road": ("cells",),
    "Region": ("id", "name", "capital", "cells"),
    "Dungeon": ("width", "height", "rooms", "corridors", "grid", "edges"),
    "DungeonConfig": ("min_leaf", "room_min", "room_padding", "split_jitter",
                      "extra_corridor_chance"),
    "Rect": ("x", "y", "w", "h"),
    "Settlement": ("width", "height", "name", "footprint", "wards", "lots",
                   "streets", "gates", "wall", "landmark", "walled", "coastal",
                   "purpose", "water_edge"),
    "SettlementConfig": ("population", "irregularity", "lot_size", "wealth",
                         "era", "layout", "purpose", "walled", "coastal"),
    "Ward": ("id", "polygon", "center", "name", "kind"),
    "Lot": ("id", "polygon", "ward"),
    "Street": ("path", "kind"),
    "Wall": ("ring", "closed", "gates"),
    "Landmark": ("ward", "kind", "center", "name"),
}

# Serialisable types whose ``to_dict`` emits an extra ``"schema"`` version tag
# on top of their field keys (the three top-level documents).
SCHEMA_TAGGED = {"TerrainResult", "Dungeon", "Settlement"}


class TestPublicSurface:
    def test_all_matches_contract(self):
        assert set(mapwright.__all__) == EXPECTED_PUBLIC

    def test_everything_in_all_is_importable(self):
        for name in mapwright.__all__:
            assert hasattr(mapwright, name), f"{name} missing from package"

    def test_version_is_present(self):
        assert isinstance(mapwright.__version__, str)
        assert mapwright.__version__.count(".") >= 2  # semver-ish

    def test_version_matches_package_metadata(self):
        # __version__ must match the installed (pyproject) version, so a missed
        # bump can't ship a mislabelled wheel. Skips when run from source.
        import importlib.metadata as md

        try:
            installed = md.version("mapwright")
        except md.PackageNotFoundError:
            import pytest

            pytest.skip("mapwright not installed; running from source tree")
        assert installed == mapwright.__version__


class TestKeySignatures:
    def test_generate_signature(self):
        params = inspect.signature(
            mapwright.RegionalTerrainGenerator.generate
        ).parameters
        assert ["self", "width", "height", "config"] == list(params)[:4]
        assert params["config"].default is None

    def test_svg_render_signature(self):
        params = inspect.signature(mapwright.RegionalSVGRenderer.render).parameters
        assert ["self", "terrain", "markers"] == list(params)[:3]

    def test_marker_fields(self):
        fields = {f.name for f in dataclasses.fields(mapwright.Marker)}
        assert {"name", "x", "y", "kind"} <= fields


class TestSerialisationContract:
    """The (de)serialisation surface is part of the public contract."""

    def test_roundtrip_types_have_dict_methods(self):
        for cls in (
            mapwright.TerrainResult,
            mapwright.Dungeon,
            mapwright.Marker,
            mapwright.TerrainCell,
            mapwright.River,
            mapwright.Rect,
            mapwright.Road,
            mapwright.Region,
            mapwright.Settlement,
            mapwright.Ward,
            mapwright.Lot,
            mapwright.Street,
            mapwright.Wall,
            mapwright.Landmark,
            mapwright.SettlementConfig,
        ):
            assert hasattr(cls, "to_dict") and callable(cls.to_dict)
            assert hasattr(cls, "from_dict") and callable(cls.from_dict)

    def test_top_level_types_have_json_methods(self):
        for cls in (mapwright.TerrainResult, mapwright.Dungeon, mapwright.Marker,
                    mapwright.Settlement):
            assert hasattr(cls, "to_json") and callable(cls.to_json)
            assert hasattr(cls, "from_json") and callable(cls.from_json)


class TestConfigContract:
    def test_spec_covers_every_field_exactly(self):
        spec_names = {name for name, *_ in _SPEC}
        field_names = {f.name for f in dataclasses.fields(WorldMapConfig)}
        assert spec_names == field_names

    def test_json_schema_shape(self):
        schema = WorldMapConfig.json_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        props = schema["properties"]
        assert set(props) == {f.name for f in dataclasses.fields(WorldMapConfig)}

    def test_json_schema_bounds_and_defaults_match(self):
        schema = WorldMapConfig.json_schema()
        defaults = WorldMapConfig()
        for name, typ, lo, hi, _desc in _SPEC:
            p = schema["properties"][name]
            assert p["type"] == ("integer" if typ is int else "number")
            assert p["minimum"] == lo and p["maximum"] == hi
            assert p["default"] == getattr(defaults, name)

    def test_schema_bounds_are_actually_enforced(self):
        # A payload that violates the schema bounds is clamped into them.
        for name, _typ, lo, hi, _desc in _SPEC:
            below = WorldMapConfig.from_dict({name: lo - 100})
            above = WorldMapConfig.from_dict({name: hi + 100})
            assert getattr(below, name) >= lo
            assert getattr(above, name) <= hi

    def test_presets_are_valid_against_schema(self):
        # Every preset must only use known keys within range (from_dict clamps,
        # but presets should already be in-bounds by construction).
        for name in WorldMapConfig.preset_names():
            cfg = WorldMapConfig.preset(name)
            for fname, _typ, lo, hi, _desc in _SPEC:
                assert lo <= getattr(cfg, fname) <= hi


class TestDataclassLayout:
    """Field names + order of every exported dataclass are frozen (see
    ``EXPECTED_FIELDS``). This is what consumers rely on for positional
    construction, attribute access, and serialisation key stability."""

    def test_every_exported_dataclass_is_pinned(self):
        for name in mapwright.__all__:
            obj = getattr(mapwright, name)
            if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
                continue
            assert name in EXPECTED_FIELDS, (
                f"new exported dataclass {name!r} — add it to EXPECTED_FIELDS "
                f"(and bump the version / note it in the CHANGELOG)"
            )
            actual = tuple(f.name for f in dataclasses.fields(obj))
            assert actual == EXPECTED_FIELDS[name], (
                f"{name} field layout changed: {actual} != {EXPECTED_FIELDS[name]}"
            )

    def test_no_stale_entries_in_expected_fields(self):
        # Every pinned name must still be an exported dataclass.
        for name in EXPECTED_FIELDS:
            obj = getattr(mapwright, name, None)
            assert obj is not None and dataclasses.is_dataclass(obj), (
                f"{name} is pinned in EXPECTED_FIELDS but no longer an exported dataclass"
            )


class TestToDictSchema:
    """``to_dict()`` must emit exactly the pinned field keys (plus a ``schema``
    tag on the top-level documents) — so a consumer persisting JSON is protected
    against a silent key rename/drop that leaves the dataclass field untouched."""

    def _instances(self):
        from mapwright import (
            SeededRNG, RegionalTerrainGenerator, DungeonGenerator, DungeonConfig,
            SettlementGenerator, SettlementConfig, Marker, River, Road, Region,
            Landmark,
        )
        terrain = RegionalTerrainGenerator(SeededRNG(7)).generate(60, 45)
        dungeon = DungeonGenerator(SeededRNG(7)).generate(48, 40, DungeonConfig())
        town = SettlementGenerator(SeededRNG(3)).generate(
            900, 700, SettlementConfig(population=12000, walled=True)
        )
        assert town.wards and town.lots and town.streets and town.wall, (
            "test fixture must produce a town with wards/lots/streets/wall"
        )
        return {
            "TerrainResult": terrain,
            "TerrainCell": terrain.cells[0],
            "River": River([0, 1], 1.0),
            "Marker": Marker(name="X", x=1.0, y=2.0, kind="city"),
            "Dungeon": dungeon,
            "Rect": dungeon.rooms[0],
            "Road": Road([0, 1]),
            "Region": Region(id=0, name="R", capital=5, cells=[1, 2, 3]),
            "Settlement": town,
            "Ward": town.wards[0],
            "Lot": town.lots[0],
            "Street": town.streets[0],
            "Wall": town.wall,
            "Landmark": Landmark(ward=0, kind="temple", center=(1.0, 2.0), name="Shrine"),
            "SettlementConfig": SettlementConfig(),
        }

    def test_to_dict_keys_match_contract(self):
        for name, inst in self._instances().items():
            expected = set(EXPECTED_FIELDS[name])
            if name in SCHEMA_TAGGED:
                expected |= {"schema"}
            assert set(inst.to_dict()) == expected, (
                f"{name}.to_dict() keys drifted: {set(inst.to_dict())} != {expected}"
            )
