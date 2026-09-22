"""Tests for Hindsight's declared config surface."""

from plugins.memory.config_schema import (
    KIND_SECRET,
    KIND_SELECT,
    get_provider_config_schema,
)
from plugins.memory.hindsight import HindsightMemoryProvider


def test_hindsight_is_declared():
    provider = get_provider_config_schema("hindsight")

    assert provider is not None
    assert provider.label == "Hindsight"
    assert {field.key for field in provider.fields} == {
        "mode",
        "api_key",
        "api_url",
        "bank_id",
        "recall_budget",
    }


def test_fields_are_all_inline():
    provider = get_provider_config_schema("hindsight")
    assert provider is not None

    # Hindsight is simple enough to render fully in the compact panel, so it
    # never grows a Full config… modal.
    assert all(field.inline for field in provider.fields)


def test_mode_gating_is_expressed_as_select_options():
    provider = get_provider_config_schema("hindsight")
    assert provider is not None

    mode = next(field for field in provider.fields if field.key == "mode")
    assert mode.kind == KIND_SELECT
    assert mode.allowed_values() == {"cloud", "local_external"}
    # local_embedded is intentionally unsupported on desktop.
    assert "local_embedded" not in mode.allowed_values()


def test_api_key_is_a_secret_bound_to_env():
    provider = get_provider_config_schema("hindsight")
    assert provider is not None

    api_key = next(field for field in provider.fields if field.key == "api_key")
    assert api_key.kind == KIND_SECRET
    assert api_key.is_secret is True
    assert api_key.env_key == "HINDSIGHT_API_KEY"


def test_jev_shadow_defaults_are_declared_on_provider_schema():
    fields = {
        field["key"]: field
        for field in HindsightMemoryProvider().get_config_schema()
        if field["key"].startswith("jev_shadow_")
    }
    assert {"jev_shadow_enabled", "jev_shadow_model", "jev_shadow_target", "jev_shadow_timeout", "jev_shadow_max_queue", "jev_shadow_root"} <= fields.keys()
    assert fields["jev_shadow_enabled"]["default"] is False
    assert fields["jev_shadow_model"]["default"] == "jev-latest"
    assert fields["jev_shadow_target"]["default"] == 100
    assert fields["jev_shadow_timeout"]["default"] == 10.0
    assert fields["jev_shadow_max_queue"]["default"] == 32
    assert fields["jev_shadow_root"]["default"] == ""
