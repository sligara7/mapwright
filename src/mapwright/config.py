"""Tunable world-generation parameters.

``WorldMapConfig`` is the single knob-set that shapes a generated world. Every
field is a bounded scalar or small int with a clear semantic and a default that
yields a balanced single-continent world — so it doubles as a **schema a host
app (or an LLM) can populate from a description**: "a frozen archipelago of
scattered isles" → ``WorldMapConfig(continents=7, sea_level=0.55, temperature=-0.8)``.

The library deliberately depends only on numpy, so this is a plain dataclass (no
pydantic). A host that wants JSON-schema/LLM population can mirror these fields
in its own model and call :meth:`WorldMapConfig.from_dict` (which clamps to
valid ranges, so a sloppy LLM payload can't produce a broken world).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# Single source of truth for every knob: (name, type, min, max, description).
# Drives both __post_init__ clamping and json_schema(), so the validation
# behaviour and the published contract can never disagree.
_SPEC: list[tuple] = [
    ("sea_level", float, 0.05, 0.9,
     "Fraction of the height range below water. Higher = more ocean."),
    ("continents", int, 1, 24,
     "Number of major landmasses. 1 = single continent; 4-8 = archipelago."),
    ("continent_spread", float, 0.0, 1.0,
     "0 = landmasses cluster at the centre; 1 = pushed toward the edges."),
    ("edge_falloff", float, 0.0, 2.0,
     "0 = land may reach the map border; 1 = strong 'ringed by sea' coastline."),
    ("mountain_density", float, 0.0, 1.0,
     "Abundance and height of hills/ranges."),
    ("roughness", float, 0.0, 1.0,
     "Terrain detail (number of erosion passes)."),
    ("land_age", float, 0.0, 1.0,
     "Geological age: 0 = young, jagged, tall peaks; 1 = old, worn, rounded."),
    ("temperature", float, -1.0, 1.0,
     "Global temperature bias: -1 frozen .. +1 scorching."),
    ("moisture", float, -1.0, 1.0,
     "Global moisture bias: -1 arid .. +1 drowned."),
    ("river_density", float, 0.0, 1.0,
     "How readily rivers are traced; more = more, smaller rivers."),
    ("lake_density", float, 0.0, 1.0,
     "How readily inland basins fill into lakes; more = more, shallower lakes."),
    ("polar_cold", float, 0.0, 1.0,
     "Strength of the equator→pole temperature gradient. 0 = no cold caps "
     "(uniformly warm); 1 = strong polar ice caps with snow near the top/bottom "
     "edges. Latitude (north/south) sets where the cold is; this sets how much."),
]


@dataclass
class WorldMapConfig:
    """Knobs for :meth:`RegionalTerrainGenerator.generate`. Defaults = baseline."""

    # --- Topology / landmass ---
    sea_level: float = 0.32
    """0..1 — fraction of the height range below water. Higher ⇒ more ocean."""
    continents: int = 1
    """Number of major landmasses. 1 = single continent; 4–8 ⇒ archipelago."""
    continent_spread: float = 0.5
    """0 ⇒ landmasses cluster at the centre; 1 ⇒ pushed toward the edges."""
    edge_falloff: float = 1.0
    """0 ⇒ land may reach the map border; 1 ⇒ strong 'ringed by sea' coastline."""

    # --- Relief ---
    mountain_density: float = 0.5
    """0..1 — abundance and height of hills/ranges."""
    roughness: float = 0.5
    """0..1 — terrain detail (drives the number of erosion passes)."""
    land_age: float = 0.5
    """0 (young: jagged, tall peaks) .. 1 (old: worn, rounded, lower)."""

    # --- Climate ---
    temperature: float = 0.0
    """-1 (frozen) .. +1 (scorching) — global temperature bias."""
    moisture: float = 0.0
    """-1 (arid) .. +1 (drowned) — global moisture bias."""

    # --- Hydrology ---
    river_density: float = 0.5
    """0..1 — how readily rivers are traced (more ⇒ more, smaller rivers)."""
    lake_density: float = 0.5
    """0..1 — how readily inland basins fill into lakes (more ⇒ more lakes)."""

    # --- Climate (appended for contract stability; see EXPECTED_FIELDS) ---
    polar_cold: float = 0.5
    """0 ⇒ no polar chill (uniformly warm) .. 1 ⇒ strong cold ice caps at the
    poles. Latitude sets *where* the cold falls (top/bottom edges); this knob
    sets *how strong* the equator→pole gradient is."""

    def __post_init__(self) -> None:
        # Clamp everything so out-of-range inputs (e.g. from an LLM) are safe.
        for name, typ, lo, hi, _desc in _SPEC:
            value = _clamp(getattr(self, name), lo, hi)
            setattr(self, name, int(value) if typ is int else float(value))

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorldMapConfig":
        """Build from a (possibly partial / noisy) mapping; unknown keys ignored."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def json_schema(cls) -> dict:
        """A JSON Schema (draft 2020-12) describing this config.

        This is mapwright's machine-readable **contract** for world parameters:
        a host app or an LLM can validate/generate payloads against it, then feed
        them through :meth:`from_dict` (which additionally clamps). Generated from
        the same field spec used for clamping, so schema and behaviour can't drift.
        """
        defaults = cls()
        properties = {
            name: {
                "type": "integer" if typ is int else "number",
                "minimum": lo,
                "maximum": hi,
                "default": getattr(defaults, name),
                "description": desc,
            }
            for name, typ, lo, hi, desc in _SPEC
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "WorldMapConfig",
            "description": "Parameters that shape a mapwright world.",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        }

    # -- presets (also demonstrate the knobs & seed LLM choices) --------

    @classmethod
    def preset(cls, name: str) -> "WorldMapConfig":
        """A named starting point. Raises KeyError for an unknown preset."""
        return cls.from_dict(dict(PRESETS[name]))

    @staticmethod
    def preset_names() -> list[str]:
        return sorted(PRESETS.keys())


# Named presets — ready-made worlds and good LLM anchors. Each maps to a kind of
# narrative setting; a host can expose these by name or let an LLM pick + tweak.
PRESETS: dict[str, dict] = {
    "continent": {},  # the balanced default
    "pangaea": {"continents": 1, "sea_level": 0.22, "continent_spread": 0.15,
                "edge_falloff": 0.55},
    "archipelago": {"continents": 7, "sea_level": 0.55, "continent_spread": 0.75,
                    "mountain_density": 0.4},
    "highlands": {"continents": 1, "mountain_density": 0.95, "roughness": 0.75,
                  "river_density": 0.7},
    "desert": {"temperature": 0.85, "moisture": -0.85, "sea_level": 0.28,
               "mountain_density": 0.25, "river_density": 0.12, "lake_density": 0.1,
               "polar_cold": 0.0},  # scorching pole-to-pole, no ice caps
    "arctic": {"temperature": -0.85, "moisture": 0.1, "mountain_density": 0.5,
               "polar_cold": 0.9},  # deep, wide ice caps
    "tropical": {"temperature": 0.6, "moisture": 0.85, "river_density": 0.85,
                 "mountain_density": 0.55, "polar_cold": 0.2},  # warm to the poles
    "islands": {"continents": 12, "sea_level": 0.62, "continent_spread": 0.85,
                "mountain_density": 0.3},
    # A whole planet: many distinct continents of varied size spread to the
    # edges, ~⅓ land, polar ice caps, and islands/arcs in the oceans between.
    # Best rendered on a WIDE canvas (e.g. generate(240, 130)) so the continents
    # read as a world rather than one zoomed-in landmass.
    "world": {"continents": 8, "sea_level": 0.64, "continent_spread": 0.95,
              "mountain_density": 0.7, "polar_cold": 0.5},
}
