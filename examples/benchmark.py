#!/usr/bin/env python3
"""Micro-benchmarks for the mapwright generators.

A quick, local sense of generation cost (and where the terrain cell cap kicks
in). Not part of CI — numbers are machine-dependent. Run::

    python examples/benchmark.py
"""

from __future__ import annotations

import time

from mapwright import (
    DungeonGenerator,
    RegionalRoadGenerator,
    RegionalTerrainGenerator,
    RegionGenerator,
    SeededRNG,
    SettlementConfig,
    SettlementGenerator,
)


def _best_ms(fn, repeat: int = 3) -> float:
    """Best wall-clock of ``repeat`` runs, in milliseconds (best = least noisy)."""
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best * 1000.0


def bench_terrain() -> None:
    print("terrain  (cell_area=6.0; cell count clamps at 1500)")
    for w, h in [(40, 30), (64, 44), (90, 70), (120, 90), (160, 120), (220, 160)]:
        res = RegionalTerrainGenerator(SeededRNG(1)).generate(w, h)
        ms = _best_ms(lambda: RegionalTerrainGenerator(SeededRNG(1)).generate(w, h))
        print(f"  {w:>3}x{h:<3} {w * h:>6} tiles -> {len(res.cells):>4} cells   {ms:8.1f} ms")


def bench_dungeon() -> None:
    print("dungeon")
    for w, h in [(48, 32), (80, 60), (120, 90), (200, 150)]:
        d = DungeonGenerator(SeededRNG(1)).generate(w, h)
        ms = _best_ms(lambda: DungeonGenerator(SeededRNG(1)).generate(w, h))
        print(f"  {w:>3}x{h:<3} -> {len(d.rooms):>3} rooms   {ms:8.1f} ms")


def bench_settlement() -> None:
    print("settlement  (90x90)")
    for pop in [600, 2000, 9000, 40000]:
        cfg = SettlementConfig(population=pop)
        t = SettlementGenerator(SeededRNG(1)).generate(90, 90, cfg)
        ms = _best_ms(lambda: SettlementGenerator(SeededRNG(1)).generate(90, 90, cfg))
        print(f"  pop {pop:>6} -> {len(t.wards):>2} wards, {len(t.lots):>4} lots, "
              f"{len(t.streets):>3} streets   {ms:8.1f} ms")


def bench_overlays() -> None:
    print("regional overlays  (on a 120x90 terrain)")
    terrain = RegionalTerrainGenerator(SeededRNG(1)).generate(120, 90)
    land = [c for c in terrain.cells if not c.is_water]
    sites = [(c.cx, c.cy) for c in land[:: max(1, len(land) // 8)][:8]]
    roads_ms = _best_ms(lambda: RegionalRoadGenerator().generate(terrain, sites))
    regions_ms = _best_ms(lambda: RegionGenerator(SeededRNG(1)).generate(terrain))
    print(f"  roads (8 sites)   {roads_ms:8.1f} ms")
    print(f"  regions (auto)    {regions_ms:8.1f} ms")


def main() -> None:
    for bench in (bench_terrain, bench_dungeon, bench_settlement, bench_overlays):
        bench()
        print()


if __name__ == "__main__":
    main()
