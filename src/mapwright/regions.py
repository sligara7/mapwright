"""Region / faction assignment over regional terrain.

Partitions the land of a :class:`~mapwright.terrain.TerrainResult` into named
territories: a handful of well-spread capital cells (farthest-point sampling)
seed a multi-source flood fill over the land-cell graph, so every reachable land
cell joins its nearest capital's region — a graph-Voronoi partition that the sea
cleanly divides. Each region is named with the Markov :class:`NameGenerator`.

Domain-neutral: returns :class:`Region` objects (a name, a capital cell id, and
the member cell ids); a host can colour, border, or label them however it likes.
Seed-deterministic via :class:`SeededRNG`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .names import NameGenerator
from .rng import SeededRNG
from .terrain import TerrainResult


@dataclass
class Region:
    """A named territory: its capital cell and the land cells it contains."""

    id: int
    name: str
    capital: int        # cell id of the region's seat
    cells: list[int]    # member cell ids (capital included)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "capital": self.capital,
                "cells": list(self.cells)}

    @classmethod
    def from_dict(cls, data: dict) -> "Region":
        return cls(
            id=int(data["id"]),
            name=data["name"],
            capital=int(data["capital"]),
            cells=[int(c) for c in data["cells"]],
        )


class RegionGenerator:
    """Partitions a terrain map's land into named regions."""

    def __init__(self, rng: SeededRNG):
        self._rng = rng.derive("regions")
        self._names = NameGenerator(self._rng.derive("names"))

    def generate(
        self, terrain: TerrainResult, count: int | None = None, *, culture: str = "generic"
    ) -> list[Region]:
        """Partition the land into ``count`` regions (auto-scaled with land area if
        ``count`` is None). ``culture`` selects the namebase for region names.

        Returns ``[]`` for ``count <= 0`` (or no land). Regions cover the land
        *reachable from a capital*; with fewer regions than separate landmasses,
        the unclaimed landmasses are simply left without a region (a single faction
        does not span open sea)."""
        cells = terrain.cells
        land = [c for c in cells if not c.is_water]
        if not land:
            return []
        if count is None:
            count = max(2, min(12, round((len(land) / 22) ** 0.5)))
        elif count <= 0:
            return []  # zero regions requested → none (not silently coerced to 1)
        count = min(count, len(land))

        capitals = self._pick_capitals(terrain, land, count)
        label = self._flood(terrain, capitals)  # cell id → capital index

        regions: list[Region] = []
        for idx, cap in enumerate(capitals):
            members = sorted(cid for cid, lab in label.items() if lab == idx)
            regions.append(Region(idx, self._names.region(culture), cap, members))
        return regions

    # -- internals -------------------------------------------------------

    def _pick_capitals(self, terrain: TerrainResult, land, count: int) -> list[int]:
        """Farthest-point sampling: a random first seat, then each next capital is
        the land cell farthest (by land-graph distance) from the chosen ones, so
        territories spread out and separate landmasses each get a seat."""
        land_ids = [c.id for c in land]
        capitals = [self._rng.choice(land_ids)]
        far = 10 ** 9
        while len(capitals) < count:
            dist = self._bfs(terrain, capitals)
            cand = max(land_ids, key=lambda i: dist.get(i, far))
            if cand in capitals:
                break  # everything already reached (more capitals than components allow)
            capitals.append(cand)
        return capitals

    @staticmethod
    def _bfs(terrain: TerrainResult, sources: list[int]) -> dict[int, int]:
        """Multi-source hop distance from ``sources`` over land adjacency."""
        cells = terrain.cells
        dist: dict[int, int] = {s: 0 for s in sources}
        q: deque[int] = deque(sources)
        while q:
            cur = q.popleft()
            for n in cells[cur].neighbors:
                if not cells[n].is_water and n not in dist:
                    dist[n] = dist[cur] + 1
                    q.append(n)
        return dist

    @staticmethod
    def _flood(terrain: TerrainResult, capitals: list[int]) -> dict[int, int]:
        """Label each reachable land cell with the index of the nearest capital
        (simultaneous multi-source BFS = graph Voronoi)."""
        cells = terrain.cells
        label: dict[int, int] = {}
        q: deque[int] = deque()
        for idx, cap in enumerate(capitals):
            label[cap] = idx
            q.append(cap)
        while q:
            cur = q.popleft()
            for n in cells[cur].neighbors:
                if not cells[n].is_water and n not in label:
                    label[n] = label[cur]
                    q.append(n)
        return label
