"""Unified seeded random-number generator for procedural map generation.

Ported from the discipline used by Azgaar's Fantasy-Map-Generator (MIT) and
Watabou's generators: a *single* seed drives *every* stage of generation, so
the same seed always reproduces the same world. The key idea that the previous
``random.seed(seed)`` / ``np.random.seed(seed)`` pattern lacked:

  * It mutated Python's **global** ``random`` module state, so any other code
    that drew from ``random`` (or any reordering of draws) silently changed the
    output. Here the stream is **instance-local** — nothing leaks in or out.
  * It kept two *uncoordinated* streams (stdlib ``random`` and ``numpy``). Here
    both are derived from one seed, so a single integer reproduces terrain
    *and* noise.
  * Adding a draw anywhere shifted everything downstream. :meth:`derive` gives
    each generation stage (terrain, naming, settlements, …) its own independent
    sub-stream keyed by a label, so stages can evolve without desyncing.

Example::

    rng = SeededRNG(request.seed)          # rng.seed is the resolved int seed
    terrain_rng = rng.derive("terrain")    # independent, reproducible sub-stream
    name_rng = rng.derive("names")         # adding/removing draws here cannot
                                           # shift terrain_rng's output
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional, Sequence, TypeVar

import numpy as np

T = TypeVar("T")

# Seeds are kept inside the signed 31-bit range so they round-trip cleanly
# through JSON, the ``generation_seed`` column, and numpy's seed API.
_SEED_MODULUS = 2**31


def _coerce_seed(seed: Optional[int]) -> int:
    """Resolve an optional seed to a concrete, reproducible 31-bit integer.

    When ``seed`` is ``None`` we draw a fresh one from the OS entropy pool and
    *return it* so callers can persist it and replay the exact same map later.
    """
    if seed is None:
        # SystemRandom is process-state-free, so picking the auto-seed never
        # perturbs any SeededRNG stream.
        return random.SystemRandom().randrange(_SEED_MODULUS)
    return int(seed) % _SEED_MODULUS


def _derive_seed(parent_seed: int, label: str) -> int:
    """Deterministically mix ``parent_seed`` with ``label`` into a child seed.

    Uses BLAKE2b rather than the builtin ``hash()`` because ``hash()`` of a
    string is salted per-process (``PYTHONHASHSEED``) and would make derived
    streams non-reproducible across runs.
    """
    digest = hashlib.blake2b(
        f"{parent_seed}:{label}".encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") % _SEED_MODULUS


class SeededRNG:
    """A reproducible, instance-local random stream with derivable sub-streams.

    Wraps a private :class:`random.Random` and a private
    :class:`numpy.random.Generator`, both seeded from the same integer. None of
    the methods touch global RNG state.
    """

    __slots__ = ("seed", "_rng", "_np")

    def __init__(self, seed: Optional[int] = None):
        self.seed: int = _coerce_seed(seed)
        self._rng = random.Random(self.seed)
        self._np: Optional[np.random.Generator] = None  # lazily constructed

    # -- sub-streams -----------------------------------------------------

    def derive(self, label: str) -> "SeededRNG":
        """Return an independent child stream keyed by ``label``.

        Same parent seed + same label always yields the same child, but the
        child's draws are statistically independent of the parent's and of
        sibling labels. This is how generation stages stay decoupled.
        """
        return SeededRNG(_derive_seed(self.seed, label))

    @property
    def numpy(self) -> np.random.Generator:
        """A numpy ``Generator`` seeded from the same seed (for vectorised noise)."""
        if self._np is None:
            self._np = np.random.default_rng(self.seed)
        return self._np

    # -- scalar draws ----------------------------------------------------

    def random(self) -> float:
        """Float in ``[0.0, 1.0)``."""
        return self._rng.random()

    def uniform(self, low: float, high: float) -> float:
        """Float in ``[low, high)``."""
        return self._rng.uniform(low, high)

    def randint(self, low: int, high: int) -> int:
        """Integer in ``[low, high]`` (inclusive on both ends, like stdlib)."""
        return self._rng.randint(low, high)

    def chance(self, probability: float) -> bool:
        """``True`` with the given probability (Watabou's ``Random.bool``)."""
        return self._rng.random() < probability

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """A normally-distributed float."""
        return self._rng.gauss(mu, sigma)

    def fuzzy(self, value: float, spread: float) -> float:
        """Jitter ``value`` by ``±spread`` uniformly.

        Watabou uses this constantly to break up grid regularity (e.g. nudging
        polygon vertices). ``rng.fuzzy(10, 2)`` returns a float in ``[8, 12)``.
        """
        return value + self._rng.uniform(-spread, spread)

    # -- sequence draws --------------------------------------------------

    def choice(self, seq: Sequence[T]) -> T:
        """Uniformly pick one element."""
        return self._rng.choice(seq)

    def choices(
        self,
        population: Sequence[T],
        weights: Optional[Sequence[float]] = None,
        k: int = 1,
    ) -> list[T]:
        """Weighted sampling with replacement (stdlib semantics)."""
        return self._rng.choices(population, weights=weights, k=k)

    def weighted(self, options: dict[T, float]) -> T:
        """Pick one key from a ``{value: weight}`` mapping, proportional to weight.

        The bread-and-butter of Azgaar's culture/state/biome selection.
        """
        keys = list(options.keys())
        weights = list(options.values())
        return self._rng.choices(keys, weights=weights, k=1)[0]

    def shuffle(self, seq: list) -> None:
        """In-place shuffle."""
        self._rng.shuffle(seq)

    def sample(self, population: Sequence[T], k: int) -> list[T]:
        """Sample ``k`` distinct elements (without replacement)."""
        return self._rng.sample(list(population), k)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SeededRNG(seed={self.seed})"
