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

    # --- Climate ---
    temperature: float = 0.0
    """-1 (frozen) .. +1 (scorching) — global temperature bias."""
    moisture: float = 0.0
    """-1 (arid) .. +1 (drowned) — global moisture bias."""

    # --- Hydrology ---
    river_density: float = 0.5
    """0..1 — how readily rivers are traced (more ⇒ more, smaller rivers)."""

    def __post_init__(self) -> None:
        # Clamp everything so out-of-range inputs (e.g. from an LLM) are safe.
        self.sea_level = _clamp(self.sea_level, 0.05, 0.9)
        self.continents = int(_clamp(self.continents, 1, 24))
        self.continent_spread = _clamp(self.continent_spread, 0.0, 1.0)
        self.edge_falloff = _clamp(self.edge_falloff, 0.0, 2.0)
        self.mountain_density = _clamp(self.mountain_density, 0.0, 1.0)
        self.roughness = _clamp(self.roughness, 0.0, 1.0)
        self.temperature = _clamp(self.temperature, -1.0, 1.0)
        self.moisture = _clamp(self.moisture, -1.0, 1.0)
        self.river_density = _clamp(self.river_density, 0.0, 1.0)

    # -- serialisation / interop ----------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorldMapConfig":
        """Build from a (possibly partial / noisy) mapping; unknown keys ignored."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

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
               "mountain_density": 0.25, "river_density": 0.12},
    "arctic": {"temperature": -0.85, "moisture": 0.1, "mountain_density": 0.5},
    "tropical": {"temperature": 0.6, "moisture": 0.85, "river_density": 0.85,
                 "mountain_density": 0.55},
    "islands": {"continents": 12, "sea_level": 0.62, "continent_spread": 0.85,
                "mountain_density": 0.3},
}
