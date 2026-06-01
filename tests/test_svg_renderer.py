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
