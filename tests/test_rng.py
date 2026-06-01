"""Unit tests for SeededRNG — the unified, reproducible random stream."""

import random

import numpy as np

from mapwright.rng import SeededRNG


def _draw_sequence(rng: SeededRNG, n: int = 20) -> list:
    return [rng.random() for _ in range(n)]


class TestReproducibility:
    def test_same_seed_same_sequence(self):
        a = SeededRNG(12345)
        b = SeededRNG(12345)
        assert _draw_sequence(a) == _draw_sequence(b)

    def test_different_seed_different_sequence(self):
        a = SeededRNG(1)
        b = SeededRNG(2)
        assert _draw_sequence(a) != _draw_sequence(b)

    def test_seed_is_resolved_and_persisted(self):
        rng = SeededRNG(None)
        assert isinstance(rng.seed, int)
        # Replaying the resolved seed reproduces the stream exactly.
        replay = SeededRNG(rng.seed)
        assert _draw_sequence(rng) == _draw_sequence(replay)

    def test_seed_is_within_31_bits(self):
        assert SeededRNG(2**40 + 7).seed == (2**40 + 7) % 2**31

    def test_numpy_stream_is_reproducible(self):
        a = SeededRNG(99).numpy.integers(0, 1000, size=50)
        b = SeededRNG(99).numpy.integers(0, 1000, size=50)
        assert np.array_equal(a, b)


class TestIsolation:
    def test_does_not_touch_global_random(self):
        random.seed(0)
        baseline = [random.random() for _ in range(5)]
        random.seed(0)
        # Interleave heavy SeededRNG use; global stream must be untouched.
        rng = SeededRNG(777)
        _draw_sequence(rng, 100)
        rng.derive("x").randint(0, 10)
        assert [random.random() for _ in range(5)] == baseline


class TestDerive:
    def test_derive_is_deterministic(self):
        parent = SeededRNG(42)
        c1 = parent.derive("terrain")
        c2 = SeededRNG(42).derive("terrain")
        assert _draw_sequence(c1) == _draw_sequence(c2)

    def test_different_labels_independent(self):
        parent = SeededRNG(42)
        assert _draw_sequence(parent.derive("terrain")) != _draw_sequence(
            parent.derive("names")
        )

    def test_child_independent_of_parent_draw_order(self):
        # The whole point of derive(): drawing from one child must not shift
        # another child's stream. Derive names first vs terrain first.
        p1 = SeededRNG(5)
        names_first = _draw_sequence(p1.derive("names"))

        p2 = SeededRNG(5)
        _draw_sequence(p2.derive("terrain"), 50)  # exhaust terrain first
        names_second = _draw_sequence(p2.derive("names"))
        assert names_first == names_second


class TestHelpers:
    def test_randint_inclusive_bounds(self):
        rng = SeededRNG(3)
        vals = [rng.randint(1, 6) for _ in range(500)]
        assert min(vals) == 1 and max(vals) == 6

    def test_chance_extremes(self):
        rng = SeededRNG(3)
        assert all(rng.chance(1.0) for _ in range(20))
        assert not any(rng.chance(0.0) for _ in range(20))

    def test_fuzzy_within_spread(self):
        rng = SeededRNG(3)
        for _ in range(200):
            v = rng.fuzzy(10.0, 2.0)
            assert 8.0 <= v < 12.0

    def test_weighted_respects_zero_weight(self):
        rng = SeededRNG(3)
        picks = {rng.weighted({"a": 1.0, "b": 0.0}) for _ in range(50)}
        assert picks == {"a"}

    def test_weighted_skews_to_heavy_option(self):
        rng = SeededRNG(3)
        picks = [rng.weighted({"rare": 1.0, "common": 99.0}) for _ in range(400)]
        assert picks.count("common") > picks.count("rare")
