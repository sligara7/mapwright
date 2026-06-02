# Changelog

All notable changes to mapwright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

**Public API** = the names exported in `mapwright.__all__` (pinned by
`tests/test_api_contract.py`). While the version is `0.x`, minor versions may
make breaking changes; these will always be noted here.

## [Unreleased]

### Added
- Property-based tests (Hypothesis, a new dev dependency) over config clamping,
  the geometry/graph primitives, and generation round-trips. Test-only — no
  change to the public API.
- `examples/benchmark.py` — micro-benchmarks for the generators, and a
  **Performance** section in the README documenting the 1500-cell terrain cap,
  the per-pixel rasterisation cost on large maps, and the dungeon MST scaling.

## [0.10.0] — 2026-06-02

### Added
- **Region / faction assignment** — `RegionGenerator.generate(terrain, count=…)`
  partitions the land into named territories: well-spread capital cells
  (farthest-point sampling) seed a multi-source flood fill over the land-cell
  graph, so each reachable land cell joins its nearest capital's region (the sea
  divides them). Returns `Region` objects (name, capital cell, member cells);
  region names come from the Markov `NameGenerator`. `RegionalSVGRenderer.render(
  ..., regions=…)` draws political borders and italic region labels.

## [0.9.0] — 2026-06-02

### Added
- **Regional roads / trade routes** — `RegionalRoadGenerator.generate(terrain, sites)`
  connects settlement sites with a road network: a minimum spanning tree over the
  sites whose edges are A*-routed over the terrain cell graph, preferring flat land
  and paying penalties for sea, lakes, rivers (bridges), and uphill slope. Returns
  `Road` objects (lists of cell ids); `RegionalSVGRenderer.render(..., roads=…)`
  draws them as dashed routes with a pale casing.
- **Generic A* (`_graph.astar`)** — an internal shortest-path primitive over an
  arbitrary graph (neighbour + cost + heuristic callbacks), alongside `prim_mst`.

## [0.8.0] — 2026-06-01

### Added
- **Inland lakes** — `RegionalTerrainGenerator` now flags interior hollows (land
  cells lower than most neighbours that collect real flux) as lakes: a new
  `Biome.LAKE`, an `is_lake` flag on `TerrainCell`, and a bounded
  `WorldMapConfig.lake_density` knob. Lakes seed the moisture model and rivers
  terminate into them; `RegionalSVGRenderer` draws them as flat freshwater.
- **Rain-shadow climate** — moisture is now attenuated on the lee of high terrain.
  A prevailing wind is swept upwind→downwind over the cell graph; air rising over
  mountains precipitates (wet windward slopes) and arrives dry on the far side, so
  windward/leeward biome contrast emerges naturally.

### Changed
- `TerrainCell` gains an `is_lake` field and `TerrainResult` serialisation bumps to
  `mapwright/terrain@2`; older `@1` payloads still load (`is_lake` defaults False).
  `WorldMapConfig` gains `lake_density` (old payloads use its default). `Biome` adds
  `LAKE = 12`. The `desert` preset sets a low `lake_density` for aridity.

## [0.7.0] — 2026-06-01

### Added
- **Settlement walls** — a defensive wall when `walled=True`, completing the
  settlement tier. `Wall` (ring, closed, gates) is added to the public surface
  and `Settlement.wall` holds it. The wall follows the footprint perimeter with a
  tower at each corner and gate gaps where the main roads exit; on a coastal town
  the ring is opened along the coast (a harbour, no wall over water).
  `SettlementSVGRenderer` draws the wall, round corner towers, and square
  gatehouses at the gates (replacing the previous heavier-boundary placeholder).
  Added a walled `citadel` to the gallery.

### Changed
- `Settlement` serialisation tag bumped to `mapwright/settlement@4` (adds
  `wall`); older payloads without it still load (`wall` defaults to `None`).

## [0.6.0] — 2026-06-01

### Added
- **Settlement streets** — a road network over the wards. `Street` (path, kind)
  is added to the public surface; `Settlement` gains `streets` and `gates`.
  Wards are connected by a minimum-spanning-tree (the shared `prim_mst`) over
  ward adjacency — detected from shared polygon edges — plus a few loop roads;
  each minor street runs through the two ward centres via their shared-edge
  midpoint. A few gates are placed around the footprint (plus a harbour gate on
  the coast when `coastal=True`), and `"main"` roads connect each gate to the
  market. `SettlementSVGRenderer` overlays the network (casing + pale surface,
  main roads wider) with a `show_streets` toggle.

### Changed
- `Settlement` serialisation tag bumped to `mapwright/settlement@3` (adds
  `streets` and `gates`); older payloads without those keys still load (they
  default to empty).

## [0.5.0] — 2026-06-01

### Added
- **Settlement lots** — wards are now subdivided into building plots. `Lot`
  (id, polygon, ward) is added to the public surface and `Settlement.lots` lists
  them; `SettlementSVGRenderer` draws the building footprints (toggle with
  `show_lots`). Each buildable ward is recursively bisected across its longest
  axis down to a target plot area — a new bounded `SettlementConfig.lot_size`
  knob — with per-kind sizing (noble = large estates, slums = cramped, market =
  an open square with no buildings); each plot is inset so gaps read as alleys.
  New shared geometry primitives: `polygon_area`, `inset_convex`.

### Changed
- `Settlement` serialisation tag bumped to `mapwright/settlement@2` (adds
  `lots`); older `@1` payloads without a `lots` key still load (lots default to
  empty), and `SettlementConfig` gains `lot_size` (old payloads use its default).

## [0.4.0] — 2026-06-01

### Added
- **Settlement tier (wards)** — `SettlementGenerator` → `Settlement` (+ `SettlementConfig`,
  `Ward`, `SETTLEMENT_PRESETS`, and `SettlementSVGRenderer`). Self-contained town
  *layout*: an organic convex footprint divided into named Voronoi wards (a central
  market, residential/craftsmen/temple/noble/garrison/slums mix, plus a dockside ward
  and synthetic coastline when `coastal=True`); `walled` is recorded for the upcoming
  wall layer. Config follows the `WorldMapConfig` discipline (bounded knobs + boolean
  flags, `from_dict` clamping, `json_schema()`, presets) and the result round-trips
  via `to_dict`/`from_dict`/`to_json`/`from_json`. Clean-room from the *ideas* of
  Watabou's TownGeneratorOS (GPLv3) — concept only, no code; see NOTICE.
  _This is the first slice of the tier; lots, streets, and walls follow in later
  versions, and the `Settlement`/`Ward` shapes will grow accordingly._

### Changed
- Internal refactor: extracted the shared Voronoi/Lloyd, half-plane polygon
  clipping, and Prim-MST primitives into `_geometry.py` / `_graph.py`; the terrain
  and dungeon tiers now delegate to them (no behaviour change — same seeds produce
  byte-identical output). New tiers build on these instead of re-implementing them.

## [0.3.0] — 2026-06-01

### Added
- **Dungeon SVG renderer** — `DungeonSVGRenderer.render(dungeon, …)` turns a
  `Dungeon` into a scalable SVG (dark walls, carved floor from the walkable grid,
  room outlines, optional faint tile grid via `show_grid`, optional per-room
  labels via `labels=True`/a sequence). Mirrors `RegionalSVGRenderer`: pure
  string-building, no new dependency. Closes the previously ascii-only gap for
  dungeons.
- **Serialisation / JSON round-trip** — `to_dict`/`from_dict` and
  `to_json`/`from_json` on `TerrainResult`, `Dungeon`, and `Marker` (plus
  `to_dict`/`from_dict` on the nested `TerrainCell`, `River`, and `Rect`). A saved
  world or dungeon reloads bit-identically — numpy rasters (`cell_of`, dungeon
  `grid`) and full-precision floats are preserved, so a reloaded world renders to
  byte-identical SVG. Payloads carry a `schema` tag and ignore unknown keys on
  load (forward-compatible). No new dependency; pure-JSON builtins only.

## [0.2.0] — 2026-06-01

### Added
- **Dungeon generation** — `DungeonGenerator` → `Dungeon` (+ `DungeonConfig`, `Rect`):
  BSP space-partitioning rooms (no overlap) connected by a Prim minimum-spanning-tree
  of L-corridors, plus optional loop corridors. Returns rooms, carved corridor cells,
  and a boolean walkable grid; `Dungeon.ascii()` for quick previews. Clean-room from
  Dungeon-Generator (MIT, BSP) and donjuan (CC0, MST connectivity).

## [0.1.0] — 2026-06-01

Initial release. Domain-neutral procedural fantasy map & world generation.

### Added
- `SeededRNG` — one-seed determinism with `.derive(label)` sub-streams; unifies
  the stdlib and numpy generators. Reproducible across processes.
- `NameGenerator` / `MarkovNameGenerator` — order-k Markov place/person names
  over hand-authored culture namebases (`NAMEBASES`); hash-seed independent.
- `RegionalTerrainGenerator` → `TerrainResult` — Voronoi cells (Lloyd-relaxed),
  Planchon–Darboux depression fill, hydraulic + creep erosion, river tracing,
  latitude/elevation climate, and a Whittaker `Biome` matrix.
- `WorldMapConfig` — bounded, documented world parameters (sea level, continents,
  climate, mountains, rivers) with `from_dict` clamping, named `PRESETS`, and a
  `json_schema()` contract for host/LLM population.
- `RegionalSVGRenderer` + `Marker` — shaded-relief (hillshade) SVG: biome
  polygons, coastline, rivers, labelled markers. `compute_cell_polygons` rebuilds
  convex Voronoi polygons via half-plane clipping.

[Unreleased]: https://github.com/sligara7/mapwright/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/sligara7/mapwright/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/sligara7/mapwright/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/sligara7/mapwright/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/sligara7/mapwright/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/sligara7/mapwright/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/sligara7/mapwright/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/sligara7/mapwright/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sligara7/mapwright/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/sligara7/mapwright/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sligara7/mapwright/releases/tag/v0.1.0
