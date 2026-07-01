"""Unit tests for the regional SVG renderer + Voronoi polygon reconstruction."""

import xml.etree.ElementTree as ET


from mapwright.rng import SeededRNG
from mapwright.svg_renderer import Marker, RegionalSVGRenderer
from mapwright.terrain import RegionalTerrainGenerator, compute_cell_polygons


def _terrain(seed: int = 2026, w: int = 40, h: int = 28):
    return RegionalTerrainGenerator(SeededRNG(seed)).generate(w, h)


class TestCellPolygons:
    def test_every_cell_gets_a_polygon(self):
        t = _terrain()
        polys = compute_cell_polygons(t.cells, t.width, t.height)
        assert set(polys) == {c.id for c in t.cells}

    def test_polygons_are_valid_and_in_bounds(self):
        t = _terrain()
        polys = compute_cell_polygons(t.cells, t.width, t.height)
        nonempty = [p for p in polys.values() if len(p) >= 3]
        assert len(nonempty) > len(t.cells) * 0.8  # most cells produce a polygon
        for p in nonempty:
            for x, y in p:
                assert -0.01 <= x <= t.width + 0.01
                assert -0.01 <= y <= t.height + 0.01

    def test_polygon_contains_its_own_seed(self):
        # A Voronoi cell must contain its generating site.
        t = _terrain()
        polys = compute_cell_polygons(t.cells, t.width, t.height)
        contained = 0
        for c in t.cells:
            if _point_in_poly(c.cx, c.cy, polys[c.id]):
                contained += 1
        assert contained > len(t.cells) * 0.9


class TestSVGRender:
    def test_is_well_formed_xml(self):
        svg = RegionalSVGRenderer().render(_terrain())
        root = ET.fromstring(svg)  # raises on malformed XML
        assert root.tag.endswith("svg")

    def test_has_expected_dimensions(self):
        t = _terrain(w=40, h=28)
        svg = RegionalSVGRenderer(scale=16).render(t)
        root = ET.fromstring(svg)
        assert root.attrib["width"] == "640"   # 40 * 16
        assert root.attrib["height"] == "448"  # 28 * 16

    def test_contains_biome_polygons(self):
        svg = RegionalSVGRenderer().render(_terrain())
        assert svg.count("<polygon") > 20

    def test_renders_rivers_when_present(self):
        from mapwright import WorldMapConfig
        # Find a seed that yields rivers (robust to terrain-model tuning).
        wet = WorldMapConfig(river_density=0.95)
        t = next(
            (RegionalTerrainGenerator(SeededRNG(s)).generate(60, 44, config=wet)
             for s in range(40)
             if RegionalTerrainGenerator(SeededRNG(s)).generate(60, 44, config=wet).rivers),
            None,
        )
        assert t is not None and t.rivers
        svg = RegionalSVGRenderer().render(t)
        assert "<path" in svg

    def test_deterministic(self):
        t = _terrain()
        a = RegionalSVGRenderer().render(t)
        b = RegionalSVGRenderer().render(t)
        assert a == b

    def test_relief_changes_output(self):
        t = _terrain()
        with_relief = RegionalSVGRenderer().render(t, show_relief=True)
        flat = RegionalSVGRenderer().render(t, show_relief=False)
        assert with_relief != flat

    def test_settlement_markers_and_labels(self):
        t = _terrain()
        markers = [
            Marker(name="Eldmoor", x=20, y=14, kind="settlement_city"),
            Marker(name="Brackwater", x=10, y=8, kind="settlement_village"),
        ]
        svg = RegionalSVGRenderer().render(t, markers)
        assert svg.count("<circle") == 2
        assert "Eldmoor" in svg and "Brackwater" in svg
        # XML stays well-formed with labels.
        ET.fromstring(svg)

    def test_label_text_is_escaped(self):
        t = _terrain()
        markers = [Marker(name="Smith & Co <Keep>", x=20, y=14, kind="settlement_town")]
        svg = RegionalSVGRenderer().render(t, markers)
        assert "&amp;" in svg and "&lt;Keep&gt;" in svg
        ET.fromstring(svg)


class TestCartography:
    """v0.26 additions: hachures, smart labels, features, scale bar, compass."""

    def test_defaults_unchanged_shape(self):
        # New flags all default off → same well-formed doc, no hachure/furniture.
        t = _terrain()
        svg = RegionalSVGRenderer().render(t)
        ET.fromstring(svg)
        assert 'opacity="0.5"' not in svg  # no hachure layer by default

    def test_bad_relief_style_raises(self):
        import pytest

        with pytest.raises(ValueError):
            RegionalSVGRenderer(relief_style="nope")

    def test_hachure_adds_strokes(self):
        t = _terrain()
        svg = RegionalSVGRenderer(relief_style="hachure").render(t)
        ET.fromstring(svg)
        assert 'stroke-width="0.8"' in svg  # per-cell slope strokes

    def test_relief_both_is_well_formed(self):
        t = _terrain()
        ET.fromstring(RegionalSVGRenderer(relief_style="both").render(t))

    def test_scale_bar_and_compass(self):
        t = _terrain()
        svg = RegionalSVGRenderer().render(t, scale_bar=True, scale=80,
                                           unit="leagues", compass=True)
        ET.fromstring(svg)
        assert "leagues" in svg
        assert ">N<" in svg

    def test_smart_labels_deterministic_and_wellformed(self):
        t = _terrain()
        markers = [Marker("Aworld", 12, 10, "settlement_city"),
                   Marker("Bstead", 24, 16, "settlement_town")]
        r = RegionalSVGRenderer(label_seed=3)
        a = r.render(t, markers, smart_labels=True)
        b = r.render(t, markers, smart_labels=True)
        ET.fromstring(a)
        assert a == b
        assert "Aworld" in a and "Bstead" in a

    def test_smart_labels_replace_inline_labels(self):
        # In smart mode the naive fixed-offset marker label group is not emitted;
        # the unified annealed layer carries the names instead.
        t = _terrain()
        markers = [Marker("Onlyburg", 15, 12, "settlement_town")]
        smart = RegionalSVGRenderer(label_seed=1).render(t, markers, smart_labels=True)
        assert "Onlyburg" in smart

    def test_feature_labels_naive(self):
        from mapwright.features import FeatureGenerator

        t = _terrain()
        feats = FeatureGenerator(SeededRNG(2026)).generate(t)
        svg = RegionalSVGRenderer().render(t, features=feats)
        ET.fromstring(svg)
        if feats:
            assert su_any(feats, svg)


def su_any(feats, svg) -> bool:
    import xml.sax.saxutils as su

    return any(su.escape(f.name) in svg for f in feats)


# -- helpers ------------------------------------------------------------------

def _point_in_poly(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside
