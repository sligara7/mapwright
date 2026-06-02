"""Tests for render themes (palette + biome vocabulary over neutral terrain)."""

import re

import pytest

from mapwright import THEMES, RegionalTerrainGenerator, SeededRNG, Theme
from mapwright.terrain import Biome
from mapwright.themes import DEFAULT_THEME, get_theme, theme_names

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _terrain(seed=5, w=30, h=22):
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h)


class TestThemeData:
    def test_every_theme_covers_all_biomes(self):
        for name, theme in THEMES.items():
            assert set(theme.biomes) == set(Biome), f"{name} missing biomes"

    def test_all_colours_are_hex(self):
        scalar = ("ocean_bg", "coastline", "river", "road", "road_casing",
                  "region_border", "region_label", "settlement_fill",
                  "settlement_stroke", "label_fill", "label_halo")
        for theme in THEMES.values():
            for field in scalar:
                assert _HEX.match(getattr(theme, field)), f"{theme.name}.{field}"
            for col in theme.biomes.values():
                assert _HEX.match(col)

    def test_keys_match_names(self):
        for name, theme in THEMES.items():
            assert theme.name == name

    def test_default_theme_exists(self):
        assert DEFAULT_THEME in THEMES
        assert set(theme_names()) == set(THEMES)

    def test_missing_biome_raises(self):
        with pytest.raises(ValueError):
            Theme(name="bad", biomes={Biome.OCEAN: "#000000"},
                  ocean_bg="#000000", coastline="#000000", river="#000000",
                  road="#000000", road_casing="#000000", region_border="#000000",
                  region_label="#000000", settlement_fill="#000000",
                  settlement_stroke="#000000", label_fill="#000000", label_halo="#000000")


class TestVocabulary:
    def test_biome_label_override_and_fallback(self):
        neon = THEMES["neon"]
        assert neon.biome_label(Biome.OCEAN) == "Void"          # overridden
        assert neon.biome_label(Biome.PLAINS) == "Plains"        # title-cased fallback

    def test_biome_rgb_tuples(self):
        rgb = THEMES["parchment"].biome_rgb()
        assert rgb[Biome.OCEAN] == (31, 78, 107)


class TestResolve:
    def test_get_theme_by_name_and_passthrough(self):
        t = get_theme("dune")
        assert t is THEMES["dune"]
        assert get_theme(t) is t

    def test_unknown_theme_raises(self):
        with pytest.raises(KeyError):
            get_theme("does-not-exist")


class TestRendering:
    def test_default_matches_explicit_parchment(self):
        from mapwright import RegionalSVGRenderer
        terrain = _terrain()
        a = RegionalSVGRenderer(scale=8).render(terrain)
        b = RegionalSVGRenderer(scale=8, theme="parchment").render(terrain)
        assert a == b

    def test_theme_changes_output_and_paints_background(self):
        from mapwright import RegionalSVGRenderer
        terrain = _terrain()
        base = RegionalSVGRenderer(scale=8).render(terrain)
        neon = RegionalSVGRenderer(scale=8, theme="neon").render(terrain)
        assert neon != base
        assert THEMES["neon"].ocean_bg in neon          # bg colour present
        assert THEMES["parchment"].ocean_bg not in neon  # old bg gone

    def test_accepts_theme_object(self):
        from mapwright import RegionalSVGRenderer
        svg = RegionalSVGRenderer(scale=8, theme=THEMES["blueprint"]).render(_terrain())
        assert svg.startswith("<svg")
        assert THEMES["blueprint"].coastline in svg
