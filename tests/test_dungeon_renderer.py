"""Unit tests for the dungeon SVG renderer."""

import xml.etree.ElementTree as ET

import numpy as np

from mapwright import Dungeon, DungeonGenerator, DungeonSVGRenderer, Rect, SeededRNG


def _dungeon(seed: int = 3, w: int = 48, h: int = 32) -> Dungeon:
    return DungeonGenerator(SeededRNG(seed)).generate(w, h)


class TestDungeonSVGRender:
    def test_is_well_formed_xml(self):
        svg = DungeonSVGRenderer().render(_dungeon())
        root = ET.fromstring(svg)  # raises on malformed XML
        assert root.tag.endswith("svg")

    def test_has_expected_dimensions(self):
        d = _dungeon(w=48, h=32)
        root = ET.fromstring(DungeonSVGRenderer(scale=14).render(d))
        assert root.attrib["width"] == "672"   # 48 * 14
        assert root.attrib["height"] == "448"  # 32 * 14

    def test_renders_floor_rects(self):
        # The floor is drawn as run-length rects; a non-empty dungeon has some.
        svg = DungeonSVGRenderer().render(_dungeon())
        assert svg.count("<rect") > len(_dungeon().rooms)

    def test_room_outlines_present_by_default(self):
        d = _dungeon()
        svg = DungeonSVGRenderer().render(d)
        assert "stroke" in svg  # room outlines carry a stroke

    def test_show_rooms_false_drops_outlines(self):
        d = _dungeon()
        with_rooms = DungeonSVGRenderer().render(d, show_rooms=True)
        without = DungeonSVGRenderer().render(d, show_rooms=False)
        assert without.count("<rect") < with_rooms.count("<rect")

    def test_grid_lines_optional(self):
        d = _dungeon()
        assert "<path" not in DungeonSVGRenderer().render(d, show_grid=False)
        assert "<path" in DungeonSVGRenderer().render(d, show_grid=True)

    def test_numeric_labels(self):
        d = _dungeon()
        svg = DungeonSVGRenderer().render(d, labels=True)
        assert svg.count("<text") == len(d.rooms)
        assert ">1<" in svg  # rooms numbered from 1

    def test_explicit_string_labels_are_escaped(self):
        d = _dungeon()
        names = ["Vault & Hoard"] + [f"R{i}" for i in range(1, len(d.rooms))]
        svg = DungeonSVGRenderer().render(d, labels=names)
        assert "&amp;" in svg
        assert "Vault & Hoard" not in svg  # raw ampersand never leaks

    def test_no_labels_by_default(self):
        assert "<text" not in DungeonSVGRenderer().render(_dungeon())

    def test_label_centered_on_even_sized_room(self):
        # A label must sit on the room rect's true pixel centre, not half a tile
        # off — even-width/height rooms used to drift by s/2.
        grid = np.zeros((4, 6), dtype=bool)
        grid[0:4, 0:6] = True
        d = Dungeon(width=6, height=4, rooms=[Rect(0, 0, 6, 4)], corridors=[], grid=grid)
        s = 10.0
        svg = DungeonSVGRenderer(scale=s).render(d, labels=["A"])
        text = ET.fromstring(svg).find(".//{http://www.w3.org/2000/svg}text")
        assert float(text.attrib["x"]) == (0 + 6 / 2) * s  # 30.0, the rect centre
        assert float(text.attrib["y"]) == (0 + 4 / 2) * s + 4  # baseline nudge

    def test_grid_opacity_is_svg11_safe(self):
        # Uses stroke-opacity (not 8-digit hex alpha) so SVG 1.1 renderers honour it.
        svg = DungeonSVGRenderer().render(_dungeon(), show_grid=True)
        assert "stroke-opacity=" in svg
        assert "#00000022" not in svg

    def test_deterministic_output(self):
        a = DungeonSVGRenderer().render(_dungeon(seed=11))
        b = DungeonSVGRenderer().render(_dungeon(seed=11))
        assert a == b
