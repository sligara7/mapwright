"""Neutral environmental affordances and cell aggregation.

Two domain-neutral helpers that sit on top of the terrain model:

  * :func:`environment_affordances` — *what a place affords*: the coarse,
    ecology-level hazards/opportunities implied by a biome plus its climate
    (a swamp breeds disease; a desert is short on water; a hot, wet forest gets
    both predators and biting-insect disease vectors). Tags are neutral strings —
    a host app decides what, if anything, they *mechanically* mean (a D&D layer
    might map ``"scarce_water"`` to an exhaustion clock, ``"predator"`` to an
    encounter table). This library never reaches into game rules.

  * :func:`summarize_cells` — reduce a set of :class:`TerrainCell` (a place's
    footprint, an explored area, a whole map) to one :class:`CellSummary`:
    dominant biome, mean climate, hydrology flags, and the affordance tags for
    the aggregate. Pure stats; the caller chooses which cells make up the place.

Both are deterministic — same cells in, same summary out — so they fit the
seed-reproducible contract of the rest of mapwright.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .terrain import Biome, TerrainCell

# Base affordances intrinsic to each biome, independent of climate swing.
# Neutral, ecology-level vocabulary — not game mechanics.
_BIOME_BASE_AFFORDANCES: dict[Biome, tuple[str, ...]] = {
    Biome.OCEAN: ("drowning", "no_shelter", "open_water"),
    Biome.COAST: ("tides", "open_water"),
    Biome.BEACH: ("tides", "exposure"),
    Biome.DESERT: ("scarce_water", "exposure", "extreme_heat"),
    Biome.PLAINS: ("exposure",),
    Biome.FOREST: ("dense_cover", "predator", "easy_to_get_lost"),
    Biome.SWAMP: ("disease_vector", "difficult_terrain", "predator", "miasma"),
    Biome.HILLS: ("difficult_terrain",),
    Biome.MOUNTAIN: ("rockfall", "thin_air", "difficult_terrain", "extreme_cold"),
    Biome.TUNDRA: ("extreme_cold", "exposure", "scarce_food"),
    Biome.SNOW: ("extreme_cold", "exposure", "whiteout"),
    Biome.RIVER: ("currents", "water_source"),
    Biome.LAKE: ("cold_water", "water_source"),
}

# Climate thresholds (on the 0..1 cell scales) that add extra affordances on top
# of the biome base — this is what makes a *hot, wet* forest read as a steamy
# jungle (predators + disease vectors) rather than a temperate wood.
_ARID = 0.22
_HUMID = 0.78
_COLD = 0.22
_HOT = 0.78


def environment_affordances(
    biome: Biome, temperature: float, moisture: float
) -> tuple[str, ...]:
    """Neutral affordance tags for a biome under a given climate.

    ``temperature`` and ``moisture`` are the 0..1 cell scales. Returns a
    de-duplicated tuple, biome-base tags first then climate-driven additions,
    each in a stable order so the result is reproducible.
    """
    base = _BIOME_BASE_AFFORDANCES.get(biome, ())
    tags: list[str] = list(base)
    # A freshwater biome (river/lake) affords drinking water, so heat/aridity must
    # not also call water "scarce" — that's contradictory. An ocean keeps
    # scarce_water on purpose: plenty of water at sea, none of it drinkable.
    has_fresh_water = "water_source" in base

    extra: list[str] = []
    if moisture <= _ARID and not has_fresh_water:
        extra.append("scarce_water")
    if moisture >= _HUMID:
        # standing water + warmth is what actually breeds the biting insects /
        # waterborne illness; pair with heat below for the full jungle effect.
        extra.append("disease_vector")
    if temperature >= _HOT:
        extra.append("extreme_heat")
        if not has_fresh_water:
            extra.append("scarce_water")
    if temperature <= _COLD:
        extra.append("extreme_cold")

    # de-dup, preserving first-seen order (base before climate extras)
    seen: dict[str, None] = {}
    for tag in (*tags, *extra):
        seen.setdefault(tag, None)
    return tuple(seen)


@dataclass
class CellSummary:
    """Aggregate of a set of terrain cells — one place's environment."""

    dominant_biome: Biome
    temperature: float       # mean over the cells, 0..1
    moisture: float          # mean over the cells, 0..1
    mean_height: float       # mean raw height, 0..1 (compare to a TerrainResult.sea_level)
    has_river: bool
    has_lake: bool
    water_fraction: float    # fraction of cells that are ocean or lake, 0..1
    cell_count: int
    affordances: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "dominant_biome": int(self.dominant_biome),
            "temperature": self.temperature,
            "moisture": self.moisture,
            "mean_height": self.mean_height,
            "has_river": self.has_river,
            "has_lake": self.has_lake,
            "water_fraction": self.water_fraction,
            "cell_count": self.cell_count,
            "affordances": list(self.affordances),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CellSummary":
        return cls(
            dominant_biome=Biome(int(data["dominant_biome"])),
            temperature=float(data["temperature"]),
            moisture=float(data["moisture"]),
            mean_height=float(data["mean_height"]),
            has_river=bool(data["has_river"]),
            has_lake=bool(data["has_lake"]),
            water_fraction=float(data["water_fraction"]),
            cell_count=int(data["cell_count"]),
            affordances=tuple(data["affordances"]),
        )


def summarize_cells(cells: Iterable[TerrainCell]) -> CellSummary:
    """Reduce a set of cells to one :class:`CellSummary`.

    The dominant biome is the modal biome over the cells (ties broken by the
    lowest :class:`Biome` value, for determinism). Climate fields are means.
    Raises ``ValueError`` on an empty input — an empty footprint is a caller
    bug, not something to paper over with a default.
    """
    cells = list(cells)
    if not cells:
        raise ValueError("summarize_cells requires at least one cell")

    counts = Counter(c.biome for c in cells)
    top = max(counts.values())
    dominant = min(b for b, n in counts.items() if n == top)

    n = len(cells)
    temperature = sum(c.temperature for c in cells) / n
    moisture = sum(c.moisture for c in cells) / n
    mean_height = sum(c.height for c in cells) / n
    water_fraction = sum(c.is_water or c.is_lake for c in cells) / n

    return CellSummary(
        dominant_biome=dominant,
        temperature=temperature,
        moisture=moisture,
        mean_height=mean_height,
        has_river=any(c.is_river for c in cells),
        has_lake=any(c.is_lake for c in cells),
        water_fraction=water_fraction,
        cell_count=n,
        affordances=environment_affordances(dominant, temperature, moisture),
    )
