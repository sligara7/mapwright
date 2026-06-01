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
    "compute_cell_polygons",
    "Marker",
    "RegionalSVGRenderer",
    "Dungeon",
    "DungeonConfig",
    "DungeonGenerator",
    "Rect",
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
