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


def render_template(template: str, sea_level: float, seed: int) -> str:
    cfg = WorldMapConfig(sea_level=sea_level)
    t = RegionalTerrainGenerator(SeededRNG(seed)).generate(MAP_W, MAP_H, config=cfg, template=template)
    return RegionalSVGRenderer(scale=MAP_SCALE).render(t)


def render_dungeon(seed: int = 3) -> str:
    dungeon = DungeonGenerator(SeededRNG(seed)).generate(DUNGEON_W, DUNGEON_H)
    return DungeonSVGRenderer(scale=DUNGEON_SCALE).render(dungeon, labels=True)


def render_settlement(preset: str | None, seed: int) -> str:
    cfg = SettlementConfig.preset(preset) if preset else None
    town = SettlementGenerator(SeededRNG(seed)).generate(TOWN_W, TOWN_H, cfg)
    return SettlementSVGRenderer(scale=TOWN_SCALE).render(town)


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
    emit("roads", render_roads(seed=7))
    emit("regions", render_regions(seed=4))
    emit("template-isthmus", render_template("isthmus", 0.5, seed=5))
    emit("template-atoll", render_template("atoll", 0.55, seed=8))
    emit("age-young", render_age(0.0, seed=103))
    emit("age-old", render_age(1.0, seed=103))

    if not wrote_png:
        print("(cairosvg not installed — wrote SVG only; `pip install cairosvg` for PNGs)")


if __name__ == "__main__":
    main()
