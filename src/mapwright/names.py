"""Procedural name generation via character-level Markov chains.

Ported (clean-room) from the technique in Azgaar's Fantasy-Map-Generator (MIT):
train a Markov chain on a small list of example names that share a linguistic
"feel", then walk the chain to emit *new* names in the same style. This is
cheap, fully offline (no LLM call), deterministic given a :class:`SeededRNG`,
and language-agnostic — swap the training list to change the culture.

Why a Markov chain rather than just sampling syllables: an order-k chain
captures local letter-transition statistics (which clusters are plausible in a
given language) without needing a phonotactic grammar. Order 3 over the seed
lists below gives names that read as "Nordic" or "Elvish" without ever copying
a training word verbatim (we reject exact training-set hits).

The seed namebases here are intentionally compact, hand-authored word lists —
*not* copied from any GPL/unlicensed generator — so the output and this module
are clean for reuse. They're large enough for an order-3 chain; extend them
freely (more examples → richer output).

Usage::

    rng = SeededRNG(seed)
    namer = NameGenerator(rng)
    namer.place("nordic")          # -> "Skjoldur"
    namer.settlement("generic")    # -> "Eldmoor"  (base name + culture suffix)
    namer.person("elvish")         # -> "Aelirien"
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .rng import SeededRNG

# Sentinel marking a word boundary in the Markov context/emission alphabet.
_BOUNDARY = "\x00"

# ---------------------------------------------------------------------------
# Seed namebases — hand-authored, license-clean training lists, one per culture.
# Order-3 chains need only a couple dozen examples to produce varied output.
# ---------------------------------------------------------------------------

NAMEBASES: dict[str, list[str]] = {
    "generic": [
        "Aldwin", "Brennan", "Caldor", "Dunmere", "Eldric", "Faron",
        "Garrick", "Hadwin", "Ironholt", "Kelmoor", "Lannet", "Marden",
        "Norwick", "Oakheart", "Pelmont", "Ravenwood", "Stonefel", "Thornby",
        "Ulmar", "Vendry", "Westmere", "Wyndham", "Yardley", "Ashford",
        "Briarcliff", "Greymoor", "Holloway", "Redwyn", "Tarnwick",
    ],
    "nordic": [
        "Bjorn", "Sigurd", "Ragnar", "Eirik", "Halldor", "Gunnar", "Ivar",
        "Knut", "Leif", "Olaf", "Sten", "Torvald", "Ulf", "Vidar", "Asgeir",
        "Skuli", "Hakon", "Brandr", "Geirmund", "Snorri", "Thorgil",
        "Frosta", "Helga", "Ingrid", "Sigrun", "Yngvar", "Skjold", "Dagny",
    ],
    "latin": [
        "Marcus", "Valeria", "Cassius", "Aurelia", "Decimus", "Octavia",
        "Tiberius", "Lucilla", "Quintus", "Flavia", "Septimus", "Antonia",
        "Cornelius", "Drusilla", "Gaius", "Livia", "Maximus", "Vipsania",
        "Aelius", "Camilla", "Tarquin", "Sabina", "Verus", "Junia",
    ],
    "elvish": [
        "Aelar", "Caelynn", "Eluvian", "Faelar", "Galadriel", "Ithilien",
        "Laeroth", "Mirelle", "Naerith", "Oromis", "Saelihn", "Thalion",
        "Aerendyl", "Cithreth", "Elarian", "Faewyn", "Illianor", "Lithriel",
        "Nimriel", "Sylvaris", "Vaelynn", "Aelirien", "Maeglin", "Tinuviel",
    ],
    "dwarvish": [
        "Borin", "Durgan", "Thrain", "Balgrim", "Kragdor", "Norund",
        "Gimrek", "Brundal", "Khazek", "Morgrum", "Thoradin", "Brokkur",
        "Dvalin", "Grundvik", "Hrothgar", "Kazrik", "Orin", "Throndur",
        "Belmund", "Garrundr", "Korvald", "Stonebeard", "Ironfist", "Deepdelve",
    ],
    "eastern": [
        "Akihiro", "Renjiro", "Haruki", "Kenshin", "Daichi", "Sora",
        "Takeo", "Yorimoto", "Ryusei", "Kaede", "Michiyo", "Tamaki",
        "Hideaki", "Naoki", "Shinobu", "Yukihiro", "Asuka", "Reiko",
        "Toshiro", "Mizuki", "Hayato", "Sayuri", "Kojiro", "Emiko",
    ],
    "desert": [
        "Aziz", "Farran", "Jamil", "Karim", "Nasir", "Rashid", "Tariq",
        "Yusuf", "Zahir", "Amina", "Layla", "Nadira", "Samira", "Zaida",
        "Hakim", "Ibrahim", "Khalil", "Mansur", "Rafiq", "Saladin",
        "Bahram", "Cyrus", "Darius", "Faridun",
    ],
    "dark": [
        "Malketh", "Vornak", "Drathys", "Skorvath", "Nyxara", "Vexmoor",
        "Gorthak", "Zultharn", "Morvath", "Skarn", "Velkith", "Drusk",
        "Azgoth", "Korrath", "Nethyr", "Sablethorn", "Grimwald", "Vauldrek",
        "Mortessa", "Shaelgar", "Thessaroth", "Ulvyn", "Xathorne", "Zerith",
    ],
}

# Optional culture-flavoured settlement suffixes, appended to a base name to
# form place names ("Eld" + "moor" -> "Eldmoor"). Mirrors how Azgaar composes
# burg names from a root plus a geographic suffix.
SETTLEMENT_SUFFIXES: dict[str, list[str]] = {
    "generic": ["ton", "ford", "moor", "wick", "bury", "hill", "dale", "field",
                "gate", "haven", "crest", "hollow", "march", "watch"],
    "nordic": ["heim", "vik", "fjord", "gard", "stad", "ness", "fell", "by"],
    "latin": ["um", "ium", "polis", "ara", "ena", "ica", "anum"],
    "elvish": ["wood", "lond", "thil", "mar", "vale", "shire", "loth", "wen"],
    "dwarvish": ["delve", "hold", "forge", "deep", "barrow", "mount", "karak"],
    "eastern": ["mura", "gawa", "yama", "shiro", "do", "ji", "saki"],
    "desert": ["abad", "stan", "kar", "oasis", "dune", "mir", "sah"],
    "dark": ["spire", "gloom", "barrow", "throne", "mire", "fang", "shroud"],
}

# A reasonable default when a caller asks for an unknown culture.
_FALLBACK_CULTURE = "generic"


class MarkovNameGenerator:
    """An order-``k`` character-level Markov chain trained on one word list."""

    def __init__(self, words: list[str], order: int = 3):
        self.order = order
        # Keep a clean, lowercased copy of the training words so we can reject
        # exact reproductions and know plausible length bounds.
        self._training = {w.lower() for w in words if w and w.isalpha()}
        self._min_len, self._max_len = self._length_bounds(self._training)
        self._chain: dict[str, list[str]] = self._build_chain(self._training)

    @staticmethod
    def _length_bounds(words: set[str]) -> tuple[int, int]:
        if not words:
            return 4, 10
        lengths = [len(w) for w in words]
        return max(3, min(lengths)), max(lengths) + 2

    def _build_chain(self, words: set[str]) -> dict[str, list[str]]:
        """Map each k-char context to the list of characters that follow it.

        We store the raw (multiset) list rather than a Counter so that picking a
        next char with ``rng.choice`` is automatically frequency-weighted.
        Words are padded with ``order`` boundary sentinels on each side.

        ``words`` is iterated in **sorted** order, not set order: the follower
        lists are positional, so set iteration (salted by ``PYTHONHASHSEED``)
        would make ``rng.choice`` pick differently across processes and break
        the library's seed-reproducibility guarantee.
        """
        chain: dict[str, list[str]] = defaultdict(list)
        pad = _BOUNDARY * self.order
        for word in sorted(words):
            padded = pad + word + _BOUNDARY
            for i in range(len(padded) - self.order):
                context = padded[i : i + self.order]
                nxt = padded[i + self.order]
                chain[context].append(nxt)
        return dict(chain)

    def generate(
        self,
        rng: SeededRNG,
        min_len: Optional[int] = None,
        max_len: Optional[int] = None,
        max_attempts: int = 40,
    ) -> str:
        """Walk the chain to produce a single capitalised name.

        Retries up to ``max_attempts`` times to satisfy the length bounds and
        avoid echoing a training word; falls back to the best candidate seen.
        """
        if not self._chain:
            return ""
        lo = min_len if min_len is not None else self._min_len
        hi = max_len if max_len is not None else self._max_len
        pad = _BOUNDARY * self.order

        best = ""
        for _ in range(max_attempts):
            context = pad
            out: list[str] = []
            while True:
                followers = self._chain.get(context)
                if not followers:
                    break
                nxt = rng.choice(followers)
                if nxt == _BOUNDARY:
                    break
                out.append(nxt)
                if len(out) >= hi:  # hard stop on runaway chains
                    break
                context = (context + nxt)[-self.order :]
            word = "".join(out)
            if len(word) > len(best):
                best = word
            if lo <= len(word) <= hi and word not in self._training:
                return word.capitalize()
        return best.capitalize() if best else ""


class NameGenerator:
    """High-level, culture-aware naming facade over per-culture Markov chains.

    Holds a :class:`SeededRNG` and lazily builds (and caches) one
    :class:`MarkovNameGenerator` per culture. All draws go through a derived
    ``"names"`` sub-stream so naming stays decoupled from terrain generation —
    adding a name call never shifts the terrain RNG.
    """

    def __init__(self, rng: SeededRNG, order: int = 3):
        self._rng = rng.derive("names")
        self._order = order
        self._chains: dict[str, MarkovNameGenerator] = {}

    @staticmethod
    def cultures() -> list[str]:
        """The set of built-in culture keys available for naming."""
        return sorted(NAMEBASES.keys())

    def _chain_for(self, culture: str) -> MarkovNameGenerator:
        key = culture if culture in NAMEBASES else _FALLBACK_CULTURE
        if key not in self._chains:
            self._chains[key] = MarkovNameGenerator(NAMEBASES[key], self._order)
        return self._chains[key]

    # -- public naming API ----------------------------------------------

    def place(self, culture: str = "generic") -> str:
        """A bare place/region name in the given culture's style."""
        return self._chain_for(culture).generate(self._rng)

    def person(self, culture: str = "generic") -> str:
        """A personal name in the given culture's style."""
        return self._chain_for(culture).generate(self._rng)

    def settlement(self, culture: str = "generic", suffix_chance: float = 0.65) -> str:
        """A settlement name: a root name, often plus a geographic suffix.

        e.g. ``"Eld" + "moor" -> "Eldmoor"``. With probability
        ``1 - suffix_chance`` the bare root is returned instead, for variety.
        """
        root = self.place(culture)
        if not root:
            return root
        suffixes = SETTLEMENT_SUFFIXES.get(culture) or SETTLEMENT_SUFFIXES[_FALLBACK_CULTURE]
        if suffixes and self._rng.chance(suffix_chance):
            # Drop a trailing vowel before a vowel-initial suffix to avoid
            # awkward clusters ("Elda" + "moor" -> "Eldmoor").
            suffix = self._rng.choice(suffixes)
            if root[-1].lower() in "aeiou" and suffix[0].lower() not in "aeiou":
                root = root[:-1]
            return (root + suffix).capitalize()
        return root

    def region(self, culture: str = "generic") -> str:
        """A region/territory name, e.g. "The Tarnwick Reach"."""
        forms = [
            "{name}",
            "The {name} Reach",
            "{name} March",
            "{name}land",
            "Vale of {name}",
            "The {name} Wastes",
        ]
        name = self.place(culture)
        return self._rng.choice(forms).format(name=name) if name else name
