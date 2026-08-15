"""Named geographic features over regional terrain.

Groups a :class:`~mapwright.terrain.TerrainResult` into connected components and
names the notable ones, so a renderer (or a host app) has *named* seas, lakes,
islands, mountain ranges, and forests to label — rather than an anonymous field
of biome cells.

Five feature kinds, all from connected-component flood fill over ``cell.neighbors``:

* ``ocean``  — a body of sea water that reaches the map frame.
* ``lake``   — an enclosed body of water (does not reach the frame).
* ``island`` — a connected landmass.
* ``range``  — a connected massif of ``MOUNTAIN``/``HILLS`` cells.
* ``forest`` — a connected stand of ``FOREST`` cells.

Domain-neutral, in the shape of :class:`~mapwright.regions.RegionGenerator`:
returns :class:`Feature` objects (kind, name, member cell ids, a cached centroid
and area) that a host may label, colour, or ignore. Seed-deterministic via
:class:`~mapwright.rng.SeededRNG`.

Clean-room from the *idea* in Azgaar's FMG (``features.ts``): connected components
label ocean/lake/island, and features large enough to matter get named.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import _serde
from .names import NameGenerator
from .rng import SeededRNG
from .terrain import Biome, TerrainResult

# Per-kind name templates. ``{name}`` is a bare Markov place-name root; the rest
# is domain-neutral cartographic furniture ("Sea of X", "X Range", ...).
_FEATURE_FORMS: dict[str, list[str]] = {
    "ocean": ["The {name} Sea", "Sea of {name}", "The {name} Ocean", "{name} Deep"],
    "lake": ["Lake {name}", "{name} Mere", "The {name} Water", "Loch {name}"],
    "island": ["{name}", "Isle of {name}", "{name} Isle", "{name}"],
    "range": ["The {name} Range", "{name} Mountains", "The {name} Peaks", "{name} Heights"],
    "forest": ["{name} Wood", "The {name} Forest", "{name}wood", "Forest of {name}"],
}


@dataclass
class Feature:
    """A named geographic feature: its kind and the cells it spans."""

    id: int
    kind: str                    # "ocean" | "lake" | "island" | "range" | "forest"
    name: str
    cells: list[int]             # member cell ids
    centroid: tuple[float, float]  # mean cell-site position, for label anchoring
    area: float                  # member-cell count (a cheap size proxy)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "cells": list(self.cells),
            "centroid": [self.centroid[0], self.centroid[1]],
            "area": self.area,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Feature:
        c = data.get("centroid", [0.0, 0.0])
        return cls(
            id=int(data["id"]),
            kind=str(data["kind"]),
            name=str(data["name"]),
            cells=[int(x) for x in data["cells"]],
            centroid=(float(c[0]), float(c[1])),
            area=float(data["area"]),
        )

    def to_json(self, **kwargs) -> str:
        """Serialise to a JSON string (``kwargs`` pass to :func:`json.dumps`)."""
        return _serde.to_json(self, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> Feature:
        return _serde.from_json(cls, text)


class FeatureGenerator:
    """Detects and names geographic features on a terrain map."""

    def __init__(self, rng: SeededRNG, names: NameGenerator | None = None):
        self._rng = rng.derive("features")
        # Own naming sub-stream (like RegionGenerator) so features stay decoupled;
        # a caller may inject a shared NameGenerator for a consistent vocabulary.
        self._names = names or NameGenerator(self._rng.derive("names"))

    def generate(
        self,
        terrain: TerrainResult,
        *,
        culture: str = "generic",
        min_ocean: int = 4,
        min_lake: int = 2,
        min_island: int = 3,
        min_range: int = 4,
        min_forest: int = 4,
    ) -> list[Feature]:
        """Return the named features of ``terrain``.

        Only components at or above the per-kind minimum cell counts are emitted,
        so the result is the *notable* features worth naming — not every puddle,
        rocky outcrop, or one-cell sliver of frame water. ``culture`` selects the
        namebase.
        """
        cells = terrain.cells
        border = self._border_cells(terrain)

        features: list[Feature] = []
        next_id = 0

        # -- water: ocean (reaches frame) vs lake (enclosed) ----------------
        for comp in self._components(cells, lambda c: c.is_water or c.is_lake):
            is_ocean = any(cid in border for cid in comp)
            kind = "ocean" if is_ocean else "lake"
            floor = min_ocean if is_ocean else min_lake
            if len(comp) < floor:
                continue
            features.append(self._make(next_id, kind, comp, cells, culture))
            next_id += 1

        # -- land: islands ---------------------------------------------------
        for comp in self._components(cells, lambda c: not c.is_water and not c.is_lake):
            if len(comp) < min_island:
                continue
            features.append(self._make(next_id, "island", comp, cells, culture))
            next_id += 1

        # -- biome bands: ranges & forests -----------------------------------
        range_biomes = {Biome.MOUNTAIN, Biome.HILLS}
        for comp in self._components(
            cells, lambda c: not c.is_water and c.biome in range_biomes
        ):
            if len(comp) < min_range:
                continue
            features.append(self._make(next_id, "range", comp, cells, culture))
            next_id += 1

        for comp in self._components(
            cells, lambda c: not c.is_water and c.biome == Biome.FOREST
        ):
            if len(comp) < min_forest:
                continue
            features.append(self._make(next_id, "forest", comp, cells, culture))
            next_id += 1

        return features

    # -- internals -----------------------------------------------------------

    def _make(self, fid: int, kind: str, comp: list[int], cells, culture: str) -> Feature:
        cx = sum(cells[i].cx for i in comp) / len(comp)
        cy = sum(cells[i].cy for i in comp) / len(comp)
        return Feature(
            id=fid,
            kind=kind,
            name=self._name(kind, culture),
            cells=sorted(comp),
            centroid=(cx, cy),
            area=float(len(comp)),
        )

    def _name(self, kind: str, culture: str) -> str:
        forms = _FEATURE_FORMS[kind]
        # NameGenerator.place() can rarely return "" — never emit an unnamed
        # feature (a form template must always be applied).
        root = self._names.place(culture) or "Uncharted"
        return self._rng.choice(forms).format(name=root)

    @staticmethod
    def _components(cells, predicate):
        """Connected components (over ``neighbors``) of cells matching ``predicate``.

        Cells are visited in id order and each component's members are discovered
        deterministically, so feature ids and names are reproducible for a seed.
        """
        match = [predicate(c) for c in cells]
        seen = [False] * len(cells)
        out: list[list[int]] = []
        for start in range(len(cells)):
            if not match[start] or seen[start]:
                continue
            comp: list[int] = []
            q: deque[int] = deque([start])
            seen[start] = True
            while q:
                cur = q.popleft()
                comp.append(cur)
                for n in cells[cur].neighbors:
                    if match[n] and not seen[n]:
                        seen[n] = True
                        q.append(n)
            out.append(comp)
        return out

    @staticmethod
    def _border_cells(terrain: TerrainResult) -> set[int]:
        """Cell ids whose Voronoi region touches the map frame — exactly the cell
        ids appearing in the outer rows/columns of the rasterised ``cell_of`` grid."""
        grid = terrain.cell_of
        ids: set[int] = set()
        ids.update(int(x) for x in grid[0, :])
        ids.update(int(x) for x in grid[-1, :])
        ids.update(int(x) for x in grid[:, 0])
        ids.update(int(x) for x in grid[:, -1])
        return ids
