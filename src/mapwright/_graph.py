"""Internal graph primitives shared across generation tiers.

A Prim minimum-spanning-tree over a dense point set (connecting dungeon rooms,
settlement wards), and an A* shortest path over an arbitrary graph (routing
regional roads over the terrain cell graph). Decoupled from any domain type:
callers pass node counts / neighbour + cost callbacks, so the routines serve any
"connect these minimally" or "cheapest path" need.
"""

from __future__ import annotations

import heapq
from typing import Callable, Hashable, Iterable


def prim_mst(n: int, dist2: Callable[[int, int], float]) -> list[tuple[int, int]]:
    """Edges of a minimum spanning tree over ``n`` nodes (Prim's algorithm).

    ``dist2(i, j)`` returns the edge weight between nodes ``i`` and ``j`` (squared
    Euclidean distance is fine — only ordering matters). Returns ``n - 1`` edges
    as ``(in_tree, newly_added)`` index pairs, or ``[]`` for ``n < 2``. Dense
    O(n²) per step; intended for small node counts (rooms, wards).
    """
    if n < 2:
        return []
    edges: list[tuple[int, int]] = []
    in_tree = {0}
    while len(in_tree) < n:
        best = None
        best_d = None
        for i in in_tree:
            for j in range(n):
                if j in in_tree:
                    continue
                d = dist2(i, j)
                if best_d is None or d < best_d:
                    best_d, best = d, (i, j)
        i, j = best
        in_tree.add(j)
        edges.append((i, j))
    return edges


def astar(
    start: Hashable,
    goal: Hashable,
    neighbors: Callable[[Hashable], Iterable[Hashable]],
    cost: Callable[[Hashable, Hashable], float],
    heuristic: Callable[[Hashable], float],
) -> list:
    """A* shortest path from ``start`` to ``goal``.

    ``neighbors(n)`` yields adjacent nodes, ``cost(a, b) >= 0`` is the edge
    weight, and ``heuristic(n) >= 0`` must be admissible (never overestimate the
    remaining cost) for the result to be optimal. Returns the node path
    ``[start, …, goal]``, or ``[]`` if ``goal`` is unreachable. Nodes must be
    hashable and orderable (ties in the frontier fall back to node comparison).
    """
    open_heap: list = [(heuristic(start), 0.0, start)]
    came: dict = {start: None}
    best_g: dict = {start: 0.0}
    while open_heap:
        _, gc, cur = heapq.heappop(open_heap)
        if cur == goal:
            path = [cur]
            while came[path[-1]] is not None:
                path.append(came[path[-1]])
            path.reverse()
            return path
        if gc > best_g.get(cur, float("inf")):
            continue  # stale heap entry
        for nb in neighbors(cur):
            ng = gc + cost(cur, nb)
            if ng < best_g.get(nb, float("inf")):
                best_g[nb] = ng
                came[nb] = cur
                heapq.heappush(open_heap, (ng + heuristic(nb), ng, nb))
    return []
