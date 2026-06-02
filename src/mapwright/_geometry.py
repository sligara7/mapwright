"""Internal geometry primitives shared across generation tiers.

Voronoi construction (jittered seeds → nearest-site assignment → Lloyd relaxation
→ grid adjacency) and convex-polygon reconstruction (perpendicular-bisector
half-plane clipping), decoupled from any domain type. The terrain, dungeon, and
settlement tiers all build on this instead of re-implementing it.

Pure numpy + math; no runtime mapwright imports (so it's safe to import from
anywhere in the package, and free of import cycles). All randomness flows through
a caller-supplied :class:`~mapwright.rng.SeededRNG`, so callers keep full control
of the seed stream and reproducibility.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .rng import SeededRNG

Point = tuple[float, float]


# -- Voronoi cell construction ------------------------------------------------

def jittered_grid_seeds(rng: "SeededRNG", width: int, height: int, n: int) -> np.ndarray:
    """``n`` jittered-grid seed points over ``width×height`` — even coverage
    without clumping. Draws two ``rng.random()`` values (x then y jitter) per grid
    cell, row-major, so the seed stream is fixed for a given seed."""
    cols = max(1, int(round(math.sqrt(n * width / max(1, height)))))
    rows = max(1, int(math.ceil(n / cols)))
    cw, ch = width / cols, height / rows
    pts = []
    for r in range(rows):
        for c in range(cols):
            jx = rng.random()
            jy = rng.random()
            pts.append(((c + jx) * cw, (r + jy) * ch))
    return np.array(pts[:n] if len(pts) >= n else pts, dtype=float)


def nearest_site(coords: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    """Nearest-seed index per coordinate, computed in blocks to bound memory."""
    p, n = coords.shape[0], seeds.shape[0]
    out = np.empty(p, dtype=np.int32)
    block = max(1, int(4_000_000 / max(1, n)))  # ~4M float cap per block
    for start in range(0, p, block):
        chunk = coords[start : start + block]
        d2 = ((chunk[:, None, :] - seeds[None, :, :]) ** 2).sum(axis=2)
        out[start : start + block] = d2.argmin(axis=1)
    return out


def voronoi_grid(
    width: int, height: int, seeds: np.ndarray, relax: int
) -> tuple[np.ndarray, np.ndarray]:
    """Rasterise a Voronoi diagram onto the integer ``width×height`` grid.

    Assigns each grid cell to its nearest seed, then applies ``relax`` Lloyd
    passes (move each seed to its region centroid, reassign). Returns
    ``(cell_of, relaxed_seeds)`` where ``cell_of`` is an int grid ``[height, width]``.
    """
    xs, ys = np.meshgrid(np.arange(width), np.arange(height))
    coords = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(float)
    cell_of = nearest_site(coords, seeds).reshape(height, width)

    for _ in range(relax):
        new_seeds = seeds.copy()
        flat = cell_of.ravel()
        for cid in range(len(seeds)):
            mask = flat == cid
            if mask.any():
                new_seeds[cid] = coords[mask].mean(axis=0)
        seeds = new_seeds
        cell_of = nearest_site(coords, seeds).reshape(height, width)
    return cell_of, seeds


def grid_adjacency(cell_of: np.ndarray, n_sites: int) -> list[list[int]]:
    """Sorted neighbour ids per site: two sites are adjacent if their regions touch
    horizontally or vertically in ``cell_of``."""
    neigh: list[set[int]] = [set() for _ in range(n_sites)]
    for a, b in ((cell_of[:, :-1], cell_of[:, 1:]), (cell_of[:-1, :], cell_of[1:, :])):
        for u, v in np.unique(np.stack([a.ravel(), b.ravel()], axis=1), axis=0):
            if u != v:
                neigh[u].add(int(v))
                neigh[v].add(int(u))
    return [sorted(s) for s in neigh]


# -- Convex polygon reconstruction (half-plane clipping) ----------------------
#
# The cell graph stores only centroids + adjacency, so for vector output we
# rebuild each cell's convex polygon by clipping the map rectangle with the
# perpendicular bisector between the cell and each neighbour (Sutherland–Hodgman
# half-plane clipping). Pure Python, no scipy — exact for relaxed seed sites.

def clip_halfplane(
    poly: list[Point], mx: float, my: float, ax: float, ay: float
) -> list[Point]:
    """Keep the part of ``poly`` on the cell's side of a bisector.

    The half-plane is ``{p : (p - m)·a <= 0}`` where ``m`` is the bisector
    midpoint and ``a`` points from the cell toward its neighbour.
    """
    def inside(p: Point) -> bool:
        return (p[0] - mx) * ax + (p[1] - my) * ay <= 1e-9

    def intersect(a: Point, b: Point) -> Point:
        dx, dy = b[0] - a[0], b[1] - a[1]
        denom = dx * ax + dy * ay
        if abs(denom) < 1e-12:
            return a
        t = ((mx - a[0]) * ax + (my - a[1]) * ay) / denom
        return (a[0] + t * dx, a[1] + t * dy)

    out: list[Point] = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        a_in, b_in = inside(a), inside(b)
        if a_in:
            out.append(a)
        if a_in != b_in:
            out.append(intersect(a, b))
    return out


def voronoi_polygons(
    centroids: dict[int, Point], neighbors: dict[int, list[int]], width: int, height: int
) -> dict[int, list[Point]]:
    """Convex polygon (list of points) for each site, clipped to the map rect.

    ``centroids[id] = (x, y)`` and ``neighbors[id] = [ids]``; clipping each site's
    rectangle against the bisector to every neighbour yields its Voronoi polygon.
    """
    rect: list[Point] = [(0.0, 0.0), (float(width), 0.0),
                         (float(width), float(height)), (0.0, float(height))]
    polys: dict[int, list[Point]] = {}
    for cid, (cx, cy) in centroids.items():
        poly = rect
        for nid in neighbors[cid]:
            nx, ny = centroids[nid]
            mx, my = (cx + nx) / 2, (cy + ny) / 2
            poly = clip_halfplane(poly, mx, my, nx - cx, ny - cy)
            if len(poly) < 3:
                break
        polys[cid] = poly
    return polys


def voronoi_cells(points: list[Point], bounds: list[Point]) -> list[list[Point]]:
    """Exact Voronoi polygon per point, clipped to the convex ``bounds`` polygon.

    No neighbour graph or raster needed: each cell is ``bounds`` clipped by the
    bisector half-plane against *every* other point (O(n²) — intended for small
    point sets like settlement wards). ``bounds`` must be convex, so each cell
    stays convex. Returns one polygon (possibly empty) per input point, in order.
    """
    cells: list[list[Point]] = []
    for i, (px, py) in enumerate(points):
        poly = list(bounds)
        for j, (qx, qy) in enumerate(points):
            if i == j:
                continue
            mx, my = (px + qx) / 2.0, (py + qy) / 2.0
            poly = clip_halfplane(poly, mx, my, qx - px, qy - py)
            if len(poly) < 3:
                break
        cells.append(poly)
    return cells


# -- small polygon utilities (shared by wards/lots/walls) ---------------------

def convex_hull(points: list[Point]) -> list[Point]:
    """Convex hull (counter-clockwise) of ``points`` via Andrew's monotone chain."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def polygon_centroid(poly: list[Point]) -> Point:
    """Area-weighted centroid of a simple polygon (falls back to the vertex mean
    for degenerate/zero-area input)."""
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(x for x, _ in poly) / n, sum(y for _, y in poly) / n)
    a = cx = cy = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return (sum(x for x, _ in poly) / n, sum(y for _, y in poly) / n)
    return (cx / (3 * a), cy / (3 * a))


def point_in_polygon(pt: Point, poly: list[Point]) -> bool:
    """True if ``pt`` lies inside ``poly`` (ray-casting; works for any simple
    polygon, convex or not)."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def polygon_area(poly: list[Point]) -> float:
    """Unsigned area of a simple polygon (shoelace)."""
    n = len(poly)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def inset_convex(poly: list[Point], dist: float) -> list[Point]:
    """Shrink a convex polygon inward by ``dist`` (every edge moved toward the
    interior). Returns ``[]`` if the inset collapses the polygon (``dist`` too
    large for its size). For ``dist <= 0`` the polygon is returned unchanged."""
    if len(poly) < 3 or dist <= 0:
        return list(poly)
    cx, cy = polygon_centroid(poly)
    out = list(poly)
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        length = math.hypot(ex, ey)
        if length < 1e-12:
            continue
        # Inward normal (perpendicular to the edge, oriented toward the centroid).
        nx, ny = ey / length, -ex / length
        if (cx - ax) * nx + (cy - ay) * ny < 0:
            nx, ny = -nx, -ny
        # Move the edge inward by `dist`, then keep the interior side.
        mx, my = ax + nx * dist, ay + ny * dist
        out = clip_halfplane(out, mx, my, -nx, -ny)
        if len(out) < 3:
            return []
    # A collapse to (near-)zero area can survive as coincident vertices with
    # len >= 3; treat that as collapsed too.
    if polygon_area(out) <= 1e-9:
        return []
    return out
