# Changelog

All notable changes to mapwright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

**Public API** = the names exported in `mapwright.__all__` (pinned by
`tests/test_api_contract.py`). While the version is `0.x`, minor versions may
make breaking changes; these will always be noted here.

## [Unreleased]

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

[Unreleased]: https://github.com/sligara7/mapwright/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sligara7/mapwright/releases/tag/v0.1.0
