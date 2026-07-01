"""Unit tests for simulated-annealing label placement."""

from mapwright.labeling import (
    LabelPlacer,
    LabelRequest,
    PlacedLabel,
    _box_size,
    _overlap,
)


def _box(pl: PlacedLabel):
    """Reconstruct a placed label's bounding box from its draw position."""
    w, h = _box_size(pl.text, pl.font_size)
    if pl.anchor == "start":
        x0 = pl.x
    elif pl.anchor == "end":
        x0 = pl.x - w
    else:
        x0 = pl.x - w / 2
    y0 = pl.y - h * 0.78
    return (x0, y0, x0 + w, y0 + h)


def _cluster(n=12, fs=10):
    return [
        LabelRequest(f"Town{i}", (50 + (i % 4) * 30, 50 + (i // 4) * 22),
                     "marker", marker_radius=4, font_size=fs)
        for i in range(n)
    ]


class TestBasics:
    def test_empty_input(self):
        assert LabelPlacer(seed=1).place([]) == []

    def test_skips_blank_text(self):
        out = LabelPlacer(seed=1).place([LabelRequest("", (10, 10))])
        assert out == []

    def test_every_request_is_placed(self):
        reqs = _cluster()
        assert len(LabelPlacer(seed=1, bounds=(400, 300)).place(reqs)) == len(reqs)

    def test_single_label_prefers_the_classic_right_middle(self):
        # With nothing to avoid, one label should take the lowest-pref candidate:
        # text to the right, baseline near the marker.
        r = LabelRequest("Solo", (100, 100), "marker", marker_radius=4, font_size=10)
        p = LabelPlacer(seed=1, bounds=(400, 300)).place([r])[0]
        assert p.anchor == "start"
        assert p.x > 100  # placed to the right of the anchor


class TestDeterminism:
    def test_same_seed_same_layout(self):
        reqs = _cluster()
        a = LabelPlacer(seed=7, bounds=(400, 300)).place(reqs)
        b = LabelPlacer(seed=7, bounds=(400, 300)).place(reqs)
        assert [(p.x, p.y, p.anchor) for p in a] == [(p.x, p.y, p.anchor) for p in b]


class TestQuality:
    def test_no_overlaps_when_space_allows(self):
        reqs = _cluster()
        placed = LabelPlacer(seed=3, bounds=(400, 300)).place(reqs)
        boxes = [_box(p) for p in placed]
        pairs = sum(
            1
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
            if _overlap(boxes[i], boxes[j])
        )
        assert pairs == 0

    def test_labels_stay_in_bounds(self):
        reqs = _cluster()
        placed = LabelPlacer(seed=4, bounds=(400, 300)).place(reqs)
        for x0, y0, x1, y1 in (_box(p) for p in placed):
            assert x0 >= -1 and y0 >= -1 and x1 <= 401 and y1 <= 301

    def test_edge_anchored_labels_are_clamped_on_canvas(self):
        # Area/marker labels whose anchor sits on the frame must not be clipped.
        reqs = [
            LabelRequest("The Border Sea", (399, 5), "area", font_size=12),
            LabelRequest("Cornerton", (1, 299), "marker", marker_radius=4,
                         font_size=10),
            LabelRequest("Topedge", (200, 0), "area", font_size=11),
        ]
        placed = LabelPlacer(seed=2, bounds=(400, 300)).place(reqs)
        for x0, y0, x1, y1 in (_box(p) for p in placed):
            assert x0 >= -0.5 and y0 >= -0.5 and x1 <= 400.5 and y1 <= 300.5

    def test_avoids_busy_linework_better_than_naive(self):
        # Dense avoid points sit just to the RIGHT of each marker (where the naive
        # right-offset would land). A good placer should route labels elsewhere.
        reqs = [
            LabelRequest(f"P{i}", (60, 40 + i * 30), "marker",
                         marker_radius=4, font_size=10)
            for i in range(6)
        ]
        avoid = []
        for r in reqs:
            ax, ay = r.anchor
            for k in range(12):
                avoid.append((ax + 8 + k * 4, ay))  # a bar of points to the right

        def total_hits(placed):
            g = LabelPlacer(seed=0, avoid_points=avoid)  # reuse its grid counter
            return sum(g._avoid_count(_box(p)) for p in placed)

        smart = LabelPlacer(seed=5, bounds=(300, 260), avoid_points=avoid).place(reqs)
        # Naive baseline: everyone forced to the right-middle candidate.
        naive = LabelPlacer(seed=5, bounds=(300, 260)).place(
            [LabelRequest(r.text, r.anchor, "marker", marker_radius=4, font_size=10)
             for r in reqs]
        )
        assert total_hits(smart) < total_hits(naive)
