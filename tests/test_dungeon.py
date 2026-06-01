"""Unit tests for the BSP + MST dungeon generator."""

from collections import deque

from mapwright import DungeonConfig, DungeonGenerator, Rect, SeededRNG


def _gen(seed=1, w=60, h=40, cfg=None):
    return DungeonGenerator(SeededRNG(seed)).generate(w, h, config=cfg)


class TestStructure:
    def test_produces_multiple_rooms(self):
        d = _gen()
        assert len(d.rooms) >= 4

    def test_rooms_within_bounds(self):
        d = _gen()
        for r in d.rooms:
            assert r.x >= 0 and r.y >= 0
            assert r.x + r.w <= d.width and r.y + r.h <= d.height
            assert r.w >= 3 and r.h >= 3

    def test_rooms_do_not_overlap(self):
        # BSP places one room per leaf → rooms never overlap.
        d = _gen()
        for i, a in enumerate(d.rooms):
            for b in d.rooms[i + 1:]:
                assert not a.intersects(b), "rooms overlap"

    def test_grid_matches_dimensions(self):
        d = _gen(w=48, h=32)
        assert d.grid.shape == (32, 48)
        assert d.grid.any()  # some floor exists


class TestConnectivity:
    def test_all_rooms_reachable(self):
        # Flood-fill the floor from the first room; every room center must be hit.
        d = _gen(seed=7)
        grid = d.grid
        start = d.rooms[0].center
        seen = set()
        q = deque([start])
        while q:
            x, y = q.popleft()
            if (x, y) in seen:
                continue
            if not (0 <= x < d.width and 0 <= y < d.height) or not grid[y, x]:
                continue
            seen.add((x, y))
            q.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
        for r in d.rooms:
            assert r.center in seen, f"room at {r.center} unreachable"

    def test_mst_connects_all_rooms(self):
        d = _gen(seed=7)
        # The spanning tree alone must touch every room (>= n-1 edges, all linked).
        n = len(d.rooms)
        adj = {i: set() for i in range(n)}
        for i, j in d.edges:
            adj[i].add(j)
            adj[j].add(i)
        seen, q = set(), deque([0])
        while q:
            u = q.popleft()
            if u in seen:
                continue
            seen.add(u)
            q.extend(adj[u] - seen)
        assert len(seen) == n


class TestDeterminism:
    def test_same_seed_same_dungeon(self):
        a, b = _gen(42), _gen(42)
        assert (a.grid == b.grid).all()
        assert [r.center for r in a.rooms] == [r.center for r in b.rooms]

    def test_different_seed_differs(self):
        assert not (_gen(1).grid == _gen(2).grid).all()


class TestConfig:
    def test_smaller_leaves_make_more_rooms(self):
        big = _gen(seed=3, cfg=DungeonConfig(min_leaf=16))
        small = _gen(seed=3, cfg=DungeonConfig(min_leaf=7))
        assert len(small.rooms) > len(big.rooms)

    def test_ascii_render(self):
        d = _gen(w=30, h=20)
        art = d.ascii()
        assert art.count("\n") == 19  # 20 rows
        assert "." in art and "#" in art


class TestRect:
    def test_center_and_intersect(self):
        a = Rect(0, 0, 4, 4)
        assert a.center == (2, 2)
        assert a.intersects(Rect(2, 2, 4, 4))
        assert not a.intersects(Rect(10, 10, 2, 2))
        assert a.intersects(Rect(5, 0, 2, 2), pad=2)  # padding makes them touch
