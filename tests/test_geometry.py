"""Unit tests for the shared geometry primitives (_geometry).

These are internal, but the terrain/dungeon/settlement tiers all build on them,
so the reusable contract is pinned here directly (not just exercised through a
tier).
"""

import numpy as np

from mapwright._geometry import (
    clip_halfplane,
    convex_hull,
    grid_adjacency,
    inset_convex,
    jittered_grid_seeds,
    nearest_site,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
    voronoi_cells,
    voronoi_grid,
    voronoi_polygons,
)
from mapwright.rng import SeededRNG


class TestSeeds:
    def test_count_and_bounds(self):
        seeds = jittered_grid_seeds(SeededRNG(1), 40, 30, 50)
        assert seeds.shape[0] <= 50  # row-major grid may slightly under-fill
        assert seeds.shape[0] >= 40
        assert (seeds[:, 0] >= 0).all() and (seeds[:, 0] <= 40).all()
        assert (seeds[:, 1] >= 0).all() and (seeds[:, 1] <= 30).all()

    def test_deterministic_for_same_seed(self):
        a = jittered_grid_seeds(SeededRNG(7), 40, 30, 50)
        b = jittered_grid_seeds(SeededRNG(7), 40, 30, 50)
        assert np.array_equal(a, b)

    def test_differs_for_different_seed(self):
        a = jittered_grid_seeds(SeededRNG(1), 40, 30, 50)
        b = jittered_grid_seeds(SeededRNG(2), 40, 30, 50)
        assert not np.array_equal(a, b)


class TestVoronoiGrid:
    def test_shape_and_labels(self):
        seeds = jittered_grid_seeds(SeededRNG(3), 30, 20, 24)
        cell_of, relaxed = voronoi_grid(30, 20, seeds, relax=2)
        assert cell_of.shape == (20, 30)
        assert cell_of.dtype == np.int32
        assert set(np.unique(cell_of)).issubset(set(range(len(seeds))))

    def test_nearest_site_matches_bruteforce(self):
        seeds = np.array([[0.0, 0.0], [10.0, 10.0]])
        coords = np.array([[1.0, 1.0], [9.0, 9.0], [5.0, 4.0]])
        assert list(nearest_site(coords, seeds)) == [0, 1, 0]


class TestAdjacency:
    def test_symmetric_and_sorted(self):
        seeds = jittered_grid_seeds(SeededRNG(5), 30, 20, 24)
        cell_of, _ = voronoi_grid(30, 20, seeds, relax=1)
        adj = grid_adjacency(cell_of, len(seeds))
        assert len(adj) == len(seeds)
        for i, neighbors in enumerate(adj):
            assert neighbors == sorted(neighbors)
            for j in neighbors:
                assert i in adj[j]  # adjacency is mutual


class TestPolygons:
    def test_clip_halfplane_keeps_correct_side(self):
        rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        # Bisector at x=5, normal pointing +x → keep x <= 5.
        clipped = clip_halfplane(rect, 5.0, 5.0, 1.0, 0.0)
        assert all(x <= 5.0 + 1e-6 for x, _ in clipped)
        assert len(clipped) >= 3

    def test_voronoi_polygons_split_two_sites(self):
        # Two sites left/right of x=5 → polygons land on their own side.
        centroids = {0: (2.0, 2.0), 1: (8.0, 2.0)}
        neighbors = {0: [1], 1: [0]}
        polys = voronoi_polygons(centroids, neighbors, 10, 4)
        assert set(polys) == {0, 1}
        assert all(x <= 5.0 + 1e-6 for x, _ in polys[0])
        assert all(x >= 5.0 - 1e-6 for x, _ in polys[1])

    def test_decoupled_from_domain_types(self):
        # Plain dict/tuple input — no TerrainCell needed (the point of extraction).
        polys = voronoi_polygons({0: (5.0, 5.0)}, {0: []}, 10, 10)
        assert len(polys[0]) == 4  # no neighbours → the full map rect


_SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


class TestVoronoiCells:
    def test_one_cell_fills_bounds(self):
        cells = voronoi_cells([(5.0, 5.0)], _SQUARE)
        assert len(cells) == 1
        assert len(cells[0]) == 4  # single point → the whole bounds polygon

    def test_two_points_split_at_bisector(self):
        cells = voronoi_cells([(2.5, 5.0), (7.5, 5.0)], _SQUARE)
        assert all(x <= 5.0 + 1e-6 for x, _ in cells[0])
        assert all(x >= 5.0 - 1e-6 for x, _ in cells[1])

    def test_each_point_lies_in_its_own_cell(self):
        pts = [(2.0, 2.0), (8.0, 3.0), (5.0, 8.0), (3.0, 6.0)]
        cells = voronoi_cells(pts, _SQUARE)
        for p, cell in zip(pts, cells):
            assert point_in_polygon(p, cell)


class TestConvexHull:
    def test_hull_of_square_with_interior_point(self):
        hull = convex_hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)])
        assert set(hull) == {(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)}

    def test_collinear(self):
        assert len(convex_hull([(0, 0), (1, 1)])) == 2


class TestPolygonCentroid:
    def test_square_centroid(self):
        cx, cy = polygon_centroid(_SQUARE)
        assert abs(cx - 5.0) < 1e-9 and abs(cy - 5.0) < 1e-9

    def test_centroid_inside_convex_polygon(self):
        poly = [(0, 0), (10, 0), (12, 6), (6, 11), (-1, 5)]
        assert point_in_polygon(polygon_centroid(poly), poly)


class TestPointInPolygon:
    def test_inside_and_outside(self):
        assert point_in_polygon((5, 5), _SQUARE)
        assert not point_in_polygon((15, 5), _SQUARE)
        assert not point_in_polygon((-1, -1), _SQUARE)


class TestPolygonArea:
    def test_square_area(self):
        assert abs(polygon_area(_SQUARE) - 100.0) < 1e-9

    def test_triangle_area(self):
        assert abs(polygon_area([(0, 0), (4, 0), (0, 3)]) - 6.0) < 1e-9

    def test_degenerate(self):
        assert polygon_area([(0, 0), (1, 1)]) == 0.0


class TestInsetConvex:
    def test_inset_shrinks_area(self):
        inset = inset_convex(_SQUARE, 1.0)
        # 10x10 inset by 1 on all sides → 8x8.
        assert abs(polygon_area(inset) - 64.0) < 1e-6
        assert all(1.0 - 1e-6 <= x <= 9.0 + 1e-6 for x, _ in inset)

    def test_inset_stays_inside(self):
        inset = inset_convex(_SQUARE, 2.0)
        for p in inset:
            assert point_in_polygon(p, _SQUARE)

    def test_too_large_inset_collapses(self):
        assert inset_convex(_SQUARE, 6.0) == []  # >half the 10-wide square

    def test_exact_half_width_collapses_not_degenerate_quad(self):
        # Regression: insetting a 2x2 square by exactly 1 used to return four
        # coincident points (len 4, area 0); it must collapse to [] instead.
        square2 = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        result = inset_convex(square2, 1.0)
        assert result == [] or polygon_area(result) > 1e-9

    def test_nonpositive_dist_unchanged(self):
        assert inset_convex(_SQUARE, 0.0) == _SQUARE
