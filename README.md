# mapwright

> ⚠️ **Early development (v0.1, alpha).** The API is still moving and may change without
> notice between versions. Extracted from a working application; usable today, but pin a
> commit if you depend on it.

**Domain-neutral procedural fantasy map & world generation** — Voronoi terrain with
hydraulic erosion, climate-driven biomes, rivers, Markov place-names, and shaded-relief
SVG rendering. Pure Python, `numpy`-only, fully seed-deterministic.

mapwright produces *neutral data* (cells, biomes, rivers, polygons) and a self-contained
SVG renderer. It has no opinion about your application's models — map its output onto your
own tiles/entities however you like.

## Install

```bash
pip install git+https://github.com/sligara7/mapwright.git
# or, for local development:
pip install -e ".[dev]"
```

## Quickstart

```python
from mapwright import SeededRNG, RegionalTerrainGenerator, RegionalSVGRenderer, Marker

# Same seed -> same world, every time.
terrain = RegionalTerrainGenerator(SeededRNG(7)).generate(width=60, height=40)

markers = [Marker(name="Eldmoor", x=30, y=18, kind="settlement_city")]
svg = RegionalSVGRenderer().render(terrain, markers)
open("world.svg", "w").write(svg)
```

Shape the world with `WorldMapConfig` — or describe it and let an LLM fill the config:

```python
from mapwright import WorldMapConfig, RegionalTerrainGenerator, SeededRNG

desert = WorldMapConfig.preset("desert")          # ready-made worlds...
custom = WorldMapConfig(continents=7, sea_level=0.55, temperature=-0.8)  # ...or tune
world  = RegionalTerrainGenerator(SeededRNG(1)).generate(60, 40, config=desert)

# Every field is a bounded scalar with a clear meaning, so it doubles as a schema
# a host app (or an LLM) can populate. from_dict clamps junk to valid ranges:
WorldMapConfig.from_dict({"temperature": 5, "continents": -3})  # -> safe, clamped
```

Presets: `continent`, `pangaea`, `archipelago`, `islands`, `highlands`, `desert`,
`arctic`, `tropical`.

Procedural place-names in several culture styles:

```python
from mapwright import SeededRNG, NameGenerator

namer = NameGenerator(SeededRNG(7))
namer.settlement("nordic")    # -> 'Eirmundheim'
namer.settlement("elvish")    # -> 'Faelynnwood'
namer.region("dwarvish")      # -> 'The Korvald Reach'
```

## What's inside

| Component | What it does |
|-----------|--------------|
| `SeededRNG` | One seed drives everything; `.derive(label)` yields independent, reproducible sub-streams (unifies stdlib + numpy). |
| `NameGenerator` | Order-k character Markov names over hand-authored culture namebases; reproducible across processes. |
| `RegionalTerrainGenerator` | Voronoi cells (Lloyd-relaxed) → heightmap → Planchon–Darboux depression fill → flux + hydraulic/creep erosion → rivers → latitude/elevation climate → Whittaker biomes. |
| `compute_cell_polygons` | Reconstructs convex Voronoi polygons (half-plane clipping) for vector rendering. |
| `RegionalSVGRenderer` | Shaded-relief (hillshade) SVG: biome polygons, coastline, rivers, labelled markers. |

Everything is neutral: `RegionalTerrainGenerator` returns a `TerrainResult` of `TerrainCell`s
(each with a `Biome`), and you decide how a `Biome` maps to your world.

## Determinism

Every generator draws from a `SeededRNG`. The same seed (and parameters) reproduces an
identical world — terrain, names, rivers, and SVG — across runs *and across processes*
(the Markov chains are built in sorted order, so output never depends on `PYTHONHASHSEED`).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Credits & license

MIT licensed (see `LICENSE`). Algorithms were implemented clean-room from the publicly
described techniques of **Azgaar's Fantasy-Map-Generator** (MIT) and **Martin O'Leary /
Ryan L. Guy's FantasyMapGenerator** (Zlib); see `NOTICE` for details. The bundled name
lists are original.
