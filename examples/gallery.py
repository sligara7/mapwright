#!/usr/bin/env python3
"""Render the gallery shown in the README.

Generates one regional map per :data:`mapwright.PRESETS` entry plus a sample
dungeon, writing SVGs to ``docs/gallery/``. Deterministic (fixed seeds), so
re-running produces a stable diff.

If ``cairosvg`` is installed it also writes PNGs (the README embeds those, since
PyPI's renderer blocks SVG and relative paths — PNGs referenced by absolute raw
URL show on both GitHub and the PyPI project page). ``cairosvg`` is *not* a
project dependency; install it ad-hoc to regenerate the raster thumbnails::

    pip install cairosvg

Usage::

    python examples/gallery.py            # writes docs/gallery/*.svg (+ .png if able)
    python examples/gallery.py --out DIR  # custom output directory
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mapwright import (
    ArtPack,
    AtlasRenderer,
    DungeonGenerator,
    DungeonSVGRenderer,
    Marker,
    NameGenerator,
    RegionalRoadGenerator,
    RegionalSVGRenderer,
    RegionalTerrainGenerator,
    RegionGenerator,
    SeededRNG,
    SettlementConfig,
    SettlementGenerator,
    SettlementSVGRenderer,
    WorldMapConfig,
)

# Map size / scale tuned for a compact-but-legible gallery thumbnail.
MAP_W, MAP_H, MAP_SCALE = 64, 44, 9.0
DUNGEON_W, DUNGEON_H, DUNGEON_SCALE = 52, 34, 12.0
TOWN_W, TOWN_H, TOWN_SCALE = 90, 90, 7.0
PNG_WIDTH = 480  # raster thumbnail width when cairosvg is available


def _write_png(svg: str, path: Path) -> bool:
    """Rasterise ``svg`` to ``path`` if cairosvg is importable; else skip."""
    try:
        import cairosvg
    except ImportError:
        return False
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(path), output_width=PNG_WIDTH)
    return True


def render_preset(name: str, seed: int) -> str:
    """Render one named preset to a clean terrain SVG (no markers, for a thumbnail)."""
    cfg = WorldMapConfig.preset(name)
    terrain = RegionalTerrainGenerator(SeededRNG(seed)).generate(MAP_W, MAP_H, config=cfg)
    return RegionalSVGRenderer(scale=MAP_SCALE).render(terrain)


def render_age(land_age: float, seed: int) -> str:
    """Same continent, different geological age (young jagged vs old worn)."""
    cfg = WorldMapConfig(land_age=land_age, mountain_density=0.7)
    t = RegionalTerrainGenerator(SeededRNG(seed)).generate(MAP_W, MAP_H, config=cfg)
    return RegionalSVGRenderer(scale=MAP_SCALE).render(t)


# A coarse 8×8 "painted" land/elevation mask (0 = sea … 1 = high) — the kind of
# hint a host or LLM hands mapwright to art-direct the continent's macro shape.
_HINT_MASK = [
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.2, 0.5, 0.6, 0.5, 0.2, 0.0, 0.0],
    [0.0, 0.5, 0.9, 0.7, 0.4, 0.3, 0.2, 0.0],
    [0.0, 0.6, 0.8, 0.3, 0.0, 0.0, 0.3, 0.0],
    [0.0, 0.5, 0.6, 0.2, 0.0, 0.0, 0.4, 0.0],
    [0.0, 0.3, 0.7, 0.6, 0.5, 0.6, 0.7, 0.0],
    [0.0, 0.0, 0.3, 0.6, 0.8, 0.6, 0.3, 0.0],
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
]


def render_hint(seed: int = 11) -> str:
    """A continent whose macro shape is art-directed by an `elevation_hint`
    (a coarse painted mask); mapwright fills in coasts, erosion, rivers, climate."""
    cfg = WorldMapConfig(sea_level=0.42, edge_falloff=0.3, mountain_density=0.6)
    t = RegionalTerrainGenerator(SeededRNG(seed)).generate(
        MAP_W, MAP_H, config=cfg, elevation_hint=_HINT_MASK)
    return RegionalSVGRenderer(scale=MAP_SCALE).render(t)


def render_template(template: str, sea_level: float, seed: int) -> str:
    cfg = WorldMapConfig(sea_level=sea_level)
    t = RegionalTerrainGenerator(SeededRNG(seed)).generate(MAP_W, MAP_H, config=cfg, template=template)
    return RegionalSVGRenderer(scale=MAP_SCALE).render(t)


def render_dungeon(seed: int = 3, theme: str = "parchment") -> str:
    dungeon = DungeonGenerator(SeededRNG(seed)).generate(DUNGEON_W, DUNGEON_H)
    return DungeonSVGRenderer(scale=DUNGEON_SCALE, theme=theme).render(
        dungeon, labels=True)


def render_settlement(preset: str | None, seed: int, theme: str = "parchment") -> str:
    cfg = SettlementConfig.preset(preset) if preset else None
    town = SettlementGenerator(SeededRNG(seed)).generate(TOWN_W, TOWN_H, cfg)
    return SettlementSVGRenderer(scale=TOWN_SCALE, theme=theme).render(town)


def render_roads(seed: int = 7) -> str:
    """A continent with a handful of named settlements linked by trade routes."""
    rng = SeededRNG(seed)
    terrain = RegionalTerrainGenerator(rng).generate(MAP_W, MAP_H)
    namer = NameGenerator(rng.derive("names"))
    land = [c for c in terrain.cells if not c.is_water]
    picks = land[:: max(1, len(land) // 7)][:7]
    kinds = ["settlement_city", "settlement_town", "settlement_village"]
    markers = [Marker(namer.settlement(), c.cx, c.cy, kinds[i % len(kinds)])
               for i, c in enumerate(picks)]
    roads = RegionalRoadGenerator().generate(terrain, [(m.x, m.y) for m in markers])
    return RegionalSVGRenderer(scale=MAP_SCALE).render(terrain, markers, roads=roads)


def render_regions(seed: int = 4) -> str:
    """A continent partitioned into named factions/territories."""
    rng = SeededRNG(seed)
    terrain = RegionalTerrainGenerator(rng).generate(MAP_W, MAP_H)
    regions = RegionGenerator(rng).generate(terrain, culture="nordic")
    return RegionalSVGRenderer(scale=MAP_SCALE).render(terrain, regions=regions)


def render_themed(theme: str, seed: int = 7) -> str:
    """The *same* continent (settlements + roads) under a render theme — shows
    that a palette/vocabulary swap restyles existing data with no regeneration."""
    rng = SeededRNG(seed)
    terrain = RegionalTerrainGenerator(rng).generate(MAP_W, MAP_H)
    namer = NameGenerator(rng.derive("names"))
    land = [c for c in terrain.cells if not c.is_water]
    picks = land[:: max(1, len(land) // 6)][:6]
    kinds = ["settlement_city", "settlement_town", "settlement_village"]
    markers = [Marker(namer.settlement(), c.cx, c.cy, kinds[i % len(kinds)])
               for i, c in enumerate(picks)]
    roads = RegionalRoadGenerator().generate(terrain, [(m.x, m.y) for m in markers])
    return RegionalSVGRenderer(scale=MAP_SCALE, theme=theme).render(
        terrain, markers, roads=roads)


# The atlas sample pack (model-generated via storyflow's media_service — see
# scripts/gen_mapwright_pack.py there) lives next to this gallery. It's the one
# bundled art in the repo, used only to showcase AtlasRenderer; it is not shipped
# in the wheel (which packages src/mapwright only).
ATLAS_PACK_DIR = Path(__file__).resolve().parent.parent / "docs" / "gallery" / "atlas_pack"
ATLAS_W, ATLAS_H, ATLAS_SCALE = 80, 56, 11.0


def render_atlas(seed: int = 5) -> bytes | None:
    """Render a hand-drawn AtlasRenderer PNG from the bundled sample pack.

    Returns PNG bytes, or ``None`` if the pack is missing or Pillow is absent
    (the atlas thumbnail is then simply skipped)."""
    if not ATLAS_PACK_DIR.is_dir():
        return None
    try:
        pack = ArtPack.from_directory(ATLAS_PACK_DIR)
    except Exception:
        return None
    cfg = WorldMapConfig(mountain_density=0.6, sea_level=0.42)
    terrain = RegionalTerrainGenerator(SeededRNG(seed)).generate(
        ATLAS_W, ATLAS_H, config=cfg, template="peninsula")
    markers = [
        Marker("Eldmoor", 40, 28, kind="settlement_castle"),
        Marker("Port Vael", 18, 38, kind="settlement_town"),
        Marker("Brackwater", 60, 44, kind="settlement_village"),
    ]
    try:
        return AtlasRenderer(pack, scale=ATLAS_SCALE, seed=seed).render(
            terrain, markers, land_age=0.35)
    except ImportError:
        return None  # Pillow not installed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="docs/gallery", help="output directory (default: docs/gallery)"
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    wrote_png = False

    def emit(name: str, svg: str) -> None:
        nonlocal wrote_png
        (out / f"{name}.svg").write_text(svg)
        if _write_png(svg, out / f"{name}.png"):
            wrote_png = True
        print(f"wrote {out / f'{name}.svg'}")

    # A distinct seed per preset so the gallery shows variety, not the same shape.
    for i, name in enumerate(WorldMapConfig.preset_names(), start=1):
        emit(name, render_preset(name, seed=100 + i))

    emit("dungeon", render_dungeon())
    emit("town", render_settlement(None, seed=7))
    emit("port", render_settlement("port", seed=5))
    emit("citadel", render_settlement("citadel", seed=3))
    emit("shantytown", render_settlement("shantytown", seed=5))
    emit("metropolis", render_settlement("metropolis", seed=5))
    emit("roads", render_roads(seed=7))
    emit("regions", render_regions(seed=4))
    emit("template-isthmus", render_template("isthmus", 0.5, seed=5))
    emit("template-atoll", render_template("atoll", 0.55, seed=8))
    emit("age-young", render_age(0.0, seed=103))
    emit("age-old", render_age(1.0, seed=103))
    emit("hint", render_hint(seed=11))
    emit("theme-parchment", render_themed("parchment"))
    emit("theme-neon", render_themed("neon"))
    emit("theme-dune", render_themed("dune"))
    emit("theme-blueprint", render_themed("blueprint"))
    # The same theme drives the town & dungeon renderers too.
    emit("theme-citadel-neon", render_settlement("citadel", seed=3, theme="neon"))
    emit("theme-dungeon-blueprint", render_dungeon(theme="blueprint"))

    # AtlasRenderer thumbnail — a direct PNG (no SVG), from the bundled sample pack.
    atlas_png = render_atlas(seed=5)
    if atlas_png:
        (out / "atlas.png").write_bytes(atlas_png)
        wrote_png = True
        print(f"wrote {out / 'atlas.png'}")
    else:
        print("(skipped atlas thumbnail — sample pack missing or Pillow not installed)")

    if not wrote_png:
        print("(cairosvg not installed — wrote SVG only; `pip install cairosvg` for PNGs)")


if __name__ == "__main__":
    main()
