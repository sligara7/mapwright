"""Procedural dungeon generation: BSP rooms + minimum-spanning-tree corridors.

Clean-room from the ideas of two permissively-licensed generators:
  * **BSP space partitioning** — Adrian Kulawik's *Dungeon-Generator* (MIT): split
    the map recursively into leaves and place one room per leaf, so rooms never
    overlap and space is used evenly.
  * **MST connectivity** — *donjuan* (CC0): connect rooms with a minimum spanning
    tree (Prim's) so every room is reachable with no redundant corridors, then add
    a few extra edges for loops.

Domain-neutral: returns rooms (rectangles), carved corridor cells, and a boolean
walkable grid. A host maps these onto its own tiles/doors/encounters. Fully
seed-deterministic via :class:`SeededRNG`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from . import _graph, _serde
from .rng import SeededRNG


@dataclass
class Rect:
    """An axis-aligned room rectangle (integer tile coordinates)."""

    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    @property
    def center(self) -> tuple[int, int]:
        return (self.cx, self.cy)

    def intersects(self, other: "Rect", pad: int = 0) -> bool:
        return (
            self.x - pad < other.x + other.w
            and self.x + self.w + pad > other.x
            and self.y - pad < other.y + other.h
            and self.y + self.h + pad > other.y
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Rect":
        return cls(**_serde.only_known(cls, data))


@dataclass
class DungeonConfig:
    """Knobs for dungeon generation. Defaults give a balanced multi-room layout."""

    min_leaf: int = 8
    """Smallest BSP leaf (tiles) — a leaf this size or smaller is not split."""
    room_min: int = 3
    """Smallest room edge length."""
    room_padding: int = 1
    """Gap kept between a room and its leaf's edges."""
    split_jitter: float = 0.35
    """0 = always split a leaf in half; up to 0.5 = split position varies."""
    extra_corridor_chance: float = 0.12
    """Probability per non-tree room-pair of an extra (loop) corridor."""


@dataclass
class Dungeon:
    """Generated dungeon: rooms, carved corridor cells, and a walkable grid."""

    width: int
    height: int
    rooms: list[Rect]
    corridors: list[tuple[int, int]]
    grid: np.ndarray  # bool [height, width]; True = floor
    edges: list[tuple[int, int]] = field(default_factory=list)  # connected room-index pairs

    def ascii(self) -> str:
        """A quick ``#``/``.`` map (rooms+corridors as floor) for eyeballing/tests."""
        rows = []
        for y in range(self.height):
            rows.append("".join("." if self.grid[y, x] else "#" for x in range(self.width)))
        return "\n".join(rows)

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        """A JSON-safe mapping of the dungeon, round-tripping via :meth:`from_dict`
        (the walkable grid is stored as nested booleans)."""
        return {
            "schema": "mapwright/dungeon@1",
            "width": self.width,
            "height": self.height,
            "rooms": [r.to_dict() for r in self.rooms],
            "corridors": [[x, y] for x, y in self.corridors],
            "grid": self.grid.tolist(),
            "edges": [[i, j] for i, j in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dungeon":
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            rooms=[Rect.from_dict(r) for r in data["rooms"]],
            corridors=[(int(x), int(y)) for x, y in data["corridors"]],
            grid=np.asarray(data["grid"], dtype=bool),
            edges=[(int(i), int(j)) for i, j in data["edges"]],
        )

    def to_json(self, **kwargs) -> str:
        """Serialise to a JSON string (``kwargs`` pass to :func:`json.dumps`)."""
        return _serde.to_json(self, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "Dungeon":
        return _serde.from_json(cls, text)


class DungeonGenerator:
    """Builds a :class:`Dungeon` for a ``width×height`` grid."""

    def __init__(self, rng: SeededRNG):
        self._rng = rng.derive("dungeon")

    def generate(
        self, width: int, height: int, config: DungeonConfig | None = None
    ) -> Dungeon:
        cfg = config or DungeonConfig()
        leaves = self._bsp(0, 0, width, height, cfg)
        rooms = [r for leaf in leaves if (r := self._room_in_leaf(leaf, cfg))]

        grid = np.zeros((height, width), dtype=bool)
        for r in rooms:
            grid[r.y : r.y + r.h, r.x : r.x + r.w] = True

        corridors, edges = self._connect(rooms, grid, cfg)
        return Dungeon(width, height, rooms, corridors, grid, edges)

    # -- 1. BSP partitioning --------------------------------------------

    def _bsp(self, x: int, y: int, w: int, h: int, cfg: DungeonConfig) -> list[Rect]:
        """Recursively split a region into leaves no smaller than ``min_leaf``."""
        # Decide whether (and how) to split. Stop when too small to yield two
        # viable children in either axis.
        can_h = w >= 2 * cfg.min_leaf
        can_v = h >= 2 * cfg.min_leaf
        if not can_h and not can_v:
            return [Rect(x, y, w, h)]

        # Prefer splitting the longer axis so leaves stay squarish.
        if can_h and can_v:
            split_horizontal = w > h if abs(w - h) > cfg.min_leaf else self._rng.chance(0.5)
        else:
            split_horizontal = can_h

        if split_horizontal:
            lo, hi = cfg.min_leaf, w - cfg.min_leaf
            cut = self._jittered_cut(lo, hi, w, cfg)
            return (self._bsp(x, y, cut, h, cfg)
                    + self._bsp(x + cut, y, w - cut, h, cfg))
        else:
            lo, hi = cfg.min_leaf, h - cfg.min_leaf
            cut = self._jittered_cut(lo, hi, h, cfg)
            return (self._bsp(x, y, w, cut, cfg)
                    + self._bsp(x, y + cut, w, h - cut, cfg))

    def _jittered_cut(self, lo: int, hi: int, span: int, cfg: DungeonConfig) -> int:
        mid = span / 2
        jitter = span * cfg.split_jitter
        cut = int(round(self._rng.uniform(mid - jitter, mid + jitter)))
        return max(lo, min(hi, cut))

    # -- 2. Rooms --------------------------------------------------------

    def _room_in_leaf(self, leaf: Rect, cfg: DungeonConfig) -> Rect | None:
        """Place a random room inside a leaf, inset by ``room_padding``."""
        pad = cfg.room_padding
        max_w = leaf.w - 2 * pad
        max_h = leaf.h - 2 * pad
        if max_w < cfg.room_min or max_h < cfg.room_min:
            return None
        rw = self._rng.randint(cfg.room_min, max_w)
        rh = self._rng.randint(cfg.room_min, max_h)
        rx = leaf.x + pad + self._rng.randint(0, max_w - rw)
        ry = leaf.y + pad + self._rng.randint(0, max_h - rh)
        return Rect(rx, ry, rw, rh)

    # -- 3. Corridors (MST + loops) -------------------------------------

    def _connect(
        self, rooms: list[Rect], grid: np.ndarray, cfg: DungeonConfig
    ) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        """Connect rooms with a Prim MST (+ optional loops); carve L-corridors."""
        n = len(rooms)
        corridors: list[tuple[int, int]] = []
        edges: list[tuple[int, int]] = []
        if n < 2:
            return corridors, edges

        centers = [r.center for r in rooms]

        def dist2(i: int, j: int) -> int:
            (ax, ay), (bx, by) = centers[i], centers[j]
            return (ax - bx) ** 2 + (ay - by) ** 2

        # Prim's MST over room centers (dense graph, n is small).
        edges = _graph.prim_mst(n, dist2)

        # A few extra edges → loops (less tree-like, more interesting).
        for i in range(n):
            for j in range(i + 1, n):
                if (i, j) in edges or (j, i) in edges:
                    continue
                if self._rng.chance(cfg.extra_corridor_chance):
                    edges.append((i, j))

        for i, j in edges:
            corridors.extend(self._carve_l(centers[i], centers[j], grid))
        return corridors, edges

    def _carve_l(
        self, a: tuple[int, int], b: tuple[int, int], grid: np.ndarray
    ) -> list[tuple[int, int]]:
        """Carve an L-shaped corridor between two points; return the new cells."""
        (ax, ay), (bx, by) = a, b
        cells: list[tuple[int, int]] = []
        h_first = self._rng.chance(0.5)
        corner = (bx, ay) if h_first else (ax, by)

        def line(p: tuple[int, int], q: tuple[int, int]) -> None:
            (px, py), (qx, qy) = p, q
            if px == qx:
                for y in range(min(py, qy), max(py, qy) + 1):
                    if not grid[y, px]:
                        grid[y, px] = True
                        cells.append((px, y))
            else:
                for x in range(min(px, qx), max(px, qx) + 1):
                    if not grid[py, x]:
                        grid[py, x] = True
                        cells.append((x, py))

        line(a, corner)
        line(corner, b)
        return cells
