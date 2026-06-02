"""Tests for the AtlasRenderer + ArtPack (symbol-stamping atlas look).

These build a tiny *synthetic* art pack on the fly (transparent PNGs in a
Nortantis-style folder layout) so no real art is committed. The whole module is
skipped when Pillow is unavailable, since the atlas extra is optional.
"""

import random
from io import BytesIO

import pytest

pytest.importorskip("PIL")  # atlas rendering needs the optional [atlas] extra
from PIL import Image  # noqa: E402

from mapwright import (  # noqa: E402
    ArtPack,
    AtlasRenderer,
    Marker,
    RegionalTerrainGenerator,
    SeededRNG,
    WorldMapConfig,
)
from mapwright.atlas_renderer import Symbol, _classify  # noqa: E402


def _png(path, color=(40, 40, 40, 255), size=(48, 64)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


@pytest.fixture
def pack_dir(tmp_path):
    """A minimal Nortantis-style pack: a couple of variants across slots."""
    _png(tmp_path / "mountains" / "sharp" / "m1.png")
    _png(tmp_path / "mountains" / "sharp" / "m2.png")
    _png(tmp_path / "mountains" / "eroded with spires" / "old1.png")
    _png(tmp_path / "hills" / "h1.png")
    _png(tmp_path / "trees" / "pine 3" / "p1.png")
    _png(tmp_path / "trees" / "deciduous" / "d1.png")
    _png(tmp_path / "cities" / "perspective european" / "castle_keep.png")
    _png(tmp_path / "cities" / "perspective european" / "village_small.png")
    _png(tmp_path / "decorations" / "compass roses" / "c1.png")
    _png(tmp_path / "decorations" / "sea monsters" / "kraken.png")
    return tmp_path


def _terrain(seed=3, w=34, h=24):
    cfg = WorldMapConfig(mountain_density=0.7)
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h, config=cfg)


class TestClassify:
    def test_mountain_age_buckets(self):
        assert _classify("mountains/sharp/m.png", "m.png") == "mountain.young"
        assert _classify("mountains/eroded with spires/x.png", "x.png") == "mountain.old"
        assert _classify("mountains/steep/y.png", "y.png") == "mountain.mid"

    def test_trees_and_hills(self):
        assert _classify("trees/pine 3/p.png", "p.png") == "tree.pine"
        assert _classify("trees/deciduous/d.png", "d.png") == "tree.deciduous"
        assert _classify("hills/h.png", "h.png") == "hill"

    def test_settlements_and_decorations(self):
        assert _classify("cities/x/castle_1.png", "castle_1.png") == "city.castle"
        assert _classify("cities/x/village_2.png", "village_2.png") == "city.village"
        assert _classify("decorations/compass roses/c.png", "c.png") == "decoration.compass"

    def test_unknown_returns_none(self):
        assert _classify("readme/notes.png", "notes.png") is None


class TestArtPackLoading:
    def test_auto_discovery_finds_slots(self, pack_dir):
        pack = ArtPack.from_directory(pack_dir)
        assert "mountain.young" in pack.slots
        assert len(pack.slots["mountain.young"]) == 2  # two variants
        assert "mountain.old" in pack.slots
        assert "hill" in pack.slots
        assert "tree.pine" in pack.slots
        assert "tree.deciduous" in pack.slots
        assert "city.castle" in pack.slots
        assert "decoration.compass" in pack.slots

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ArtPack.from_directory(tmp_path / "nope")

    def test_manifest_overrides_autodiscovery(self, tmp_path):
        _png(tmp_path / "art" / "peak.png")
        (tmp_path / "manifest.json").write_text(
            '{"name": "neon", "colors": {"water": "#001133"},'
            ' "slots": {"mountain.young": {"files": ["art/*.png"], "width": 3.0}}}'
        )
        pack = ArtPack.from_directory(tmp_path)
        assert pack.name == "neon"
        assert pack.color("water") == "#001133"
        assert pack.slots["mountain.young"][0].width == 3.0


class TestPick:
    def test_exact_slot(self, pack_dir):
        pack = ArtPack.from_directory(pack_dir)
        sym = pack.pick("mountain.young", random.Random(1))
        assert isinstance(sym, Symbol)
        assert "sharp" in sym.path

    def test_sibling_fallback(self, pack_dir):
        # mountain.mid has no exact/base slot; must fall back to a sibling.
        pack = ArtPack.from_directory(pack_dir)
        sym = pack.pick("mountain.mid", _rng())
        assert sym is not None
        assert "mountains" in sym.path

    def test_missing_returns_none(self, pack_dir):
        # "dune" has no slot and no "dune.*" sibling in this pack → None.
        pack = ArtPack.from_directory(pack_dir)
        assert pack.pick("dune", _rng()) is None


class TestRender:
    def test_render_returns_valid_png_of_expected_size(self, pack_dir):
        pack = ArtPack.from_directory(pack_dir)
        terrain = _terrain()
        png = AtlasRenderer(pack, scale=8, seed=1).render(terrain, land_age=0.2)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        im = Image.open(BytesIO(png))
        assert im.size == (terrain.width * 8, terrain.height * 8)

    def test_render_is_deterministic(self, pack_dir):
        pack = ArtPack.from_directory(pack_dir)
        terrain = _terrain()
        a = AtlasRenderer(pack, scale=8, seed=4).render(terrain, land_age=0.5)
        b = AtlasRenderer(pack, scale=8, seed=4).render(terrain, land_age=0.5)
        assert a == b

    def test_seed_changes_output(self, pack_dir):
        pack = ArtPack.from_directory(pack_dir)
        terrain = _terrain()
        a = AtlasRenderer(pack, scale=8, seed=1).render(terrain)
        b = AtlasRenderer(pack, scale=8, seed=2).render(terrain)
        assert a != b

    def test_render_with_markers_and_labels(self, pack_dir):
        pack = ArtPack.from_directory(pack_dir)
        terrain = _terrain()
        markers = [
            Marker(name="Eldmoor", x=18, y=12, kind="settlement_castle"),
            Marker(name="Brack", x=8, y=6, kind="settlement_village"),
        ]
        png = AtlasRenderer(pack, scale=8, seed=1).render(
            terrain, markers, show_labels=True)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_empty_pack_still_renders_base_map(self, tmp_path):
        # A pack with zero symbols should still paint land/sea without crashing.
        empty = ArtPack.from_directory(tmp_path)  # tmp_path is an empty dir
        png = AtlasRenderer(empty, scale=6, seed=1).render(_terrain())
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def _rng():
    return random.Random(7)
