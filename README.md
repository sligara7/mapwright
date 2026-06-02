# mapwright

> ⚠️ **Early development (v0.x, alpha).** The API is still moving and may change without
> notice between versions. Usable today, but pin a version (e.g. `mapwright==0.10.0`) if
> you depend on it.

**Domain-neutral procedural fantasy map & world generation** — Voronoi terrain with
hydraulic erosion, climate-driven biomes, rivers, Markov place-names, and shaded-relief
SVG rendering. Pure Python, `numpy`-only, fully seed-deterministic.

mapwright produces *neutral data* (cells, biomes, rivers, polygons) and a self-contained
SVG renderer. It has no opinion about your application's models — map its output onto your
own tiles/entities however you like.

## Gallery

Every image below is a deterministic render of a built-in preset (or a dungeon),
produced by [`examples/gallery.py`](examples/gallery.py):

<table>
<tr>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/continent.png" alt="continent preset"><br><sub><code>continent</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/archipelago.png" alt="archipelago preset"><br><sub><code>archipelago</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/islands.png" alt="islands preset"><br><sub><code>islands</code></sub></td>
</tr>
<tr>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/highlands.png" alt="highlands preset"><br><sub><code>highlands</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/desert.png" alt="desert preset"><br><sub><code>desert</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/arctic.png" alt="arctic preset"><br><sub><code>arctic</code></sub></td>
</tr>
<tr>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/pangaea.png" alt="pangaea preset"><br><sub><code>pangaea</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/tropical.png" alt="tropical preset"><br><sub><code>tropical</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/dungeon.png" alt="generated dungeon"><br><sub><code>DungeonGenerator</code></sub></td>
</tr>
<tr>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/town.png" alt="generated town"><br><sub><code>SettlementGenerator</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/port.png" alt="generated coastal port"><br><sub><code>Settlement (port)</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/citadel.png" alt="generated walled citadel"><br><sub><code>Settlement (citadel)</code></sub></td>
</tr>
<tr>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/roads.png" alt="settlements linked by terrain-routed roads"><br><sub><code>RegionalRoadGenerator</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/regions.png" alt="land partitioned into named territories"><br><sub><code>RegionGenerator</code></sub></td>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/template-isthmus.png" alt="isthmus heightmap template"><br><sub><code>template="isthmus"</code></sub></td>
</tr>
<tr>
<td align="center"><img width="240" src="https://raw.githubusercontent.com/sligara7/mapwright/main/docs/gallery/template-atoll.png" alt="atoll heightmap template"><br><sub><code>template="atoll"</code></sub></td>
<td></td>
<td></td>
</tr>
</table>

Regenerate them with `python examples/gallery.py` (SVGs always; PNGs when
`cairosvg` is installed).

## Install

```bash
pip install mapwright
# latest from git:
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

Terrain defaults to a **tectonic-plate** simulation (organic coasts + mountain ranges).
For a controllable continent *archetype*, pass a `template` (Azgaar-style composed
heightmap ops) — `config` still drives sea level, climate, and rivers on top of it:

```python
from mapwright import RegionalTerrainGenerator, SeededRNG, WorldMapConfig, TERRAIN_TEMPLATES

print(list(TERRAIN_TEMPLATES))   # archipelago, volcano, peninsula, isthmus, atoll, continents
world = RegionalTerrainGenerator(SeededRNG(5)).generate(
    80, 58, WorldMapConfig(sea_level=0.55), template="archipelago")
```

Save and reload worlds (and dungeons) — JSON round-trips losslessly, so a reloaded
world renders byte-identically:

```python
from mapwright import RegionalTerrainGenerator, SeededRNG, TerrainResult

terrain = RegionalTerrainGenerator(SeededRNG(7)).generate(60, 40)
open("world.json", "w").write(terrain.to_json())          # ...later...
same = TerrainResult.from_json(open("world.json").read())  # bit-identical
```

`to_dict`/`from_dict` (and `to_json`/`from_json`) are available on `TerrainResult`,
`Dungeon`, and `Marker`. Numpy rasters and full-precision floats are preserved.

Procedural place-names in several culture styles:

```python
from mapwright import SeededRNG, NameGenerator

namer = NameGenerator(SeededRNG(7))
namer.settlement("nordic")    # -> 'Eirmundheim'
namer.settlement("elvish")    # -> 'Faelynnwood'
namer.region("dwarvish")      # -> 'The Korvald Reach'
```

Generate a dungeon and render it:

```python
from mapwright import SeededRNG, DungeonGenerator, DungeonSVGRenderer

dungeon = DungeonGenerator(SeededRNG(3)).generate(48, 32)
svg = DungeonSVGRenderer().render(dungeon, labels=True)  # number the rooms
open("dungeon.svg", "w").write(svg)
print(dungeon.ascii())  # or eyeball it as text
```

Generate a town — an organic footprint split into named wards, each subdivided
into building lots, threaded with streets, and optionally walled (try the
`port` and `citadel` presets):

```python
from mapwright import SeededRNG, SettlementGenerator, SettlementConfig, SettlementSVGRenderer

town    = SettlementGenerator(SeededRNG(7)).generate(90, 90)
port    = SettlementGenerator(SeededRNG(5)).generate(90, 90, SettlementConfig.preset("port"))
citadel = SettlementGenerator(SeededRNG(3)).generate(90, 90, SettlementConfig.preset("citadel"))
open("town.svg", "w").write(SettlementSVGRenderer().render(town))
```

Settlement presets: `hamlet`, `village`, `town`, `city`, `port`, `citadel`.

## What's inside

| Component | What it does |
|-----------|--------------|
| `SeededRNG` | One seed drives everything; `.derive(label)` yields independent, reproducible sub-streams (unifies stdlib + numpy). |
| `NameGenerator` | Order-k character Markov names over hand-authored culture namebases; reproducible across processes. |
| `RegionalTerrainGenerator` | Voronoi cells (Lloyd-relaxed) → **tectonic-plate** heightmap (organic coasts + mountain ranges at plate collisions; percentile sea level) → Planchon–Darboux depression fill → flux + hydraulic/creep erosion → rivers + inland lakes → latitude/elevation climate with **rain-shadow** → Whittaker biomes. |
| `compute_cell_polygons` | Reconstructs convex Voronoi polygons (half-plane clipping) for vector rendering. |
| `RegionalSVGRenderer` | Shaded-relief (hillshade) SVG: biome polygons, coastline, rivers, roads, labelled markers. |
| `RegionalRoadGenerator` | Connects settlement sites with trade routes — an MST whose edges are A*-routed over the terrain (avoids sea, climbs/crosses rivers at a cost). |
| `RegionGenerator` | Partitions land into named factions/territories: spread capitals seed a flood fill over the land graph (sea divides them); each `Region` is Markov-named. |
| `DungeonGenerator` | BSP-partitioned rooms + minimum-spanning-tree corridors → rooms, corridor cells, and a walkable grid (with `Dungeon.ascii()`). |
| `DungeonSVGRenderer` | Renders a `Dungeon` to SVG: walls, carved floor, room outlines, optional tile grid and per-room labels. |
| `SettlementGenerator` | Self-contained town layout: an organic footprint divided into named Voronoi **wards** (market, docks, …), each subdivided into building **lots**, a **street** network (MST over ward adjacency + main roads from gates to the market), an optional defensive **wall** (towers + gate gaps, opened at the harbour when coastal), and optional coastline. |
| `SettlementSVGRenderer` | Renders a `Settlement` to SVG: sea, footprint, kind-coloured wards, building lots, streets, wall with towers/gatehouses, labels. |

Everything is neutral: `RegionalTerrainGenerator` returns a `TerrainResult` of `TerrainCell`s
(each with a `Biome`), and you decide how a `Biome` maps to your world.

## Determinism

Every generator draws from a `SeededRNG`. The same seed (and parameters) reproduces an
identical world — terrain, names, rivers, and SVG — across runs *and across processes*
(the Markov chains are built in sorted order, so output never depends on `PYTHONHASHSEED`).

## Performance

Pure Python + numpy, single-threaded. Typical map/town sizes generate in well under a
second; `examples/benchmark.py` prints a table for your machine. Rough figures (numbers
are machine-dependent):

| Generator | Size | Time |
|-----------|------|------|
| Terrain | 64×44 (≈470 cells) | ~150 ms |
| Terrain | 120×90 (1500 cells, capped) | ~1.8 s |
| Dungeon | 80×60 (≈50 rooms) | ~9 ms |
| Settlement | pop 9000 (50 wards, ~1100 lots) | ~65 ms |
| Roads / regions | on a 120×90 map | a few ms |

Two things worth knowing:

- **Terrain cell count is capped at 1500** (`cell_area` clamp in `generate`), which bounds
  the hydrology/climate/graph work — but the initial Voronoi *rasterisation* is per-pixel,
  so total time still grows roughly linearly with `width × height` on large maps. Raise
  `cell_area` (fewer, coarser cells) to trade detail for speed, e.g.
  `generate(w, h, cell_area=12)`.
- **Dungeon corridor connection is a dense MST (~O(rooms³))**, so dungeons with hundreds of
  rooms get slow — keep them modest or raise `DungeonConfig.min_leaf` for fewer, larger rooms.

## API stability & contract

The **public API is exactly the names exported in `mapwright.__all__`** — that's
the contract. It's pinned by `tests/test_api_contract.py` (public surface, key
signatures), so an accidental breaking change fails CI.

For the world parameters specifically, `WorldMapConfig.json_schema()` returns a
JSON Schema (draft 2020-12) — the machine-readable contract a host app or LLM can
validate/generate against, then feed through `WorldMapConfig.from_dict()` (which
clamps to valid ranges). Schema and runtime clamping are generated from the same
field spec, so they can't drift.

Versioning follows [SemVer](https://semver.org/). While at `0.x` the API may still
change between minor versions; every change is recorded in `CHANGELOG.md`. Pin a
tag or commit if you depend on it.

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
