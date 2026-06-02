"""Unit tests for the shared graph primitives (_graph)."""

import math

from mapwright._graph import astar, prim_mst


def _path_dist2(points):
    def dist2(i, j):
        (ax, ay), (bx, by) = points[i], points[j]
        return (ax - bx) ** 2 + (ay - by) ** 2
    return dist2


class TestPrimMST:
    def test_empty_and_single(self):
        assert prim_mst(0, lambda i, j: 0) == []
        assert prim_mst(1, lambda i, j: 0) == []

    def test_returns_spanning_tree(self):
        pts = [(0, 0), (1, 0), (5, 0), (5, 5)]
        edges = prim_mst(len(pts), _path_dist2(pts))
        assert len(edges) == len(pts) - 1          # n-1 edges
        nodes = {n for e in edges for n in e}
        assert nodes == set(range(len(pts)))       # every node connected

    def test_picks_minimal_edges_on_a_line(self):
        # Collinear points → MST is the chain of nearest neighbours.
        pts = [(0, 0), (1, 0), (5, 0)]
        edges = prim_mst(len(pts), _path_dist2(pts))
        assert edges == [(0, 1), (1, 2)]

    def test_no_cycles(self):
        pts = [(0, 0), (2, 1), (1, 3), (4, 4), (3, 0)]
        edges = prim_mst(len(pts), _path_dist2(pts))
        # A tree on n nodes has exactly n-1 edges and connects all of them.
        assert len(edges) == len(pts) - 1
        assert len({n for e in edges for n in e}) == len(pts)

    def test_deterministic(self):
        pts = [(0, 0), (2, 1), (1, 3), (4, 4), (3, 0)]
        d = _path_dist2(pts)
        assert prim_mst(len(pts), d) == prim_mst(len(pts), d)


# A 4-connected integer grid, optionally with blocked cells, for A* tests.
def _grid(width, height, blocked=frozenset()):
    pts = {(x, y) for x in range(width) for y in range(height)} - set(blocked)

    def neighbors(n):
        x, y = n
        return [p for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)) if p in pts]

    def cost(a, b):
        return 1.0

    return neighbors, cost


class TestAStar:
    def test_straight_path_length(self):
        neighbors, cost = _grid(5, 1)
        path = astar((0, 0), (4, 0), neighbors, cost, lambda n: abs(n[0] - 4))
        assert path[0] == (0, 0) and path[-1] == (4, 0)
        assert len(path) == 5  # 0,1,2,3,4

    def test_finds_shortest_around_wall(self):
        # Wall blocks x=2 except a gap at (2,0); shortest must detour through it.
        blocked = {(2, 1), (2, 2)}
        neighbors, cost = _grid(5, 3, blocked)

        def h(n):
            return abs(n[0] - 4) + abs(n[1] - 1)

        path = astar((0, 1), (4, 1), neighbors, cost, h)
        assert path[0] == (0, 1) and path[-1] == (4, 1)
        assert (2, 0) in path                 # routed through the gap
        assert all(p not in blocked for p in path)

    def test_unreachable_returns_empty(self):
        # Goal fully walled off.
        blocked = {(1, 0), (1, 1), (1, 2), (0, 2), (2, 2)}
        neighbors, cost = _grid(3, 3, blocked)
        assert astar((0, 0), (2, 0), neighbors, cost, lambda n: 0.0) == []

    def test_start_equals_goal(self):
        neighbors, cost = _grid(3, 3)
        assert astar((1, 1), (1, 1), neighbors, cost, lambda n: 0.0) == [(1, 1)]

    def test_respects_edge_cost(self):
        # Make the direct row expensive so the path detours via y=1.
        neighbors, _ = _grid(3, 2)

        def cost(a, b):
            return 10.0 if (a[1] == 0 and b[1] == 0) else 1.0

        path = astar((0, 0), (2, 0), neighbors, cost,
                     lambda n: math.hypot(n[0] - 2, n[1]))
        assert (1, 1) in path  # detoured off the costly row
