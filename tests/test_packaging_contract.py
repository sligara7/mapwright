"""Executable checks for the packaging rules this project has adopted.

Both were stated in ROADMAP and enforced by nothing. They are now recorded as
gate-blocking DesignRules in the project's reflow2 design, and detected here.

  * numpy-only - ROADMAP states it plainly: "Single runtime dep: numpy. Keep it that
    way." The rule has two halves and both are checked: the core declares exactly numpy,
    AND Pillow stays isolated in the `atlas` extra. The second half is the one that rots
    quietly, because adding Pillow to the core would leave every atlas test passing.

  * the lint gate states its own rule set - adopted 2026-08-15 AFTER it cost us: `ruff`
    was declared open-ended as `ruff>=0.1` with no `[tool.ruff.lint]` table, so CI
    resolved forward to a ruff whose wider DEFAULTS put main red on all four
    interpreters with 121 findings in files nobody had touched. dndwright hit the same
    thing first and pinned; mapwright had not adopted the rule. Now it has.
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
        # The import itself is half the assertion: it must succeed with only numpy
        # installed. The __all__ read is the other half, and it also means the name
        # is genuinely used - so no `noqa` is warranted here.
        import mapwright

        assert mapwright.__all__, "the package imported but exports nothing"


class TestLintGateStatesItsOwnRules:
    """Adopted 2026-08-15, after an open-ended ruff spec put main red.

    Mirrors dndwright's rule of the same name. Both halves matter and the second is
    the one that actually bit: a `select` list alone does not help if the linter that
    reads it can be replaced by a newer one on the next CI run.
    """

    def test_ruleset_is_stated_explicitly(self):
        select = _config().get("tool", {}).get("ruff", {}).get("lint", {}).get("select")
        assert select, (
            "[tool.ruff.lint] select is missing or empty, so the gate inherits ruff's "
            "DEFAULTS. That is exactly what put main red on 2026-08-15: a newer ruff "
            "widened them and 121 findings appeared in untouched files. State the rule "
            "set explicitly."
        )

    def test_ruff_is_pinned_on_both_sides(self):
        dev = _config()["project"]["optional-dependencies"]["dev"]
        ruff = next((r for r in dev if _dist_name(r) == "ruff"), None)
        assert ruff is not None, "ruff is not declared in the dev extra"
        assert "<" in ruff, (
            f"ruff is not bounded above ({ruff!r}). CI installs this extra fresh on "
            f"every run, so an open-ended spec adopts whatever ruff shipped that "
            f"morning and the gate's meaning changes without a commit. This is the "
            f"exact defect that put main red on 2026-08-15."
        )
        assert ">" in ruff or "==" in ruff, (
            f"ruff has no lower bound ({ruff!r}); the explicit rule set needs the "
            f"[tool.ruff.lint] table, which older ruff does not read."
        )
