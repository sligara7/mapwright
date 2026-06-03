"""Tests for environmental affordances and cell aggregation."""

import pytest

from mapwright import (
    Biome,
    CellSummary,
    RegionalTerrainGenerator,
    SeededRNG,
    environment_affordances,
    summarize_cells,
)
from mapwright.terrain import TerrainCell


def _cell(biome=Biome.PLAINS, temperature=0.5, moisture=0.5, height=0.5, **kw):
    return TerrainCell(
        id=kw.pop("id", 0),
        cx=0.0,
        cy=0.0,
        biome=biome,
        temperature=temperature,
        moisture=moisture,
        height=height,
        **kw,
    )


# -- environment_affordances ------------------------------------------------


def test_biome_base_tags_present():
    tags = environment_affordances(Biome.DESERT, temperature=0.5, moisture=0.5)
    assert "scarce_water" in tags
    assert "exposure" in tags


def test_steamy_jungle_gets_predator_and_disease():
    # hot + wet forest == the "steamy jungle": predators (base) + biting-insect
    # disease vectors (humid) + heat.
    tags = environment_affordances(Biome.FOREST, temperature=0.9, moisture=0.9)
    assert "predator" in tags          # base forest
    assert "disease_vector" in tags    # humid
    assert "extreme_heat" in tags      # hot


def test_arid_adds_scarce_water_even_off_desert():
    tags = environment_affordances(Biome.PLAINS, temperature=0.5, moisture=0.05)
    assert "scarce_water" in tags


def test_cold_adds_extreme_cold():
    tags = environment_affordances(Biome.PLAINS, temperature=0.05, moisture=0.5)
    assert "extreme_cold" in tags


def test_tags_are_deduped_and_stable():
    # desert base already has scarce_water + extreme_heat; hot + arid would
    # re-add them — result must not contain duplicates, and must be reproducible.
    tags = environment_affordances(Biome.DESERT, temperature=0.95, moisture=0.05)
    assert len(tags) == len(set(tags))
    assert tags == environment_affordances(Biome.DESERT, temperature=0.95, moisture=0.05)


# -- summarize_cells --------------------------------------------------------


def test_summarize_empty_raises():
    with pytest.raises(ValueError):
        summarize_cells([])


def test_dominant_biome_is_modal():
    cells = [
        _cell(biome=Biome.FOREST),
        _cell(biome=Biome.FOREST),
        _cell(biome=Biome.SWAMP),
    ]
    assert summarize_cells(cells).dominant_biome is Biome.FOREST


def test_dominant_biome_tie_breaks_to_lowest_value():
    # FOREST(5) vs SWAMP(6) tied at 1 each -> deterministic lowest value wins.
    cells = [_cell(biome=Biome.SWAMP), _cell(biome=Biome.FOREST)]
    assert summarize_cells(cells).dominant_biome is Biome.FOREST


def test_means_and_flags():
    cells = [
        _cell(temperature=0.2, moisture=0.4, height=0.6, is_river=True),
        _cell(temperature=0.6, moisture=0.8, height=0.8, is_lake=True),
    ]
    s = summarize_cells(cells)
    assert s.temperature == pytest.approx(0.4)
    assert s.moisture == pytest.approx(0.6)
    assert s.mean_height == pytest.approx(0.7)
    assert s.has_river is True
    assert s.has_lake is True
    assert s.cell_count == 2


def test_water_fraction():
    cells = [_cell(is_water=True), _cell(is_lake=True), _cell()]
    assert summarize_cells(cells).water_fraction == pytest.approx(2 / 3)


def test_summary_carries_affordances_for_dominant_biome():
    cells = [_cell(biome=Biome.DESERT, temperature=0.9, moisture=0.05)]
    s = summarize_cells(cells)
    assert isinstance(s, CellSummary)
    assert "scarce_water" in s.affordances


def test_summarize_over_a_real_generated_world():
    world = RegionalTerrainGenerator(SeededRNG(7)).generate(44, 30)
    land = [c for c in world.cells if not c.is_water]
    s = summarize_cells(land)
    assert s.cell_count == len(land)
    assert 0.0 <= s.temperature <= 1.0
    assert 0.0 <= s.moisture <= 1.0
    assert s.affordances  # land always affords *something*
    # reproducible
    assert summarize_cells(land).affordances == s.affordances
