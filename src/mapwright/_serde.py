"""Internal (de)serialisation helpers shared by the public dataclasses.

Keeps ``to_dict`` / ``from_dict`` / ``to_json`` / ``from_json`` consistent and
DRY without adding a dependency: everything round-trips through plain JSON-safe
builtins. Numpy arrays and :class:`~enum.IntEnum` fields are handled by each
owning class (only it knows its own field types); these helpers cover the parts
that are identical everywhere — dropping unknown keys on load, and the thin JSON
wrappers.

JSON preserves mapwright's seed-determinism: ``json`` formats floats with the
shortest round-tripping repr, so a serialised world deserialises to bit-identical
values (and thus an identical map).
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any


def only_known(cls, data: dict) -> dict[str, Any]:
    """Drop keys that aren't fields of dataclass ``cls``.

    Makes loads forward-compatible: a payload from a newer mapwright that grew a
    field (or carries a ``schema`` tag) still loads into an older one instead of
    raising ``TypeError`` for an unexpected keyword.
    """
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def to_json(obj: Any, **kwargs: Any) -> str:
    """JSON string for any object exposing ``to_dict()``. ``kwargs`` pass through
    to :func:`json.dumps` (e.g. ``indent=2``)."""
    return json.dumps(obj.to_dict(), **kwargs)


def from_json(cls, text: str):
    """Reconstruct via ``cls.from_dict(json.loads(text))``."""
    return cls.from_dict(json.loads(text))
