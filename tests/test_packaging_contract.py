"""Executable check for the numpy-only rule.

ROADMAP states it plainly - "Single runtime dep: numpy. Keep it that way." - and until
now nothing enforced it. It is recorded as a DesignRule in the project's reflow2 design,
marked gate-blocking, and detected here.

The rule has two halves and both are checked: the core declares exactly numpy, AND
Pillow stays isolated in the `atlas` extra. The second half is the one that rots
quietly, because adding Pillow to the core would make every atlas test pass.
"""

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 - the floor we claim to support, so the check must work here too
    import tomli as tomllib

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

#: The whole of the core's runtime surface.
ALLOWED_RUNTIME = {"numpy"}

#: Optional-feature distributions that must NEVER appear in the core.
EXTRA_ONLY = {"pillow": "atlas"}


def _config() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _dist_name(requirement: str) -> str:
    """'numpy>=1.26' -> 'numpy'; normalised per PEP 503."""
    head = re.split(r"[\s\[<>=!~;(]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def _runtime() -> set:
    return {_dist_name(r) for r in _config()["project"]["dependencies"]}


class TestNumpyOnly:
    def test_core_runtime_dependencies_are_exactly_numpy(self):
        declared = _runtime()
        assert declared == ALLOWED_RUNTIME, (
            f"core runtime dependencies drifted: {sorted(declared)!r}. mapwright is "
            f"numpy-only on purpose - a host embeds it and pays for every transitive "
            f"dependency it brings. Moving the core is a deliberate decision."
        )

    @pytest.mark.parametrize("dist,extra", sorted(EXTRA_ONLY.items()))
    def test_optional_feature_stays_in_its_extra(self, dist, extra):
        assert dist not in _runtime(), (
            f"{dist!r} has moved into the core. It belongs in the {extra!r} extra: "
            f"rendering is optional and the core must install without it."
        )
        extras = _config()["project"].get("optional-dependencies", {})
        assert extra in extras, f"the {extra!r} extra has disappeared"
        assert dist in {_dist_name(r) for r in extras[extra]}, (
            f"{dist!r} is no longer declared in the {extra!r} extra, so installing "
            f"{extra!r} would not give you a working renderer."
        )

    def test_core_imports_without_the_optional_extra(self):
        """The rule's real consequence: `pip install mapwright` must work bare."""
        import mapwright  # noqa: F401  - the import IS the assertion

        assert mapwright.__all__, "the package imported but exports nothing"
