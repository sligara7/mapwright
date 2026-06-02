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
    "SETTLEMENT_PRESETS",
}


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
