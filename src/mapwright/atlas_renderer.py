"""Hand-drawn / themed **atlas** renderer — stamps symbols from an external *art
pack* onto a :class:`~mapwright.terrain.TerrainResult` to produce a hand-drawn
fantasy-map look (mountains, forests, hills, settlements, sea decorations).

mapwright ships **no art**. An *art pack* is a set of symbol images plus a manifest
that maps mapwright's neutral concepts (``Biome``, ``land_age``, settlement size)
onto art "slots". A host (e.g. an image-generation service) produces packs to this
schema in any style — hand-drawn ink, neon, scrap — and this renderer just places
them. The neutral generation never changes; the pack is the skin.

Requires Pillow (an optional extra)::

    pip install "mapwright[atlas]"

Art-pack manifest (``manifest.json`` in the pack directory) — every key optional::

    {
      "name": "my-pack",
      "style": "hand-drawn",
      "colors": {"parchment": "#ecdfbf", "water": "#b5cad1",
                 "coast": "#463c2c", "label": "#2b2218"},
      "slots": {
        "<slot>": {"files": ["glob/or/list/*.png", ...],
                   "width": <tiles>, "anchor": "bottom" | "center"}
      }
    }

**Slots** the renderer asks for (each may have several variant files):

* terrain relief — ``mountain.young`` / ``mountain.mid`` / ``mountain.old``
  (chosen by ``land_age``), ``hill``, ``tree.pine`` / ``tree.deciduous`` /
  ``tree.cactus`` (by climate), ``dune``
* settlements — ``city.castle`` / ``city.large`` / ``city.town`` / ``city.village``
* decorations — ``decoration.creature`` / ``decoration.ship`` / ``decoration.compass``

A missing ``foo.bar`` slot falls back to the generic ``foo`` slot, so a coarse
pack still works. If **no** ``manifest.json`` is present, :meth:`ArtPack.from_directory`
auto-discovers slots from a conventional folder layout (Nortantis-style:
``mountains/{sharp,steep,eroded…}``, ``hills``, ``trees/{pine,deciduous,cacti}``,
``cities``, ``decorations/{creatures,ships,compass…}``, ``sand``).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from glob import glob
from io import BytesIO
from pathlib import Path
from typing import Optional, Sequence

from .terrain import Biome, TerrainResult, compute_cell_polygons


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without Pillow
        raise ImportError(
            "AtlasRenderer needs Pillow. Install the optional extra: "
            'pip install "mapwright[atlas]"'
        ) from exc
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


# Default symbol width (in map tiles) and anchor per base category.
_DEFAULT_WIDTH = {"mountain": 2.0, "hill": 1.5, "tree": 1.0, "dune": 0.9,
                  "city": 1.7, "decoration": 3.0}
_CENTER_ANCHORS = {"decoration"}  # everything else sits on the ground ("bottom")
_DEFAULT_COLORS = {"parchment": "#ecdfbf", "water": "#b5cad1",
                   "coast": "#463c2c", "label": "#2b2218"}


@dataclass
class Symbol:
    """One art symbol: a file path, its intended width (in tiles), and anchor."""

    path: str
    width: float
    anchor: str = "bottom"


@dataclass
class ArtPack:
    """An art pack: slot name → list of variant :class:`Symbol` s, plus colours."""

    slots: dict[str, list[Symbol]] = field(default_factory=dict)
    colors: dict[str, str] = field(default_factory=dict)
    name: str = ""

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_directory(cls, path: str | Path) -> "ArtPack":
        """Load a pack from ``path``. Uses ``manifest.json`` if present, else
        auto-discovers slots from a conventional folder layout."""
        root = Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"art pack directory not found: {root}")
        manifest = root / "manifest.json"
        if manifest.is_file():
            return cls._from_manifest(root, json.loads(manifest.read_text()))
        return cls._auto_discover(root)

    @classmethod
    def _from_manifest(cls, root: Path, data: dict) -> "ArtPack":
        colors = {**_DEFAULT_COLORS, **data.get("colors", {})}
        slots: dict[str, list[Symbol]] = {}
        for slot, spec in data.get("slots", {}).items():
            base = slot.split(".")[0]
            width = float(spec.get("width", _DEFAULT_WIDTH.get(base, 1.0)))
            anchor = spec.get("anchor") or ("center" if base in _CENTER_ANCHORS else "bottom")
            files: list[str] = []
            for pat in spec.get("files", []):
                files.extend(sorted(glob(str(root / pat), recursive=True)))
            slots[slot] = [Symbol(f, width, anchor) for f in files]
        return cls(slots=slots, colors=colors, name=data.get("name", root.name))

    @classmethod
    def _auto_discover(cls, root: Path) -> "ArtPack":
        slots: dict[str, list[Symbol]] = {}
        for f in sorted(glob(str(root / "**" / "*.png"), recursive=True)):
            rel = str(Path(f).relative_to(root)).lower().replace("\\", "/")
            slot = _classify(rel, Path(f).name.lower())
            if slot is None:
                continue
            base = slot.split(".")[0]
            anchor = "center" if base in _CENTER_ANCHORS else "bottom"
            slots.setdefault(slot, []).append(
                Symbol(f, _DEFAULT_WIDTH.get(base, 1.0), anchor))
        return cls(slots=slots, colors=dict(_DEFAULT_COLORS), name=root.name)

    # -- access ----------------------------------------------------------

    def pick(self, slot: str, rng: random.Random) -> Optional[Symbol]:
        """A random variant for ``slot``. Falls back to the generic base slot and
        then to any sibling under the same base (``mountain.mid`` → ``mountain`` →
        ``mountain.young``/``…``), so coarse or partial packs still resolve."""
        base = slot.split(".")[0]
        if self.slots.get(slot):
            return _choice(self.slots[slot], rng)
        if self.slots.get(base):
            return _choice(self.slots[base], rng)
        pool = [v for key in sorted(self.slots)
                if key == base or key.startswith(base + ".")
                for v in self.slots[key]]
        return _choice(pool, rng) if pool else None

    def color(self, key: str) -> str:
        return self.colors.get(key, _DEFAULT_COLORS.get(key, "#000000"))


def _choice(variants: list[Symbol], rng: random.Random) -> Symbol:
    return variants[rng.randrange(len(variants))]


def _classify(relpath: str, filename: str) -> Optional[str]:
    """Map a Nortantis-style relative path to an art slot (auto-discovery)."""
    if "mountain" in relpath:
        if "sharp" in relpath:
            return "mountain.young"
        if "erod" in relpath or "spire" in relpath or "worn" in relpath:
            return "mountain.old"
        return "mountain.mid"
    if "hill" in relpath:
        return "hill"
    if "pine" in relpath or "conifer" in relpath or "fir" in relpath:
        return "tree.pine"
    if "decid" in relpath or "broadleaf" in relpath or ("tree" in relpath):
        return "tree.deciduous"
    if "cact" in relpath:
        return "tree.cactus"
    if "compass" in relpath:
        return "decoration.compass"
    if "ship" in relpath or "boat" in relpath:
        return "decoration.ship"
    if "creature" in relpath or "monster" in relpath or "serpent" in relpath:
        return "decoration.creature"
    if "cit" in relpath or "town" in relpath or "village" in relpath:
        if any(k in filename for k in ("castle", "fortress", "keep", "tower", "citadel")):
            return "city.castle"
        if "city" in filename or "cathedral" in filename or "metropolis" in filename:
            return "city.large"
        if any(k in filename for k in ("village", "yurt", "farm", "windmill", "hut", "house")):
            return "city.village"
        return "city.town"
    if "sand" in relpath or "dune" in relpath:
        return "dune"
    return None


# Marker.kind (e.g. "settlement_city") → city slot.
_KIND_SLOT = {
    "settlement_city": "city.large",
    "settlement_town": "city.town",
    "settlement_village": "city.village",
    "settlement_castle": "city.castle",
}


class AtlasRenderer:
    """Renders a :class:`TerrainResult` to a hand-drawn-style PNG by stamping an
    :class:`ArtPack`'s symbols. Deterministic for a given ``seed``."""

    def __init__(self, pack: ArtPack, scale: float = 12.0, seed: int = 0,
                 density: float = 1.0):
        self.pack = pack
        self.scale = scale
        self.seed = seed
        self.density = density  # 0..~2 — fraction of eligible cells that get a symbol
        self._img_cache: dict[str, object] = {}

    def render(self, terrain: TerrainResult, markers: Optional[Sequence] = None, *,
               land_age: float = 0.5, show_labels: bool = True) -> bytes:
        """Render to PNG bytes. ``land_age`` (the value the terrain was generated
        with) selects young/old mountain symbols; ``markers`` are stamped as
        settlements."""
        img = self.render_image(terrain, markers, land_age=land_age, show_labels=show_labels)
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()

    def render_image(self, terrain: TerrainResult, markers: Optional[Sequence] = None, *,
                     land_age: float = 0.5, show_labels: bool = True):
        Image, ImageDraw, ImageFont = _require_pillow()
        s = self.scale
        W, H = int(terrain.width * s), int(terrain.height * s)
        polys = compute_cell_polygons(terrain.cells, terrain.width, terrain.height)
        rng = random.Random(self.seed)

        img = Image.new("RGBA", (W, H), _hex(self.pack.color("parchment")) + (255,))
        draw = ImageDraw.Draw(img)
        water_rgb = _hex(self.pack.color("water"))
        parch_rgb = _hex(self.pack.color("parchment"))
        coast_rgb = _hex(self.pack.color("coast"))

        # 1. Base land/sea fills.
        for c in terrain.cells:
            poly = polys.get(c.id)
            if not poly or len(poly) < 3:
                continue
            pts = [(x * s, y * s) for x, y in poly]
            fill = water_rgb if (c.is_water or c.is_lake) else parch_rgb
            draw.polygon(pts, fill=fill + (255,))

        # 2. Coastline (land-cell edges that border the sea).
        water_ids = {c.id for c in terrain.cells if c.is_water}
        for c in terrain.cells:
            if c.is_water:
                continue
            poly = polys.get(c.id)
            if not poly or len(poly) < 3:
                continue
            for i in range(len(poly)):
                a, b = poly[i], poly[(i + 1) % len(poly)]
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                nb = min(c.neighbors,
                         key=lambda k: (terrain.cells[k].cx - mx) ** 2
                         + (terrain.cells[k].cy - my) ** 2, default=None)
                if nb in water_ids:
                    draw.line([(a[0] * s, a[1] * s), (b[0] * s, b[1] * s)],
                              fill=coast_rgb + (255,), width=max(2, int(s * 0.22)))

        # 3. Terrain relief symbols, painted back-to-front (forests, then hills,
        #    then mountains) with greedy spacing so they don't overlap.
        groups: dict[str, list] = {}
        for c in terrain.cells:
            slot = self._slot_for(c, land_age)
            if slot:
                groups.setdefault(slot.split(".")[0], []).append((c, slot))
        for base in ("tree", "dune", "hill", "mountain"):
            self._place(img, groups.get(base, []), rng)

        # 4. Settlements.
        if markers:
            for m in markers:
                slot = _KIND_SLOT.get(getattr(m, "kind", ""), "city.town")
                self._stamp(img, slot, m.x * s, m.y * s, rng)
            if show_labels:
                self._labels(draw, markers, ImageFont)

        # 5. A few sea decorations + a compass rose.
        ocean = [c for c in terrain.cells if c.biome == Biome.OCEAN]
        rng.shuffle(ocean)
        for c in ocean[:3]:
            slot = "decoration.ship" if rng.random() < 0.5 else "decoration.creature"
            self._stamp(img, slot, c.cx * s, c.cy * s, rng, width_override=3.0)
        self._stamp(img, "decoration.compass", W - s * 4.5, H - s * 3.5, rng, width_override=6.0)
        return img

    # -- internals -------------------------------------------------------

    def _slot_for(self, cell, land_age: float) -> Optional[str]:
        b = cell.biome
        if b in (Biome.MOUNTAIN, Biome.SNOW):
            return ("mountain.young" if land_age < 0.4
                    else "mountain.old" if land_age > 0.6 else "mountain.mid")
        if b == Biome.HILLS:
            return "hill"
        if b == Biome.FOREST:
            return "tree.pine" if cell.temperature < 0.45 else "tree.deciduous"
        if b == Biome.DESERT:
            return "tree.cactus" if self.pack.slots.get("tree.cactus") else "dune"
        return None

    def _place(self, img, items, rng: random.Random) -> None:
        """Greedily stamp a group of (cell, slot) keeping a min spacing."""
        rng.shuffle(items)
        taken: list[tuple[float, float]] = []
        for cell, slot in items:
            if rng.random() > self.density:
                continue
            x, y = cell.cx * self.scale, cell.cy * self.scale
            sym = self.pack.pick(slot, rng)
            if sym is None:
                continue
            spacing = sym.width * self.scale * 0.8
            if any((x - tx) ** 2 + (y - ty) ** 2 < spacing * spacing for tx, ty in taken):
                continue
            self._stamp_symbol(img, sym, x, y)
            taken.append((x, y))

    def _stamp(self, img, slot: str, x: float, y: float, rng: random.Random,
               width_override: Optional[float] = None) -> None:
        sym = self.pack.pick(slot, rng)
        if sym is not None:
            self._stamp_symbol(img, sym, x, y, width_override)

    def _stamp_symbol(self, img, sym: Symbol, x: float, y: float,
                      width_override: Optional[float] = None) -> None:
        Image, _, _ = _require_pillow()
        src = self._img_cache.get(sym.path)
        if src is None:
            src = Image.open(sym.path).convert("RGBA")
            self._img_cache[sym.path] = src
        tile_w = (width_override if width_override is not None else sym.width) * self.scale
        scale = tile_w / src.width
        s = src.resize((max(1, int(src.width * scale)), max(1, int(src.height * scale))),
                       Image.LANCZOS)
        ox = int(x - s.width / 2)
        oy = int(y - s.height * (0.8 if sym.anchor == "bottom" else 0.5))
        img.alpha_composite(s, (ox, oy))

    def _labels(self, draw, markers, ImageFont) -> None:
        font = ImageFont.load_default()
        color = _hex(self.pack.color("label"))
        for m in markers:
            name = getattr(m, "name", "")
            if name:
                draw.text((m.x * self.scale + 6, m.y * self.scale - 4), name,
                          fill=color + (255,), font=font)


def _hex(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
