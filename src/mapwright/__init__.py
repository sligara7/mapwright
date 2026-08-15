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

from ._tectonics import simulate_tectonic_world
from .affordances import (
    CellSummary,
    environment_affordances,
    summarize_cells,
)
from .atlas_renderer import ArtPack, AtlasRenderer
from .config import PRESETS, WorldMapConfig
from .dungeon import Dungeon, DungeonConfig, DungeonGenerator, Rect
from .dungeon_renderer import DungeonSVGRenderer
from .features import Feature, FeatureGenerator
from .labeling import LabelPlacer, LabelRequest, PlacedLabel
from .names import NAMEBASES, MarkovNameGenerator, NameGenerator
from .regions import Region, RegionGenerator
from .rng import SeededRNG
from .roads import RegionalRoadGenerator, Road
from .settlement import (
    SETTLEMENT_PRESETS,
    Landmark,
    Lot,
    Settlement,
    SettlementConfig,
    SettlementGenerator,
    Street,
    TerrainField,
    Wall,
    Ward,
    world_terrain_field,
)
from .settlement_renderer import SettlementSVGRenderer
from .svg_renderer import Marker, RegionalSVGRenderer
from .terrain import (
    TERRAIN_TEMPLATES,
    Biome,
    RegionalTerrainGenerator,
    River,
    TerrainCell,
    TerrainResult,
    compute_cell_polygons,
)
from .themes import DEFAULT_THEME, THEMES, Theme, get_theme, theme_names

__version__ = "0.28.0"

__all__ = [
    "DEFAULT_THEME",
    "NAMEBASES",
    "PRESETS",
    "SETTLEMENT_PRESETS",
    "TERRAIN_TEMPLATES",
    "THEMES",
    "ArtPack",
    "AtlasRenderer",
    "Biome",
    "CellSummary",
    "Dungeon",
    "DungeonConfig",
    "DungeonGenerator",
    "DungeonSVGRenderer",
    "Feature",
    "FeatureGenerator",
    "LabelPlacer",
    "LabelRequest",
    "Landmark",
    "Lot",
    "Marker",
    "MarkovNameGenerator",
    "NameGenerator",
    "PlacedLabel",
    "Rect",
    "Region",
    "RegionGenerator",
    "RegionalRoadGenerator",
    "RegionalSVGRenderer",
    "RegionalTerrainGenerator",
    "River",
    "Road",
    "SeededRNG",
    "Settlement",
    "SettlementConfig",
    "SettlementGenerator",
    "SettlementSVGRenderer",
    "Street",
    "TerrainCell",
    "TerrainField",
    "TerrainResult",
    "Theme",
    "Wall",
    "Ward",
    "WorldMapConfig",
    "compute_cell_polygons",
    "environment_affordances",
    "get_theme",
    "simulate_tectonic_world",
    "summarize_cells",
    "theme_names",
    "world_terrain_field",
]
