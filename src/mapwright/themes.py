"""Render **themes** — a palette + biome *vocabulary* layered over the neutral
terrain so the same generated world can be re-skinned in wildly different styles.

mapwright's :class:`~mapwright.terrain.Biome` enum stays fixed (generation never
changes); a :class:`Theme` only decides *how each biome looks and is named* when
rendered. This is the "Dominant Medium" idea from the imaginative-realms vision:
a sand world, a digital-grid world, and a blueprint schematic are the *same*
cells, biomes, rivers and roads — wearing a different skin.

A theme carries every colour the :class:`~mapwright.svg_renderer.RegionalSVGRenderer`
draws (biome fills + water/coast/river/road/region/label/settlement colours) and an
optional ``biome_names`` vocabulary that relabels biomes for legends or flavour
(e.g. ``OCEAN`` → "Void", ``FOREST`` → "Data Grove"). Built-in themes live in
:data:`THEMES`; ``"parchment"`` is the default and reproduces the classic look.

Themes are plain hex-string data — JSON-friendly, so a host (or an image service
that also makes art packs) can author new ones. Pair a theme's palette with an
:class:`~mapwright.atlas_renderer.ArtPack` of matching symbols for a full restyle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .terrain import Biome

_ALL_BIOMES = tuple(Biome)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


@dataclass(frozen=True)
class SettlementPalette:
    """Colours for :class:`~mapwright.settlement_renderer.SettlementSVGRenderer`.

    ``wards`` maps a ward *kind* (``"market"``, ``"docks"``, …) to a fill; any
    kind not listed falls back to ``ward_default``.
    """

    countryside: str
    footprint: str
    water: str
    ward_default: str
    ward_stroke: str
    building: str
    building_stroke: str
    road: str
    road_casing: str
    wall: str
    tower_edge: str
    label: str
    label_halo: str
    wards: dict[str, str] = field(default_factory=dict)

    def ward_fill(self, kind: str) -> str:
        return self.wards.get(kind, self.ward_default)

    def __hash__(self) -> int:  # dict field ⇒ hash on the stable scalar identity
        return hash((self.countryside, self.footprint, self.wall, self.road))


@dataclass(frozen=True)
class DungeonPalette:
    """Colours for :class:`~mapwright.dungeon_renderer.DungeonSVGRenderer`."""

    wall_bg: str
    floor: str
    room_fill: str
    room_stroke: str
    grid_line: str
    label: str
    label_halo: str


# Parchment sub-palettes — the canonical defaults (byte-identical to the colours
# the settlement / dungeon renderers used before themes existed).
_PARCHMENT_SETTLEMENT = SettlementPalette(
    countryside="#c9d2bb", footprint="#e6dcc0", water="#2f6d8f",
    ward_default="#cdbf9e", ward_stroke="#4a4230",
    building="#7d6c52", building_stroke="#4a3e2e",
    road="#e4d9bc", road_casing="#6c604a",
    wall="#3c3628", tower_edge="#1c1810",
    label="#23211c", label_halo="#f7f3ea",
    wards={"market": "#d9c08a", "residential": "#cdbf9e", "craftsmen": "#c2b48f",
           "noble": "#d8cdb0", "slums": "#b3a684", "temple": "#dfd6c0",
           "garrison": "#b9a98c", "docks": "#aebbb0"},
)
_PARCHMENT_DUNGEON = DungeonPalette(
    wall_bg="#1b1b22", floor="#c9bd9e", room_fill="#d8cdae",
    room_stroke="#3a3527", grid_line="#000000",
    label="#23211c", label_halo="#f7f3ea",
)


@dataclass(frozen=True)
class Theme:
    """A render palette + biome vocabulary. All colours are ``"#rrggbb"`` hex.

    ``biomes`` must map *every* :class:`Biome`; the rest are scalar element
    colours. ``biome_names`` is an optional display vocabulary (falls back to the
    biome's title-cased enum name).
    """

    name: str
    biomes: dict[Biome, str]
    ocean_bg: str
    coastline: str
    river: str
    road: str
    road_casing: str
    region_border: str
    region_label: str
    settlement_fill: str
    settlement_stroke: str
    label_fill: str
    label_halo: str
    biome_names: dict[Biome, str] = field(default_factory=dict)
    # Sub-palettes for the town & dungeon renderers; default to parchment so a
    # theme that only restyles the regional map still drives all three renderers.
    settlement: SettlementPalette = _PARCHMENT_SETTLEMENT
    dungeon: DungeonPalette = _PARCHMENT_DUNGEON

    def __post_init__(self) -> None:
        missing = [b.name for b in _ALL_BIOMES if b not in self.biomes]
        if missing:
            raise ValueError(f"theme {self.name!r} missing biome colours: {missing}")

    # ``frozen=True`` advertises hashability, but the dict fields make the
    # auto-generated __hash__ raise. Themes are identified by name, so hash on
    # that (value-equality via the dataclass __eq__ is preserved).
    def __hash__(self) -> int:
        return hash(self.name)

    def biome_label(self, biome: Biome) -> str:
        """Display name for ``biome`` under this theme's vocabulary."""
        return self.biome_names.get(biome, biome.name.title())

    def biome_rgb(self) -> dict[Biome, tuple[int, int, int]]:
        """Biome fills as ``(r, g, b)`` tuples (for relief shading)."""
        return {b: _hex_to_rgb(h) for b, h in self.biomes.items()}


# --- built-in themes -------------------------------------------------------

# "parchment" — the classic fantasy look. This is the canonical source of the
# default palette (the renderer derives its colours from here), so the default
# render is byte-identical to mapwright's pre-theme output.
_PARCHMENT = Theme(
    name="parchment",
    biomes={
        Biome.OCEAN: "#1f4e6b", Biome.COAST: "#3d7ea6", Biome.BEACH: "#d9c79b",
        Biome.DESERT: "#d6c482", Biome.PLAINS: "#a9c47f", Biome.FOREST: "#4f824a",
        Biome.SWAMP: "#6b7b4a", Biome.HILLS: "#a09a64", Biome.MOUNTAIN: "#8c847a",
        Biome.TUNDRA: "#bcc4b4", Biome.SNOW: "#f0f4f8", Biome.RIVER: "#7fa86a",
        Biome.LAKE: "#6098b8",
    },
    ocean_bg="#183e56", coastline="#283640", river="#4a82af",
    road="#6e4e32", road_casing="#f5eede",
    region_border="#78242c", region_label="#4a181e",
    settlement_fill="#f4ead8", settlement_stroke="#2b2b2b",
    label_fill="#23211c", label_halo="#f7f3ea",
)

# "neon" — Tron / digital-grid medium: near-black void, electric cyan coast &
# rivers, magenta routes & borders.
_NEON = Theme(
    name="neon",
    biomes={
        Biome.OCEAN: "#06121f", Biome.COAST: "#0a7d8c", Biome.BEACH: "#103b44",
        Biome.DESERT: "#1a1030", Biome.PLAINS: "#0e2233", Biome.FOREST: "#0a3a2e",
        Biome.SWAMP: "#07262a", Biome.HILLS: "#122a3a", Biome.MOUNTAIN: "#1b1140",
        Biome.TUNDRA: "#14303a", Biome.SNOW: "#aef7ff", Biome.RIVER: "#08303a",
        Biome.LAKE: "#00b8e6",
    },
    ocean_bg="#05060a", coastline="#00e5ff", river="#00e5ff",
    road="#ff2bd6", road_casing="#1a0a2a",
    region_border="#ff2bd6", region_label="#00e5ff",
    settlement_fill="#00e5ff", settlement_stroke="#ff2bd6",
    label_fill="#aef7ff", label_halo="#05060a",
    biome_names={Biome.OCEAN: "Void", Biome.FOREST: "Data Grove",
                 Biome.MOUNTAIN: "Spire", Biome.DESERT: "Null Sector",
                 Biome.LAKE: "Data Pool"},
    settlement=SettlementPalette(
        countryside="#05060a", footprint="#0a1420", water="#06121f",
        ward_default="#0e2233", ward_stroke="#00e5ff",
        building="#0a3a4a", building_stroke="#00e5ff",
        road="#ff2bd6", road_casing="#1a0a2a",
        wall="#00343f", tower_edge="#00e5ff",
        label="#aef7ff", label_halo="#05060a",
        wards={"market": "#103a4a", "temple": "#13314a", "noble": "#1b2a52",
               "docks": "#08303a", "garrison": "#10202e", "craftsmen": "#0e2838"},
    ),
    dungeon=DungeonPalette(
        wall_bg="#05060a", floor="#0a3a4a", room_fill="#0e4a5e",
        room_stroke="#00e5ff", grid_line="#00e5ff",
        label="#aef7ff", label_halo="#05060a",
    ),
)

# "dune" — Tatooine / sand medium: warm monochrome ochre everywhere; even the
# "sea" is a sand sea.
_DUNE = Theme(
    name="dune",
    biomes={
        Biome.OCEAN: "#c2a062", Biome.COAST: "#cdae6f", Biome.BEACH: "#e6d3a0",
        Biome.DESERT: "#e2c97c", Biome.PLAINS: "#d6bd76", Biome.FOREST: "#a99a54",
        Biome.SWAMP: "#8f8447", Biome.HILLS: "#c8b06a", Biome.MOUNTAIN: "#b59a63",
        Biome.TUNDRA: "#d8cba0", Biome.SNOW: "#efe6cf", Biome.RIVER: "#b7a866",
        Biome.LAKE: "#cdb985",
    },
    ocean_bg="#b8965a", coastline="#8a6e3c", river="#9c7a3e",
    road="#6e4e2a", road_casing="#efe2c2",
    region_border="#7a3a1e", region_label="#4a2410",
    settlement_fill="#efe2c2", settlement_stroke="#5a3a1a",
    label_fill="#3a2410", label_halo="#efe6cf",
    biome_names={Biome.OCEAN: "Sand Sea", Biome.FOREST: "Scrubland",
                 Biome.SWAMP: "Saltflat", Biome.LAKE: "Oasis"},
    settlement=SettlementPalette(
        countryside="#cbb277", footprint="#e0cd9c", water="#c2a062",
        ward_default="#d8c48f", ward_stroke="#7a5e34",
        building="#b89a63", building_stroke="#6e5230",
        road="#efe2c2", road_casing="#8a6e3c",
        wall="#7a5e34", tower_edge="#4a3a1c",
        label="#3a2410", label_halo="#efe6cf",
        wards={"market": "#e2c87c", "temple": "#e8dcb8", "noble": "#e0d2a0",
               "docks": "#cbbf95", "slums": "#c2ad78", "garrison": "#c8b074",
               "craftsmen": "#d2bd80"},
    ),
    dungeon=DungeonPalette(
        wall_bg="#3a2a14", floor="#cbb277", room_fill="#dcc78f",
        room_stroke="#6e5230", grid_line="#3a2410",
        label="#3a2410", label_halo="#efe6cf",
    ),
)

# "blueprint" — technical schematic: navy field, monochrome cyan line-work, with
# a contrasting orange for political borders.
_BLUEPRINT = Theme(
    name="blueprint",
    biomes={
        Biome.OCEAN: "#0a2236", Biome.COAST: "#123a55", Biome.BEACH: "#1d5570",
        Biome.DESERT: "#1f6a82", Biome.PLAINS: "#1a5e78", Biome.FOREST: "#14506a",
        Biome.SWAMP: "#103f54", Biome.HILLS: "#236f86", Biome.MOUNTAIN: "#2c7e92",
        Biome.TUNDRA: "#3a8ea0", Biome.SNOW: "#bfeeff", Biome.RIVER: "#1a5e78",
        Biome.LAKE: "#2a86a8",
    },
    ocean_bg="#071925", coastline="#8fe6ff", river="#8fe6ff",
    road="#cfeff8", road_casing="#0a2236",
    region_border="#ff9e3d", region_label="#ffce9e",
    settlement_fill="#8fe6ff", settlement_stroke="#071925",
    label_fill="#d8f4ff", label_halo="#071925",
    settlement=SettlementPalette(
        countryside="#071925", footprint="#0a2236", water="#0a2236",
        ward_default="#123a55", ward_stroke="#8fe6ff",
        building="#14506a", building_stroke="#8fe6ff",
        road="#cfeff8", road_casing="#0a2236",
        wall="#236f86", tower_edge="#8fe6ff",
        label="#d8f4ff", label_halo="#071925",
        wards={"market": "#1a5e78", "temple": "#236f86", "noble": "#2c7e92",
               "docks": "#103f54", "slums": "#0f3146", "garrison": "#143f54",
               "craftsmen": "#174a60"},
    ),
    dungeon=DungeonPalette(
        wall_bg="#071925", floor="#123a55", room_fill="#1a5e78",
        room_stroke="#8fe6ff", grid_line="#8fe6ff",
        label="#d8f4ff", label_halo="#071925",
    ),
)

THEMES: dict[str, Theme] = {t.name: t for t in (_PARCHMENT, _NEON, _DUNE, _BLUEPRINT)}
DEFAULT_THEME = "parchment"


def theme_names() -> list[str]:
    """Names of the built-in themes."""
    return list(THEMES)


def get_theme(theme: str | Theme) -> Theme:
    """Resolve a theme name (or pass a :class:`Theme` through). Raises ``KeyError``
    on an unknown name."""
    if isinstance(theme, Theme):
        return theme
    try:
        return THEMES[theme]
    except KeyError:
        raise KeyError(
            f"unknown theme {theme!r}; available: {', '.join(THEMES)}") from None
