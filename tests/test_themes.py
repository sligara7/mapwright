"""Tests for render themes (palette + biome vocabulary over neutral terrain)."""

import re

import pytest

from mapwright import (
    THEMES,
    DungeonGenerator,
    DungeonSVGRenderer,
    RegionalTerrainGenerator,
    SeededRNG,
    SettlementGenerator,
    SettlementSVGRenderer,
    Theme,
)
from mapwright.terrain import Biome
from mapwright.themes import (
    DEFAULT_THEME,
    DungeonPalette,
    SettlementPalette,
    get_theme,
    theme_names,
)

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


class TestSubPalettes:
    def test_every_theme_has_settlement_and_dungeon_palettes(self):
        for theme in THEMES.values():
            assert isinstance(theme.settlement, SettlementPalette)
            assert isinstance(theme.dungeon, DungeonPalette)

    def test_sub_palette_colours_are_hex(self):
        s_fields = ("countryside", "footprint", "water", "ward_default",
                    "ward_stroke", "building", "building_stroke", "road",
                    "road_casing", "wall", "tower_edge", "label", "label_halo")
        d_fields = ("wall_bg", "floor", "room_fill", "room_stroke", "grid_line",
                    "label", "label_halo")
        for theme in THEMES.values():
            for f in s_fields:
                assert _HEX.match(getattr(theme.settlement, f)), f"{theme.name}.settlement.{f}"
            for col in theme.settlement.wards.values():
                assert _HEX.match(col)
            for f in d_fields:
                assert _HEX.match(getattr(theme.dungeon, f)), f"{theme.name}.dungeon.{f}"

    def test_ward_fill_fallback(self):
        pal = THEMES["parchment"].settlement
        assert pal.ward_fill("market") == pal.wards["market"]
        assert pal.ward_fill("nonexistent") == pal.ward_default

    def test_settlement_palette_hashable(self):
        # frozen dataclass with a dict field must stay hashable (see Theme).
        assert len({THEMES["neon"].settlement, THEMES["dune"].settlement}) == 2

    def test_settlement_default_matches_parchment(self):
        town = SettlementGenerator(SeededRNG(7)).generate(70, 70)
        a = SettlementSVGRenderer(scale=6).render(town)
        b = SettlementSVGRenderer(scale=6, theme="parchment").render(town)
        assert a == b

    def test_settlement_theme_changes_output(self):
        town = SettlementGenerator(SeededRNG(7)).generate(70, 70)
        base = SettlementSVGRenderer(scale=6).render(town)
        neon = SettlementSVGRenderer(scale=6, theme="neon").render(town)
        assert neon != base
        assert THEMES["neon"].settlement.countryside in neon

    def test_dungeon_default_matches_parchment(self):
        d = DungeonGenerator(SeededRNG(3)).generate(40, 28)
        a = DungeonSVGRenderer(scale=10).render(d, labels=True, show_grid=True)
        b = DungeonSVGRenderer(scale=10, theme="parchment").render(
            d, labels=True, show_grid=True)
        assert a == b

    def test_dungeon_theme_changes_output(self):
        d = DungeonGenerator(SeededRNG(3)).generate(40, 28)
        base = DungeonSVGRenderer(scale=10).render(d)
        bp = DungeonSVGRenderer(scale=10, theme="blueprint").render(d)
        assert bp != base
        assert THEMES["blueprint"].dungeon.wall_bg in bp


class TestVocabulary:
    def test_biome_label_override_and_fallback(self):
        neon = THEMES["neon"]
        assert neon.biome_label(Biome.OCEAN) == "Void"          # overridden
        assert neon.biome_label(Biome.PLAINS) == "Plains"        # title-cased fallback

    def test_biome_rgb_tuples(self):
        rgb = THEMES["parchment"].biome_rgb()
        assert rgb[Biome.OCEAN] == (31, 78, 107)


class TestHashability:
    def test_theme_is_hashable_despite_dict_fields(self):
        # frozen dataclass advertises hashability; dict fields must not break it.
        assert len({THEMES["neon"], THEMES["dune"], THEMES["neon"]}) == 2
        assert hash(THEMES["parchment"]) == hash(THEMES["parchment"])


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
