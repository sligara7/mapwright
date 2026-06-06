"""mapwright — domain-neutral procedural fantasy map & world generation.

A dependency-light (numpy-only) library:

  * :class:`SeededRNG` — one seed drives everything; ``.derive(label)`` gives
    independent, reproducible sub-streams.
  * :class:`NameGenerator` — order-k Markov place/person names in several culture
    styles, seed-reproducible across processes.
  * :class:`RegionalTerrainGenerator` — Voronoi cells (Lloyd-relaxed) → heightmap
    → Planchon–Darboux depression fill → flux + hydraulic/creep erosion → rivers
    → latitude/elevation climate → Whittaker biomes. Returns neutral data
    (:class:`Biome`, :class:`TerrainResult`); mapping it onto a host app's tile
    vocabulary is the caller's job.
  * :class:`RegionalSVGRenderer` — shaded-relief (hillshade) SVG: biome polygons,
    coastline, rivers, labelled :class:`Marker` points.

Built clean-room from the published ideas in Azgaar's Fantasy-Map-Generator (MIT)
and rlguy/Mewo2's FantasyMapGenerator (Zlib). See NOTICE.

Quickstart::

    from mapwright import SeededRNG, RegionalTerrainGenerator, RegionalSVGRenderer
    terrain = RegionalTerrainGenerator(SeededRNG(7)).generate(60, 40)
    svg = RegionalSVGRenderer().render(terrain)
"""

from .affordances import (
    CellSummary,
    environment_affordances,
    summarize_cells,
)
from .atlas_renderer import ArtPack, AtlasRenderer
from .config import WorldMapConfig, PRESETS
from .dungeon import Dungeon, DungeonConfig, DungeonGenerator, Rect
from .dungeon_renderer import DungeonSVGRenderer
from .names import NameGenerator, MarkovNameGenerator, NAMEBASES
from .regions import Region, RegionGenerator
from .rng import SeededRNG
from .roads import Road, RegionalRoadGenerator
from .settlement import (
    Landmark,
    Lot,
    Settlement,
    SettlementConfig,
    SettlementGenerator,
    Street,
    TerrainField,
    Wall,
    Ward,
    SETTLEMENT_PRESETS,
    world_terrain_field,
)
from .settlement_renderer import SettlementSVGRenderer
from .svg_renderer import Marker, RegionalSVGRenderer
from .themes import THEMES, Theme
from .terrain import (
    Biome,
    River,
    TerrainCell,
    TerrainResult,
    RegionalTerrainGenerator,
    TERRAIN_TEMPLATES,
    compute_cell_polygons,
)

__version__ = "0.24.0"

__all__ = [
    "SeededRNG",
    "WorldMapConfig",
    "PRESETS",
    "CellSummary",
    "environment_affordances",
    "summarize_cells",
    "NameGenerator",
    "MarkovNameGenerator",
    "NAMEBASES",
    "Biome",
    "River",
    "TerrainCell",
    "TerrainResult",
    "RegionalTerrainGenerator",
    "TERRAIN_TEMPLATES",
    "compute_cell_polygons",
    "Marker",
    "RegionalSVGRenderer",
    "Theme",
    "THEMES",
    "ArtPack",
    "AtlasRenderer",
    "Road",
    "RegionalRoadGenerator",
    "Region",
    "RegionGenerator",
    "Dungeon",
    "DungeonConfig",
    "DungeonGenerator",
    "DungeonSVGRenderer",
    "Rect",
    "Settlement",
    "SettlementConfig",
    "SettlementGenerator",
    "SettlementSVGRenderer",
    "Ward",
    "Lot",
    "Street",
    "Wall",
    "Landmark",
    "TerrainField",
    "world_terrain_field",
    "SETTLEMENT_PRESETS",
]
