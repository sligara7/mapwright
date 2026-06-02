"""Regional roads / trade routes between settlements.

Given a :class:`~mapwright.terrain.TerrainResult` and a set of settlement sites,
connects them with a road network: a minimum-spanning tree over the sites
(so every settlement is reachable) whose edges are routed as terrain-aware
shortest paths (A*) over the Voronoi cell graph — preferring flat land, avoiding
the sea, and paying a crossing cost at rivers and steep slopes.

Domain-neutral: ``generate`` takes plain ``(x, y)`` sites (a host maps its own
settlements / :class:`~mapwright.svg_renderer.Marker` positions onto them) and
returns :class:`Road` objects as lists of cell ids. Deterministic — the routing
depends only on the terrain and the sites.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._graph import astar, prim_mst
from .terrain import TerrainResult

# Routing penalties (multipliers on straight-line step distance).
_SEA_PENALTY = 8.0      # roads shun open water (a strait crossing is costly)
_LAKE_PENALTY = 6.0
_RIVER_PENALTY = 2.0    # a bridge/ford
_SLOPE_PENALTY = 4.0    # per unit of uphill height gain


@dataclass
class Road:
    """A road as an ordered list of terrain cell ids (route through their centroids)."""

    cells: list[int]

    def to_dict(self) -> dict:
        return {"cells": list(self.cells)}

    @classmethod
    def from_dict(cls, data: dict) -> "Road":
        return cls(cells=[int(c) for c in data["cells"]])


class RegionalRoadGenerator:
    """Builds a road network connecting settlement sites over a terrain map."""

    def generate(self, terrain: TerrainResult, sites: list[tuple[float, float]]) -> list[Road]:
        """Connect ``sites`` (``(x, y)`` points) with terrain-routed roads.

        Sites are snapped to their nearest land cell; the settlements are linked
        by a minimum spanning tree (straight-line topology), and each link is then
        routed cell-to-cell with A* over the terrain. Returns one :class:`Road`
        per tree edge (empty if fewer than two sites land on the map).
        """
        cells = terrain.cells
        land = [c for c in cells if not c.is_water]
        if not land or len(sites) < 2:
            return []

        nodes = [self._nearest_land(land, x, y) for x, y in sites]
        # Collapse sites that snap to the same cell (and keep ≥2 distinct).
        nodes = list(dict.fromkeys(nodes))
        if len(nodes) < 2:
            return []

        centers = [(cells[i].cx, cells[i].cy) for i in nodes]

        def dist2(i: int, j: int) -> float:
            (ax, ay), (bx, by) = centers[i], centers[j]
            return (ax - bx) ** 2 + (ay - by) ** 2

        roads: list[Road] = []
        for i, j in prim_mst(len(nodes), dist2):
            path = self._route(terrain, nodes[i], nodes[j])
            if len(path) >= 2:
                roads.append(Road(path))
        return roads

    # -- internals -------------------------------------------------------

    @staticmethod
    def _nearest_land(land, x: float, y: float) -> int:
        return min(land, key=lambda c: (c.cx - x) ** 2 + (c.cy - y) ** 2).id

    def _route(self, terrain: TerrainResult, start: int, goal: int) -> list[int]:
        cells = terrain.cells
        gx, gy = cells[goal].cx, cells[goal].cy

        def neighbors(n):
            return cells[n].neighbors

        def cost(a: int, b: int) -> float:
            ca, cb = cells[a], cells[b]
            step = math.hypot(ca.cx - cb.cx, ca.cy - cb.cy) or 1.0
            penalty = 1.0
            if cb.is_water:
                penalty += _SEA_PENALTY
            if cb.is_lake:
                penalty += _LAKE_PENALTY
            if cb.is_river:
                penalty += _RIVER_PENALTY
            penalty += _SLOPE_PENALTY * max(0.0, cb.height - ca.height)
            return step * penalty

        def heuristic(n) -> float:
            # Straight-line distance ≤ any real (penalty ≥ 1) path cost → admissible.
            return math.hypot(cells[n].cx - gx, cells[n].cy - gy)

        return astar(start, goal, neighbors, cost, heuristic)
