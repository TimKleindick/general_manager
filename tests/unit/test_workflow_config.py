from __future__ import annotations

from types import SimpleNamespace

from general_manager.workflow.config import (
    workflow_async_enabled,
    workflow_beat_enabled,
    workflow_beat_max_jitter_seconds,
    workflow_beat_outbox_interval_seconds,
    workflow_dead_letter_enabled,
    workflow_delivery_running_timeout_seconds,
    workflow_max_retries,
    workflow_mode,
    workflow_outbox_batch_size,
    workflow_outbox_claim_ttl_seconds,
    workflow_outbox_process_chunk_size,
    workflow_retry_backoff_seconds,
)


def test_workflow_mode_uses_nested_setting_and_normalizes() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={"WORKFLOW_MODE": " Production "},
        WORKFLOW_MODE="local",
    )

    assert workflow_mode(django_settings) == "production"


def test_workflow_mode_defaults_unknown_values_to_local() -> None:
    django_settings = SimpleNamespace(GENERAL_MANAGER={"WORKFLOW_MODE": "staging"})

    assert workflow_mode(django_settings) == "local"


def test_workflow_async_and_beat_default_to_production_mode() -> None:
    django_settings = SimpleNamespace(GENERAL_MANAGER={"WORKFLOW_MODE": "production"})

    assert workflow_async_enabled(django_settings) is True
    assert workflow_beat_enabled(django_settings) is True


def test_workflow_async_and_beat_explicit_values_use_bool_coercion() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_ASYNC": "",
            "WORKFLOW_BEAT_ENABLED": "false",
        }
    )

    assert workflow_async_enabled(django_settings) is False
    assert workflow_beat_enabled(django_settings) is True


def test_workflow_async_and_beat_treat_none_as_omitted() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_ASYNC": None,
            "WORKFLOW_BEAT_ENABLED": None,
        }
    )

    assert workflow_async_enabled(django_settings) is True
    assert workflow_beat_enabled(django_settings) is True


def test_workflow_integer_settings_prefer_nested_and_clamp() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={
            "WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS": 0,
            "WORKFLOW_BEAT_MAX_JITTER_SECONDS": -1,
            "WORKFLOW_OUTBOX_BATCH_SIZE": -10,
            "WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE": "2",
            "WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS": "0",
            "WORKFLOW_MAX_RETRIES": -4,
            "WORKFLOW_RETRY_BACKOFF_SECONDS": "8",
            "WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS": "0",
        },
        WORKFLOW_OUTBOX_BATCH_SIZE=999,
    )

    assert workflow_beat_outbox_interval_seconds(django_settings) == 1
    assert workflow_beat_max_jitter_seconds(django_settings) == 0
    assert workflow_outbox_batch_size(django_settings) == 1
    assert workflow_outbox_process_chunk_size(django_settings) == 2
    assert workflow_outbox_claim_ttl_seconds(django_settings) == 1
    assert workflow_max_retries(django_settings) == 0
    assert workflow_retry_backoff_seconds(django_settings) == 8
    assert workflow_delivery_running_timeout_seconds(django_settings) == 1


def test_workflow_integer_settings_fall_back_on_invalid_values() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={
            "WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS": object(),
            "WORKFLOW_BEAT_MAX_JITTER_SECONDS": object(),
            "WORKFLOW_OUTBOX_BATCH_SIZE": object(),
            "WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE": object(),
            "WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS": object(),
            "WORKFLOW_MAX_RETRIES": object(),
            "WORKFLOW_RETRY_BACKOFF_SECONDS": object(),
            "WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS": object(),
        }
    )

    assert workflow_beat_outbox_interval_seconds(django_settings) == 5
    assert workflow_beat_max_jitter_seconds(django_settings) == 2
    assert workflow_outbox_batch_size(django_settings) == 100
    assert workflow_outbox_process_chunk_size(django_settings) == 50
    assert workflow_outbox_claim_ttl_seconds(django_settings) == 300
    assert workflow_max_retries(django_settings) == 3
    assert workflow_retry_backoff_seconds(django_settings) == 5
    assert workflow_delivery_running_timeout_seconds(django_settings) == 300


def test_workflow_dead_letter_enabled_prefers_nested_bool_coercion() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={"WORKFLOW_DEAD_LETTER_ENABLED": 0},
        WORKFLOW_DEAD_LETTER_ENABLED=True,
    )

    assert workflow_dead_letter_enabled(django_settings) is False


def test_workflow_dead_letter_enabled_coerces_none_to_false() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={"WORKFLOW_DEAD_LETTER_ENABLED": None}
    )

    assert workflow_dead_letter_enabled(django_settings) is False


def test_workflow_config_ignores_non_mapping_general_manager() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER=["not", "a", "mapping"],
        WORKFLOW_OUTBOX_BATCH_SIZE=7,
    )

    assert workflow_mode(django_settings) == "local"
    assert workflow_outbox_batch_size(django_settings) == 7
