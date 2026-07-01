"""Spherical plate-tectonics base heightmap — numpy-only, seed-deterministic.

mapwright's default terrain lays continents down from smooth noise, which reads
as rounded *blobs*. Real continents get their shape from plates that have drifted
and collided: crumpled mountain belts along collision margins, ragged rifted
coasts, ocean basins between them. This module produces that base shape.

It runs a small spherical plate simulation and returns **one snapshot** — an
equirectangular signed-altitude grid (``> 0`` land, ``< 0`` sea floor). The whole
point is the *snapshot*, not the animation: we drift the plates internally just
far enough to build realistic morphology, keep the final field, and discard the
rest. A host (``RegionalTerrainGenerator``) uses that grid as its base elevation
and runs the usual erosion / rivers / climate / biomes on top.

Design notes:
* **numpy-only** — no scipy. The neighbour search (``_radius_neighbours``) is a
  3-D spatial hash; the resample to a lon/lat grid is a splat + hole-fill. This
  keeps mapwright's single-dependency promise and its byte-identical determinism
  (scipy's cKDTree/griddata can drift across versions).
* **deterministic** — all randomness comes from a :class:`SeededRNG`.
* Ported from the author's own ``genmap`` engine (his work). Techniques:
  Fibonacci-sphere nodes, warped-Voronoi plates, Euler-pole + Rodrigues plate
  motion, and boundary geomorphology (convergent uplift, subduction consumption,
  divergent rift insertion) with forcing smoothing.
"""

from __future__ import annotations

import numpy as np

from .rng import SeededRNG

# World-shape presets: how the initial continents are seeded. ``land_freq`` is
# the frequency of the continentality field — low clumps land into one
# supercontinent, high scatters it into isles.
WORLD_STYLES: dict[str, dict] = {
    "random":      dict(p_land=0.45, land_freq=3.2, relief=0.5, relief_freq=6.0),
    "pangea":      dict(p_land=0.35, land_freq=1.0, relief=0.35, relief_freq=5.0),
    "continents":  dict(p_land=0.38, land_freq=2.2, relief=0.40, relief_freq=6.0),
    "archipelago": dict(p_land=0.30, land_freq=5.0, relief=1.0, relief_freq=12.0),
    "ocean":       dict(p_land=0.18, land_freq=2.5, relief=0.5, relief_freq=6.0),
}


# ---------------------------------------------------------------------------
# Sphere nodes, noise, seeds
# ---------------------------------------------------------------------------
def _fibonacci_sphere(res: int) -> np.ndarray:
    """Near-uniform points on the unit sphere (Fibonacci lattice)."""
    n = res * res
    i = np.arange(n)
    z = 1.0 - 2.0 * (i + 0.5) / n
    r = np.sqrt(np.clip(1.0 - z * z, 0.0, 1.0))
    theta = np.pi * (3.0 - np.sqrt(5.0)) * i          # golden angle
    return np.column_stack([r * np.cos(theta), r * np.sin(theta), z])


def _fourier_noise(pos: np.ndarray, gen: np.random.Generator,
                   n_waves: int = 8, freq: float = 3.0) -> np.ndarray:
    """Smooth pseudo-random scalar field on the sphere (~[-1, 1]).

    A sum of random plane waves (random Fourier features) — cheap gradient noise
    with no external dependency.
    """
    field = np.zeros(len(pos))
    for _ in range(n_waves):
        w = gen.normal(size=3)
        w = w / np.linalg.norm(w) * gen.uniform(0.8, freq)
        field += gen.uniform(-1.0, 1.0) * np.sin(pos @ w + gen.uniform(0, 2 * np.pi))
    return field / np.sqrt(n_waves)


def _farthest_point_seeds(pos: np.ndarray, n: int,
                          gen: np.random.Generator) -> np.ndarray:
    """Pick n well-spread seed points (greedy farthest-point sampling)."""
    idx = [int(gen.integers(len(pos)))]
    d = 1.0 - pos @ pos[idx[0]]                        # 1 - cos(angle)
    for _ in range(n - 1):
        i = int(np.argmax(d))
        idx.append(i)
        d = np.minimum(d, 1.0 - pos @ pos[i])
    return pos[idx]


# ---------------------------------------------------------------------------
# Plates + initial elevation
# ---------------------------------------------------------------------------
def _build_plates(pos, n_plates, gen, warp, world, land):
    """Carve the globe into warped-Voronoi plates and paint land vs ocean.

    Plate *shapes* (the moving rigid units) are warped-Voronoi cells; the
    land/ocean *pattern* is a separate elevation field, so a plate carries both
    continental and oceanic crust (as real plates do). Returns ``(plate,
    density, alt)``.
    """
    cfg = dict(WORLD_STYLES[world])
    if land is not None:
        cfg["p_land"] = float(np.clip(land, 0.0, 1.0))

    seeds = _farthest_point_seeds(pos, n_plates, gen)
    disp = np.column_stack([_fourier_noise(pos, gen, freq=2.2) for _ in range(3)])
    warped = pos + warp * disp
    plate = np.argmax(warped @ seeds.T, axis=1)        # nearest warped seed

    elev = (_fourier_noise(pos, gen, freq=cfg["land_freq"], n_waves=10)
            + cfg["relief"] * _fourier_noise(pos, gen, freq=cfg["relief_freq"]))
    sea = np.quantile(elev, 1.0 - cfg["p_land"])       # land area ~ p_land
    alt = elev - sea                                   # >0 land, <0 sea floor

    # Continental crust (dry land) is lighter and rides over subducting oceanic.
    density = np.where(alt >= 0,
                       gen.normal(2.7, 0.05, len(pos)),   # granitic-ish
                       gen.normal(3.0, 0.05, len(pos)))   # basaltic-ish
    return plate, density, alt


def _euler_poles(n_plates, gen, steps, max_angle=1.6):
    """Random rotation axis (unit) + angular speed (rad/step) per plate.

    Speeds are scaled so a plate sweeps at most ``max_angle`` over the whole run
    — dramatic drift, not a blur of full revolutions.
    """
    axes = gen.normal(size=(n_plates, 3))
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    omega = gen.uniform(-1.0, 1.0, n_plates) * max_angle / max(steps, 1)
    return axes, omega


def _rodrigues(v, k, alpha):
    """Rotate rows of v around unit axis k by angle alpha (Rodrigues)."""
    ca, sa = np.cos(alpha), np.sin(alpha)
    kxv = np.cross(k, v)
    kdv = v @ k
    return v * ca + kxv * sa + np.outer(kdv * (1 - ca), k)


def _plate_velocity(pos, plate, axes, omega):
    """Instantaneous surface velocity V = omega * (k x v) for every node."""
    vel = np.zeros_like(pos)
    for p in range(len(omega)):
        m = plate == p
        if m.any():
            vel[m] = omega[p] * np.cross(axes[p], pos[m])
    return vel


# ---------------------------------------------------------------------------
# Neighbour search — numpy 3-D spatial hash (replaces scipy cKDTree)
# ---------------------------------------------------------------------------
def _radius_neighbours(pos: np.ndarray, r: float):
    """All ordered node pairs within Euclidean distance ``r``.

    Returns ``(src, dst, d2)`` int/float arrays: for every node, an edge to each
    other node within ``r`` (self excluded). Implemented as a voxel spatial hash
    (bin size ``r``, search the 27 neighbouring bins), fully vectorised bar a
    27-iteration loop over bin offsets — no scipy.
    """
    n = len(pos)
    if n < 2:
        empty_i = np.empty(0, dtype=np.int64)
        return empty_i, empty_i.copy(), np.empty(0)
    b = np.floor(pos / r).astype(np.int64)
    b = b - b.min(axis=0) + 1                          # shift so -1 offsets stay >= 0
    dims = b.max(axis=0) + 2                            # all neighbour coords < dims

    def lin(bc):                                        # mixed-radix bin id (collision-free)
        return (bc[:, 0] * dims[1] + bc[:, 1]) * dims[2] + bc[:, 2]

    bid = lin(b)
    order = np.argsort(bid, kind="stable")             # nodes grouped by bin
    bid_sorted = bid[order]
    uniq, first = np.unique(bid_sorted, return_index=True)
    last = np.append(first[1:], n)                      # end offsets into `order`

    src_parts, dst_parts = [], []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1)
               for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for off in offsets:
        nb = lin(b + off)                              # each node's neighbour-bin id
        p = np.searchsorted(uniq, nb)
        p_clip = np.clip(p, 0, len(uniq) - 1)
        hit = uniq[p_clip] == nb                       # neighbour bin is occupied
        nodes = np.nonzero(hit)[0]
        if not len(nodes):
            continue
        pp = p_clip[nodes]
        seg0, seg1 = first[pp], last[pp]
        counts = seg1 - seg0
        total = int(counts.sum())
        if not total:
            continue
        # ragged expansion: for each hit node, all members of that neighbour bin
        base = np.repeat(np.cumsum(counts) - counts, counts)
        within = np.arange(total) - base
        dst_parts.append(order[np.repeat(seg0, counts) + within])
        src_parts.append(np.repeat(nodes, counts))

    src = np.concatenate(src_parts)
    dst = np.concatenate(dst_parts)
    diff = pos[src] - pos[dst]
    d2 = np.einsum("ij,ij->i", diff, diff)
    keep = (d2 < r * r) & (src != dst)
    return src[keep], dst[keep], d2[keep]


def _nearest_foreign(src, dst, d2, plate):
    """Per source node, its nearest neighbour on a *different* plate.

    Returns ``(gi, gj, nd)`` — reacting node ids, their nearest foreign
    neighbour ids, and the distance to it.
    """
    foreign = plate[dst] != plate[src]
    fs, fd, fq = src[foreign], dst[foreign], d2[foreign]
    if not len(fs):
        z = np.empty(0, dtype=np.int64)
        return z, z.copy(), np.empty(0)
    o = np.lexsort((fq, fs))                            # by src, then distance
    fs, fd, fq = fs[o], fd[o], fq[o]
    firstmask = np.empty(len(fs), dtype=bool)
    firstmask[0] = True
    firstmask[1:] = fs[1:] != fs[:-1]                  # first (nearest) per src
    return fs[firstmask], fd[firstmask], np.sqrt(fq[firstmask])


# ---------------------------------------------------------------------------
# One simulation step
# ---------------------------------------------------------------------------
def _evolve_step(pos, plate, density, alt, active, axes, omega, dt=1.0,
                 uplift=70.0, subduct=45.0, rift=18.0, erosion=0.004,
                 collide_r=0.05, consume_r=0.022, smooth=2,
                 trench=-7.5, ridge=-0.6, open_frac=0.6, max_insert=300):
    """Rotate the plates one step, then react at boundaries.

    Convergent boundaries uplift the lighter plate; the denser plate subducts and
    — once driven right up against foreign crust — is *consumed* (removed from
    the live sheet) so the node clouds can't interpenetrate into speckle.
    Divergent boundaries that pull open a gap *spawn* fresh crust by recycling a
    consumed node (a closed mantle conveyor, so coverage stays stable). The raw
    per-node forcing is diffused over the neighbour graph into coherent belts.
    """
    for p in range(len(omega)):
        m = plate == p
        if m.any():
            pos[m] = _rodrigues(pos[m], axes[p], omega[p] * dt)

    live = np.nonzero(active)[0]
    if len(live) < 4:
        return
    loc = np.full(len(pos), -1)
    loc[live] = np.arange(len(live))

    src_l, dst_l, d2 = _radius_neighbours(pos[live], collide_r)  # local (live) ids
    gsrc, gdst = live[src_l], live[dst_l]
    vel = _plate_velocity(pos, plate, axes, omega)
    gi, gj, nd = _nearest_foreign(gsrc, gdst, d2, plate)

    dalt = np.zeros(len(live))
    if len(gi):
        e = pos[gj] - pos[gi]
        e /= (np.linalg.norm(e, axis=1, keepdims=True) + 1e-12)
        closing = np.einsum("ij,ij->i", vel[gi] - vel[gj], e)   # >0 converging
        li = loc[gi]
        conv = closing > 0
        lighter = density[gi] < density[gj]
        up = conv & lighter
        down = conv & ~lighter
        div = ~conv
        dalt[li[up]] += uplift * closing[up]
        dalt[li[down]] -= subduct * closing[down]
        dalt[li[div]] += rift * closing[div]

    # diffuse the forcing over the neighbour graph -> broad belts, not spikes
    for _ in range(smooth):
        ssum = np.zeros(len(live))
        scnt = np.zeros(len(live))
        np.add.at(ssum, src_l, dalt[dst_l])
        np.add.at(scnt, src_l, 1.0)
        mean = np.where(scnt > 0, ssum / np.maximum(scnt, 1.0), dalt)
        dalt = 0.5 * dalt + 0.5 * mean

    alt[live] += dalt * dt
    alt[live] -= erosion * alt[live]

    if len(gi):
        eaten = gi[down & (nd < consume_r)]
        if len(eaten):
            active[eaten] = False
            alt[eaten] = trench
        open_gap = div & (nd > open_frac * collide_r)
        si, sj = gi[open_gap], gj[open_gap]
        if len(si):
            dead = np.nonzero(~active)[0]
            k = min(len(dead), len(si), max_insert)
            if k:
                slots, mi, mj = dead[:k], si[:k], sj[:k]
                mid = pos[mi] + pos[mj]
                mid /= (np.linalg.norm(mid, axis=1, keepdims=True) + 1e-12)
                pos[slots] = mid
                plate[slots] = plate[mi]
                density[slots] = 3.0
                alt[slots] = ridge
                active[slots] = True

    np.clip(alt, -8.0, 9.0, out=alt)


# ---------------------------------------------------------------------------
# Resample the scattered snapshot onto a lon/lat grid (replaces scipy griddata)
# ---------------------------------------------------------------------------
def _fill_holes(grid: np.ndarray, max_passes: int = 64) -> np.ndarray:
    """Fill NaN cells from their non-NaN neighbours (longitude wraps)."""
    g = grid
    for _ in range(max_passes):
        nan = np.isnan(g)
        if not nan.any():
            break
        acc = np.zeros_like(g)
        cnt = np.zeros_like(g)
        for ax, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            rolled = np.roll(g, shift, axis=ax)
            if ax == 0:                                # latitude does not wrap
                if shift == 1:
                    rolled[0, :] = np.nan
                else:
                    rolled[-1, :] = np.nan
            ok = ~np.isnan(rolled)
            acc[ok] += rolled[ok]
            cnt[ok] += 1.0
        fillable = nan & (cnt > 0)
        if not fillable.any():
            break                                      # nothing more can be filled
        g = g.copy()
        g[fillable] = acc[fillable] / cnt[fillable]
    if np.isnan(g).any():                              # isolated cells -> global mean
        fallback = 0.0 if np.isnan(g).all() else float(np.nanmean(g))
        g = np.where(np.isnan(g), fallback, g)
    return g


def _smooth_grid(grid: np.ndarray, passes: int = 1) -> np.ndarray:
    """Light 3x3 box blur; longitude wraps, latitude clamps. Tames resample
    speckle at plate boundaries without washing out coasts or belts."""
    g = grid
    for _ in range(passes):
        acc = g.copy()
        cnt = np.ones_like(g)
        acc += np.roll(g, 1, axis=1) + np.roll(g, -1, axis=1)   # longitude wraps
        cnt += 2.0
        for shift in (1, -1):
            rolled = np.roll(g, shift, axis=0)
            if shift == 1:
                rolled[0, :] = 0.0
            else:
                rolled[-1, :] = 0.0
            acc += rolled
            cnt += (np.arange(g.shape[0])[:, None] != (0 if shift == 1 else g.shape[0] - 1))
        g = acc / cnt
    return g


def _to_equirect(pos, alt, active, nlon, nlat, smooth=1) -> np.ndarray:
    """Splat live node altitudes onto an ``(nlat, nlon)`` grid, averaging co-cell
    nodes, filling gaps, and lightly blurring. Row 0 = north pole, row ``nlat-1`` =
    south (image convention; equator at the middle row), col = longitude."""
    v = pos[active]
    a = alt[active]
    lon = np.arctan2(v[:, 1], v[:, 0])                 # [-pi, pi]
    lat = np.arcsin(np.clip(v[:, 2], -1.0, 1.0))       # [-pi/2, pi/2]
    gx = np.clip(((lon + np.pi) / (2 * np.pi) * nlon).astype(int), 0, nlon - 1)
    gy = np.clip(((np.pi / 2 - lat) / np.pi * nlat).astype(int), 0, nlat - 1)  # north up
    ssum = np.zeros((nlat, nlon))
    scnt = np.zeros((nlat, nlon))
    np.add.at(ssum, (gy, gx), a)
    np.add.at(scnt, (gy, gx), 1.0)
    grid = np.where(scnt > 0, ssum / np.maximum(scnt, 1.0), np.nan)
    grid = _fill_holes(grid)
    if smooth:
        grid = _smooth_grid(grid, passes=smooth)
    return grid


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
def simulate_tectonic_world(
    rng: SeededRNG,
    *,
    plates: int = 8,
    steps: int = 60,
    world: str = "continents",
    warp: float = 0.28,
    land: float | None = None,
    res: int = 96,
    nlon: int = 192,
    nlat: int = 96,
) -> np.ndarray:
    """Simulate plate tectonics and return one snapshot as an equirectangular
    signed-altitude grid (``> 0`` land, ``< 0`` sea floor), shape ``(nlat, nlon)``.

    Seed-deterministic via ``rng``. ``world`` selects a shape preset
    (:data:`WORLD_STYLES`); ``plates`` sets how many rigid plates; ``steps`` is
    how far the plates drift internally before the snapshot is taken (more steps
    = more collision structure). Only the final field is returned — the
    intermediate motion is discarded.
    """
    if world not in WORLD_STYLES:
        raise ValueError(f"unknown world style {world!r}; choose from {sorted(WORLD_STYLES)}")
    gen = rng.derive("tectonics").numpy

    pos = _fibonacci_sphere(res)
    plate, density, alt = _build_plates(pos, plates, gen, warp, world, land)
    axes, omega = _euler_poles(plates, gen, steps)
    active = np.ones(len(pos), dtype=bool)
    for _ in range(steps):
        _evolve_step(pos, plate, density, alt, active, axes, omega)
    return _to_equirect(pos, alt, active, nlon, nlat)
