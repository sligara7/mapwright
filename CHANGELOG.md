# Changelog

All notable changes to mapwright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

**Public API** = the names exported in `mapwright.__all__` (pinned by
`tests/test_api_contract.py`). While the version is `0.x`, minor versions may
make breaking changes; these will always be noted here.

## [0.23.1] — 2026-06-06

### Fixed
- **Atlas ocean decorations no longer silently vanish.** `AtlasRenderer` used a
  symbol pick purely as an existence test (discarding the drawn symbol) and then
  independently coin-flipped between `decoration.ship` and `decoration.creature`.
  On an art pack that supplied only one of the two slots, roughly half the ocean
  decorations were dropped because the coin flip selected the absent slot. The
  renderer now decides the slot once and stamps it (a missing slot is a harmless
  no-op), which also removes the wasted RNG draws that made output sensitive to
  which decoration slots a pack happened to define.
- **Terrain-shaped coastal towns no longer place their footprint in the water.**
  The settlement footprint's minimum-core clamp ran on every ray, including rays
  that had stopped just shy of water — pushing those vertices back out past the
  shoreline (while still flagging them as water-bounded), so the derived coastline
  chord could sit in open sea. The clamp now applies only to rays not stopped by
  water; inland/cliff-hemmed towns keep their minimum core.

## [0.23.0] — 2026-06-05

### Added
- **Terrain-shaped settlements — a town takes the shape of its ground.**
  `SettlementGenerator.generate` accepts an optional `terrain=` field (a callable
  over normalised canvas coords `(xn, yn) -> elevation`, negative = water, or a 2D
  grid). The footprint is then grown out from the core, each ray stopping at water
  or ground too high to build — so a coastal town hugs its shore, a town between
  lakes grows fingers, and a town on open flats spreads round. The shoreline is
  derived from the real terrain (no synthetic straight coast needed); docks/harbour
  follow it.
  - New public `world_terrain_field(terrain, region=None)` builds such a field from
    a generated `RegionalTerrain`, mapping the town's canvas onto a world rectangle.
  - New public `TerrainField` type alias. Gallery **`terrain-town`** showcase.
  - `terrain=None` is **unchanged** from before.

### Changed
- **Continents are no longer roughly circular.** The single-continent heightmap no
  longer frames the map with a radial distance-from-centre falloff (which forced a
  disk). Instead it picks a per-world ocean-facing direction, offsets the landmass
  toward the far (passive) margin, seeds it from two cratonic sub-plates, and frames
  the sea with a **noise-warped, directional** term — so the coastline is ragged and
  the continent sits off-centre, running off one edge like a real continental
  margin. Multi-continent / template / hint worlds keep the prior radial frame and
  are byte-identical (`archipelago`/`islands` unchanged).
- **Organic town outlines are concave, not oval.** The procedural footprint drops
  the convex hull in favour of a star-shaped polar curve with several harmonics
  (arms + bays); wards are clipped back to it so the fill matches. Planned (`grid`)
  towns stay convex by design. The seeded RNG stream for settlements changed, so
  exact organic-town geometry differs from 0.22.0.

## [0.22.0] — 2026-06-02

### Added
- **Settlement `purpose` + landmarks — what a town exists for.** A new
  `SettlementConfig.purpose` field (`"general"` default, or `"trade"`,
  `"fortress"`, `"religious"`, `"harbor"`, `"extraction"`, `"transit"`). Anything
  but `"general"`:
  - promotes the central ward to a purpose-specific **landmark** kind
    (fortress→`citadel`, religious→`temple`, trade→`market`, harbor→`docks`,
    extraction→`mine`, transit→`plaza`), recorded on the new
    `Settlement.landmark` field (a new public `Landmark` type);
  - focuses the **main roads** on that landmark (they radiate from it to the
    gates);
  - **biases the ward-kind mix** toward the purpose (e.g. fortress → more
    garrison wards).
  - The renderer draws a star glyph over the landmark ward.
  - New presets **`fortress_town`**, **`pilgrimage_site`**, **`mining_camp`**;
    gallery `fortress-town` showcase.
  - `"general"` output is **byte-identical** to before (no landmark, unchanged
    ward bag and roads). New `Landmark` public type; `Settlement` serialisation
    bumps to `mapwright/settlement@5` (back-compatible — older payloads load with
    no landmark and `purpose="general"`).

## [0.21.0] — 2026-06-02

### Added
- **Grid streets — the organic ↔ planned layout descriptor.** A new
  `SettlementConfig.layout` field (`"organic"` default, or `"grid"`) chooses the
  street pattern. In `"grid"` mode the town gets a geometric street grid aligned
  to its long axis (PCA over the footprint): two families of parallel
  thoroughfares clipped to the footprint, the central line of each marked
  `"main"`, with gates where the mains pierce the perimeter (plus a harbour gate
  when coastal). Building lots are also bisected along the grid axes, so blocks
  come out rectangular and street-aligned. Walls splice grid gates (which land
  mid-edge) into the wall ring as real gatehouse gaps. This is the *Layout &
  Geometry* descriptor from the imaginative-realms taxonomy ("Hyper-Grid Rigid"),
  generalising the `era`/`wealth` shanty↔skyscraper axis.
  - New preset **`grid_city`**; gallery `grid-city` showcase.
  - The default (`layout="organic"`) output is **byte-identical** to before — all
    grid logic is gated behind the new mode.
  - `layout` serialises via `to_dict`/`json_schema` (new enum-field support in
    `SettlementConfig`, via an `_ENUM_SPEC`).
- New reusable geometry primitive `clip_line_to_convex` (Liang–Barsky line ↔
  convex-polygon clipping) in the internal `_geometry` module.

## [0.20.0] — 2026-06-02

### Added
- **Settlement `era` + `wealth` — the shanty ↔ skyscraper axis.** Two new 0..1
  `SettlementConfig` knobs (mapwright-original, extending the age/era/wealth idea
  to towns):
  - **`wealth`** scales plot *size* (poor = cramped tiny lots; rich = large
    estates/blocks) and the ward-kind *mix* (poor = slum-heavy; rich = more
    noble/temple wards).
  - **`era`** sets block *regularity* (ancient = organic, jittered; modern =
    near-grid rectangular blocks).
  Both are neutral at `0.5`, so the default output is **byte-identical** (the
  shaping factors are exact identities and the neutral ward bag equals the old
  fixed mix). Two new presets — `shantytown` and `metropolis` — and a gallery
  showcase of both. Purely additive; serialises via `to_dict`/`json_schema`.

## [0.19.0] — 2026-06-02

### Added
- **`elevation_hint` — art-direct the continent's shape.**
  `RegionalTerrainGenerator.generate(..., elevation_hint=…)` lets a host (or an
  LLM) supply the *macro* land/sea/elevation shape while mapwright fills in
  organic coastlines, erosion, rivers and climate. Pass a coarse 2D grid (e.g. a
  16×16 nested list; rows north→south, cols west→east) or a callable
  `f(x_norm, y_norm) -> elevation` over normalised [0, 1] coords. Only relative
  ordering matters; `sea_level` still sets how much floods, and the full
  hydrology/biome pipeline runs on top, so the result stays coherent (rivers
  form, coasts are organic). Takes precedence over `template`; set
  `edge_falloff=0` to allow land at the map borders. Purely additive — the
  default (`elevation_hint=None`) output is byte-identical. This is the
  most on-philosophy answer to "make shapes non-circular": the caller draws the
  shape, mapwright does the physics (the mapgen4 hint-map idea, clean-room).
  Gallery gains a hint-driven crescent continent.

## [0.18.0] — 2026-06-02

### Added
- **Environmental affordances + cell aggregation — `affordances` module.** Two
  domain-neutral helpers for turning terrain into a place's *environment*:
  - `environment_affordances(biome, temperature, moisture)` → neutral
    ecology-level tags (`scarce_water`, `disease_vector`, `predator`,
    `extreme_heat`, …). Biome-base tags plus climate-driven additions, so a
    hot+wet forest reads as a steamy jungle. A host app decides what, if
    anything, tags mean mechanically; this library never touches game rules.
  - `summarize_cells(cells)` → `CellSummary` (dominant biome, mean climate,
    hydrology flags, water fraction, affordances) for a footprint / explored
    area / whole map. Deterministic; ties broken by lowest `Biome` value.
  - New exports: `environment_affordances`, `summarize_cells`, `CellSummary`
    (purely additive to `__all__`).
- **Themes extended to the town & dungeon renderers.** A `Theme` now carries
  nested `SettlementPalette` + `DungeonPalette` sub-palettes, and
  `SettlementSVGRenderer` / `DungeonSVGRenderer` take a `theme=` just like the
  regional renderer — so one theme skins all three. The four built-ins
  (`parchment`, `neon`, `dune`, `blueprint`) now style towns and dungeons
  cohesively (e.g. a neon citadel, a blueprint dungeon). Sub-palettes default to
  parchment, so a theme that only restyles the regional map still drives the
  other renderers, and the default output is **byte-identical**. `SettlementPalette`
  / `DungeonPalette` are importable from `mapwright.themes` for authoring custom
  themes. Gallery gains a themed town + dungeon.

## [0.17.0] — 2026-06-02

### Added
- **Render themes — `Theme` + `THEMES`.** `RegionalSVGRenderer` now takes a
  `theme=` (a name or a `Theme`): a palette plus an optional biome *vocabulary*
  that re-skins the same neutral terrain without regenerating anything. The
  `Biome` enum is unchanged — a theme only decides how each biome looks and is
  named — so this is purely additive and the contract is stable. Built-ins:
  `parchment` (default, **byte-identical** to the previous output), `neon`
  (Tron / digital-grid), `dune` (Tatooine / sand medium), and `blueprint`.
  `Theme` is plain hex-string data (JSON-friendly), so a host or image service
  can author new ones; `Theme.biome_label()` exposes the vocabulary (e.g.
  `OCEAN` → "Void"). This is the first slice of the "Dominant Medium" /
  render-theme direction — pair a theme with a matching `ArtPack` for a full
  restyle. Gallery gains a same-continent neon/dune/blueprint showcase.

## [0.16.0] — 2026-06-02

### Added
- **`AtlasRenderer` + `ArtPack` — hand-drawn / themed atlas rendering.** A new
  optional renderer that stamps symbol images from an external *art pack* onto a
  `TerrainResult` to produce a hand-drawn (or any-style) fantasy-map look:
  mountains (young/mid/old by `land_age`), hills, forests (pine/deciduous/cactus
  by climate), dunes, settlements (by marker kind), and sea decorations + a
  compass rose. mapwright ships **no art** — an art pack is a directory of PNG
  symbols plus an optional `manifest.json` that maps mapwright's neutral concepts
  (`Biome`, `land_age`, settlement size) onto art "slots"; a host (e.g. an
  image-generation service) produces packs in any style and this renderer just
  places them. `ArtPack.from_directory()` reads a manifest, or auto-discovers
  slots from a conventional (Nortantis-style) folder layout. Missing fine-grained
  slots fall back to a coarser sibling, so partial packs still work. Requires
  Pillow — install the optional extra: `pip install "mapwright[atlas]"`. The
  core library stays numpy-only; without Pillow, `import mapwright` is unaffected
  and only `AtlasRenderer` rendering raises a clear install hint.

## [0.15.0] — 2026-06-02

### Added
- **`land_age` — geological age of the terrain** (a mapwright-original idea). A new
  `WorldMapConfig` knob: 0 = *young* (jagged, tall, snow-capped peaks — think the
  Rockies), 1 = *old* (worn down to rounded hills and lowlands — the Appalachians).
  It shapes the hypsometric curve (a gamma on land elevation → more/fewer mountains)
  and, for old land, applies weathering passes that smooth the relief. The default
  (0.5) is neutral — terrain is byte-identical to before, so the feature is purely
  opt-in. First slice of a broader age/era/wealth axis (forests, settlements next).

## [0.14.0] — 2026-06-02

### Added
- **Heightmap templates** — an optional, controllable alternative to the default
  tectonic auto-generation. `RegionalTerrainGenerator.generate(..., template=…)`
  builds the heightmap from composable elevation ops (hill, pit, range, trough,
  strait) spread over the cell graph, and `TERRAIN_TEMPLATES` provides named
  continent archetypes: `continents`, `archipelago`, `peninsula`, `isthmus`,
  `volcano`, `atoll`. A template sets the *pattern* of high/low ground; `config`
  still drives sea level (percentile), climate, and rivers on top. Clean-room from
  the documented idea in Azgaar's Fantasy-Map-Generator (see NOTICE). The default
  (no template) tectonic terrain is byte-identical — this is purely additive.

## [0.13.0] — 2026-06-02

### Changed
- **Tectonic-plate terrain.** The heightmap is now built from a simple plate
  simulation instead of a radial/noise field: the map is tiled into continental
  and oceanic plates (Voronoi over plate seeds) that drift, and **convergent plate
  boundaries raise mountain ranges** — so continents get organic coastlines *and*
  believable linear mountain belts, with no centre bias. The `continents` knob is
  the number of continental plates; oceanic plates interleave between them, so
  multi-continent worlds (`archipelago`, `islands`) fragment into scattered islands
  around an inner sea rather than one blob. Sea level is now **percentile-based**
  (`sea_level` maps directly to the water fraction). Rivers form reliably across
  all presets (a flux-quantile source threshold). Clean-room from the documented
  ideas of Nortantis (tectonics) and the Fractal Worldmap Generator (percentile
  sea level); see NOTICE. Regenerated the terrain/roads/regions gallery.

## [0.12.0] — 2026-06-02

### Changed
- **Organic, non-circular landmasses.** The heightmap was a sum of circular
  gaussians minus a radial edge falloff, so continents read as disks. It now
  splits **shape from elevation**: an organic land/sea mask (radial island bias +
  fractal value noise → irregular coastlines with bays, capes, and scattered
  islets) combined with a separate *smooth* elevation field (graph
  distance-to-coast + mountain relief + low-frequency valleys). Decoupling them is
  what keeps the coastline organic without fragmenting the drainage.
- **Rivers now use a flux-quantile source threshold** (top 3–13% of land cells by
  accumulated flux, scaling with `river_density`) instead of an absolute,
  total-cell-count threshold — so rivers form reliably on gentle and steep terrain
  alike and track the actual flux distribution. Regenerated the terrain gallery.

## [0.11.0] — 2026-06-02

### Changed
- **Settlement footprints are no longer circular.** Town outlines are now
  elongated along a random axis, lopsided via low-frequency radial lobes, and
  rotated — a clearly organic (but still convex, so lots/streets/walls stay valid)
  shape instead of a near-perfect disk. Regenerated the town/port/citadel gallery.

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

[Unreleased]: https://github.com/sligara7/mapwright/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/sligara7/mapwright/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/sligara7/mapwright/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/sligara7/mapwright/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/sligara7/mapwright/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/sligara7/mapwright/compare/v0.10.0...v0.11.0
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
