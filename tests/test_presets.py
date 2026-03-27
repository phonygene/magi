"""Tests for persona presets."""
import pytest
from magi.presets import get_preset, list_presets, PRESETS
from magi.core.node import Persona


def test_list_presets():
    names = list_presets()
    assert "eva" in names
    assert "code-review" in names
    assert "research" in names
    assert "writing" in names
    assert "strategy" in names


def test_get_preset_eva():
    personas = get_preset("eva")
    assert len(personas) == 3
    assert all(isinstance(p, Persona) for p in personas)
    assert personas[0].name == "Melchior"


def test_get_preset_code_review():
    personas = get_preset("code-review")
    assert len(personas) == 3
    assert "Security" in personas[0].name


def test_get_preset_unknown():
    with pytest.raises(KeyError, match="Unknown preset"):
        get_preset("nonexistent")


def test_all_presets_have_three_personas():
    for name, personas in PRESETS.items():
        assert len(personas) == 3, f"Preset '{name}' has {len(personas)} personas, expected 3"


def test_all_personas_have_system_prompt():
    for name, personas in PRESETS.items():
        for p in personas:
            prompt = p.system_prompt
            assert p.name in prompt
            assert p.description in prompt
