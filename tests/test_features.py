"""Unit tests for named geographic feature detection."""

import json

from mapwright.features import Feature, FeatureGenerator
from mapwright.rng import SeededRNG
from mapwright.terrain import RegionalTerrainGenerator


def _terrain(seed: int = 7, w: int = 60, h: int = 40):
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h)


def _features(seed: int = 7, **kw):
    return FeatureGenerator(SeededRNG(seed)).generate(_terrain(seed), **kw)


class TestDetection:
    def test_kinds_are_valid(self):
        valid = {"ocean", "lake", "island", "range", "forest"}
        assert {f.kind for f in _features()} <= valid

    def test_ocean_reaches_the_frame(self):
        # Every ocean feature must contain at least one border cell; a lake must not.
        t = _terrain()
        gen = FeatureGenerator(SeededRNG(7))
        border = gen._border_cells(t)
        feats = gen.generate(t)
        for f in feats:
            touches = any(cid in border for cid in f.cells)
            if f.kind == "ocean":
                assert touches
            if f.kind == "lake":
                assert not touches

    def test_at_least_one_ocean_and_a_landmass(self):
        feats = _features()
        assert any(f.kind == "ocean" for f in feats)
        assert any(f.kind == "island" for f in feats)

    def test_every_feature_has_a_name_and_members(self):
        for f in _features():
            assert f.name
            assert len(f.cells) >= 1
            assert f.area == len(f.cells)

    def test_components_are_disjoint_within_a_kind(self):
        feats = _features()
        for kind in {"ocean", "lake", "island"}:
            seen: set[int] = set()
            for f in feats:
                if f.kind != kind:
                    continue
                assert seen.isdisjoint(f.cells)
                seen.update(f.cells)

    def test_size_gates_are_respected(self):
        # A very high forest gate removes all forest features.
        feats = FeatureGenerator(SeededRNG(7)).generate(_terrain(), min_forest=10_000)
        assert not any(f.kind == "forest" for f in feats)


class TestDeterminism:
    def test_same_seed_same_features(self):
        a = _features(7)
        b = _features(7)
        assert [(f.kind, f.name, f.cells) for f in a] == [
            (f.kind, f.name, f.cells) for f in b
        ]

    def test_centroid_within_bounds(self):
        t = _terrain()
        for f in FeatureGenerator(SeededRNG(7)).generate(t):
            assert 0 <= f.centroid[0] <= t.width
            assert 0 <= f.centroid[1] <= t.height


class TestSerialization:
    def test_round_trip(self):
        for f in _features():
            r = Feature.from_dict(json.loads(f.to_json()))
            assert r == f
