"""Cartographic label placement by simulated annealing.

Given a set of :class:`LabelRequest` (each a piece of text anchored to a point —
a settlement marker, or a feature centroid) this places the labels so they avoid
each other, avoid the map frame, avoid sitting on top of markers, and steer clear
of busy line-work (rivers, coastlines, borders) supplied as ``avoid_points``.

The method is the classic map-labeling formulation (Christensen/Zoraster,
as implemented in rlguy's FantasyMapGenerator): each label has a discrete set of
candidate positions; an additive *energy* scores a whole configuration (per-label
base penalties + a mutual-overlap penalty); simulated annealing with a geometric
cooling schedule searches for a low-energy layout.

Renderer-agnostic and reused across the world / settlement / dungeon renderers.
**Self-contained determinism:** a :class:`LabelPlacer` builds its own
:class:`~mapwright.rng.SeededRNG` from an integer ``seed``, so the renderer stays
stateless and pure — same seed and inputs → identical layout, every process.

Clean-room from the published algorithm (rlguy, Zlib) — no source copied. Text
boxes are estimated from character count (no font metrics), which keeps this
dependency-free at the cost of exact glyph widths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .rng import SeededRNG

# Scoring weights (all penalties; lower energy is better).
_W_BOUNDS = 4.0    # text box leaves the map frame
_W_MARKER = 2.0    # box covers another label's anchor point
_W_AVOID = 3.0     # box covers busy line-work (river/coast/border points)
_W_PREF = 1.0      # orientation preference (candidate-intrinsic)
_W_OVERLAP = 4.0   # box overlaps another label's chosen box

# Annealing schedule.
_T0 = 1.0 / math.log(3.0)   # ~0.9102; P(accept ΔE=1) = 2/3 at the start
_COOL = 0.9
_MAX_TEMPS = 100
_SUCCESS_FACTOR = 5    # cool after 5·n accepted moves at a temperature
_ATTEMPT_FACTOR = 20   # or after 20·n attempted moves


@dataclass
class LabelRequest:
    """A label to place: its ``text`` anchored at ``anchor`` (pixel coords)."""

    text: str
    anchor: tuple[float, float]
    kind: str = "marker"          # "marker" (offset beside anchor) | "area" (over it)
    priority: float = 1.0         # reserved: higher = more important to place well
    font_size: float = 9.0
    marker_radius: float = 0.0    # dot radius to offset a marker label clear of


@dataclass
class PlacedLabel:
    """A resolved label position for a renderer to draw.

    ``x``/``y`` are the SVG text position (``y`` is the baseline) and ``anchor``
    is the SVG ``text-anchor`` (``start``/``middle``/``end``) that ``x`` refers to.
    """

    text: str
    x: float
    y: float
    font_size: float
    anchor: str = "start"


@dataclass
class _Candidate:
    box: tuple[float, float, float, float]   # x0, y0, x1, y1
    draw_x: float
    draw_y: float                            # baseline
    text_anchor: str
    pref: float                              # orientation preference (>=0, lower better)


def _box_size(text: str, font_size: float) -> tuple[float, float]:
    """Estimate a text box (w, h) from character count — no font metrics."""
    w = max(1, len(text)) * font_size * 0.55
    h = font_size * 1.2
    return w, h


def _overlap(a, b) -> bool:
    """Axis-aligned box overlap (half-open, touching edges don't count)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class LabelPlacer:
    """Places :class:`LabelRequest` s by simulated annealing."""

    def __init__(
        self,
        seed: int = 0,
        *,
        avoid_points: list | tuple = (),
        bounds: tuple[float, float] | None = None,
    ):
        self._rng = SeededRNG(seed).derive("labeling")
        self._bounds = bounds  # (width, height) of the drawable area, or None
        self._avoid = [(float(x), float(y)) for x, y in avoid_points]
        self._grid = self._build_avoid_grid(self._avoid)

    def place(self, requests) -> list[PlacedLabel]:
        """Resolve ``requests`` into non-overlapping :class:`PlacedLabel` s."""
        reqs = [r for r in requests if r.text]
        if not reqs:
            return []

        anchors = [r.anchor for r in reqs]
        cand: list[list[_Candidate]] = [
            self._candidates(r) for r in reqs
        ]
        base = [
            [self._base_score(c, li, anchors) for c in cand[li]]
            for li in range(len(reqs))
        ]

        chosen = self._anneal(cand, base)
        out: list[PlacedLabel] = []
        for li, ci in enumerate(chosen):
            c = cand[li][ci]
            out.append(PlacedLabel(reqs[li].text, c.draw_x, c.draw_y,
                                   reqs[li].font_size, c.text_anchor))
        return out

    # -- candidate generation -------------------------------------------------

    def _candidates(self, r: LabelRequest) -> list[_Candidate]:
        ax, ay = r.anchor
        w, h = _box_size(r.text, r.font_size)
        if r.kind == "area":
            cands = self._area_candidates(ax, ay, w, h)
        else:
            cands = self._marker_candidates(ax, ay, r.marker_radius, w, h)
        # Pull any candidate whose box overhangs the frame back on-canvas, so a
        # label anchored near the edge (e.g. an ocean centroid by the border) is
        # never clipped when it can fit at all.
        return [self._clamp(c) for c in cands]

    def _clamp(self, c: _Candidate) -> _Candidate:
        if self._bounds is None:
            return c
        bw, bh = self._bounds
        x0, y0, x1, y1 = c.box
        dx = -x0 if x0 < 0 else (bw - x1 if x1 > bw else 0.0)
        dy = -y0 if y0 < 0 else (bh - y1 if y1 > bh else 0.0)
        if dx == 0.0 and dy == 0.0:
            return c
        return _Candidate((x0 + dx, y0 + dy, x1 + dx, y1 + dy),
                          c.draw_x + dx, c.draw_y + dy, c.text_anchor, c.pref)

    def _marker_candidates(self, ax, ay, radius, w, h) -> list[_Candidate]:
        pad = 2.0
        r = radius
        cands: list[_Candidate] = []

        def add(x0, y0, anchor, pref):
            box = (x0, y0, x0 + w, y0 + h)
            if anchor == "start":
                dx = x0
            elif anchor == "end":
                dx = x0 + w
            else:
                dx = x0 + w / 2
            dy = y0 + h * 0.78  # baseline near the box bottom
            cands.append(_Candidate(box, dx, dy, anchor, pref))

        # Right side (text-anchor start): box left edge at ax + r + pad.
        rx = ax + r + pad
        add(rx, ay - h / 2, "start", 0.0)          # right-middle (classic best)
        add(rx, ay - h - r * 0.3, "start", 1.0)    # right-up
        add(rx, ay + r * 0.3, "start", 1.0)        # right-down
        # Left side (text-anchor end): box right edge at ax - r - pad.
        lx = ax - r - pad - w
        add(lx, ay - h / 2, "end", 2.0)            # left-middle
        add(lx, ay - h - r * 0.3, "end", 3.0)      # left-up
        add(lx, ay + r * 0.3, "end", 3.0)          # left-down
        # Above / below (text-anchor middle): box centred on ax.
        cx0 = ax - w / 2
        add(cx0, ay - r - pad - h, "middle", 2.5)  # above
        add(cx0, ay + r + pad, "middle", 3.5)      # below
        return cands

    def _area_candidates(self, ax, ay, w, h) -> list[_Candidate]:
        cands: list[_Candidate] = []

        def add(cx, cy, pref):
            x0 = cx - w / 2
            y0 = cy - h / 2
            box = (x0, y0, x0 + w, y0 + h)
            cands.append(_Candidate(box, cx, y0 + h * 0.78, "middle", pref))

        add(ax, ay, 0.0)  # centred on the centroid (preferred)
        step = h * 1.3
        for k, (dx, dy) in enumerate(
            [(0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)]
        ):
            add(ax + dx * step, ay + dy * step, 1.0 + 0.1 * k)
        return cands

    # -- scoring --------------------------------------------------------------

    def _base_score(self, c: _Candidate, li: int, anchors) -> float:
        x0, y0, x1, y1 = c.box
        score = _W_PREF * c.pref
        if self._bounds is not None:
            bw, bh = self._bounds
            if x0 < 0 or y0 < 0 or x1 > bw or y1 > bh:
                score += _W_BOUNDS
        # Covering another label's anchor dot.
        hits = 0
        for j, (ax, ay) in enumerate(anchors):
            if j == li:
                continue
            if x0 <= ax <= x1 and y0 <= ay <= y1:
                hits += 1
        score += _W_MARKER * min(hits, 3)
        # Covering busy line-work (rivers/coast/borders). Scales with how much
        # line-work is under the box, up to a cap that can push a label off its
        # preferred side but not out of bounds or onto another label.
        n = self._avoid_count(c.box)
        score += _W_AVOID * min(1.5, n / 3.0)
        return score

    # -- annealing ------------------------------------------------------------

    def _anneal(self, cand, base) -> list[int]:
        n = len(cand)
        if n == 1:  # single label: just take its lowest-penalty candidate
            return [min(range(len(base[0])), key=lambda c: base[0][c])]

        # Flatten candidates to global ids for the overlap adjacency.
        offset = [0] * n
        boxes: list[tuple] = []
        label_of: list[int] = []
        for li in range(n):
            offset[li] = len(boxes)
            for c in cand[li]:
                boxes.append(c.box)
                label_of.append(li)

        # Overlap adjacency: gid -> set of gids from *other* labels overlapping it.
        overlap: list[set] = [set() for _ in boxes]
        for gi in range(len(boxes)):
            for gj in range(gi + 1, len(boxes)):
                if label_of[gi] == label_of[gj]:
                    continue
                if _overlap(boxes[gi], boxes[gj]):
                    overlap[gi].add(gj)
                    overlap[gj].add(gi)

        rng = self._rng
        active = [rng.randint(0, len(cand[li]) - 1) for li in range(n)]
        active_gid = [offset[li] + active[li] for li in range(n)]

        temp = _T0
        for _ in range(_MAX_TEMPS):
            successes = attempts = 0
            success_cap = _SUCCESS_FACTOR * n
            attempt_cap = _ATTEMPT_FACTOR * n
            while attempts < attempt_cap and successes < success_cap:
                attempts += 1
                li = rng.randint(0, n - 1)
                if len(cand[li]) < 2:
                    continue
                old_c = active[li]
                new_c = rng.randint(0, len(cand[li]) - 1)
                if new_c == old_c:
                    continue

                d_base = base[li][new_c] - base[li][old_c]
                # Collision delta: only labels whose *active* candidate overlaps
                # the old or new box can change, so scan those two (small) overlap
                # sets rather than all n labels.
                o_old = overlap[active_gid[li]]
                o_new = overlap[offset[li] + new_c]
                d_pairs = 0
                for gid in o_old:
                    m = label_of[gid]
                    if m != li and active_gid[m] == gid:
                        d_pairs -= 1
                for gid in o_new:
                    m = label_of[gid]
                    if m != li and active_gid[m] == gid:
                        d_pairs += 1
                d_energy = d_base + _W_OVERLAP * 2 * d_pairs

                if d_energy <= 0 or rng.random() < math.exp(-d_energy / temp):
                    active[li] = new_c
                    active_gid[li] = offset[li] + new_c
                    successes += 1
            if successes == 0:
                break  # frozen
            temp *= _COOL

        return active

    # -- avoid-point spatial grid --------------------------------------------

    _GRID = 24.0

    def _build_avoid_grid(self, points) -> dict:
        grid: dict = {}
        g = self._GRID
        for x, y in points:
            key = (int(x // g), int(y // g))
            grid.setdefault(key, []).append((x, y))
        return grid

    def _avoid_count(self, box) -> int:
        if not self._grid:
            return 0
        x0, y0, x1, y1 = box
        g = self._GRID
        n = 0
        for gx in range(int(x0 // g), int(x1 // g) + 1):
            for gy in range(int(y0 // g), int(y1 // g) + 1):
                for px, py in self._grid.get((gx, gy), ()):
                    if x0 <= px <= x1 and y0 <= py <= y1:
                        n += 1
        return n
