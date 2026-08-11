from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
import pytest
from types import ModuleType

from general_manager.cache.run_context_lru import (
    MIN_TRACKED_ENTRY_BYTES,
    estimate_cache_entry_size,
    resolve_run_context_cache_max_bytes,
)


@pytest.mark.parametrize("configured", [None, 0, 1, 1024])
def test_resolve_run_context_cache_max_bytes_accepts_supported_values(
    configured: int | None,
) -> None:
    with override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": configured}):
        assert resolve_run_context_cache_max_bytes() == configured


@override_settings(GENERAL_MANAGER={})
def test_resolve_run_context_cache_max_bytes_defaults_to_unlimited() -> None:
    assert resolve_run_context_cache_max_bytes() is None


@pytest.mark.parametrize("configured", [-1, True, False, 1.5, "1024"])
def test_resolve_run_context_cache_max_bytes_rejects_invalid_values(
    configured: object,
) -> None:
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": configured}),
        pytest.raises(ImproperlyConfigured, match="RUN_CONTEXT_CACHE_MAX_BYTES"),
    ):
        resolve_run_context_cache_max_bytes()


def test_estimate_cache_entry_size_handles_cycles() -> None:
    value: list[object] = []
    value.append(value)

    size = estimate_cache_entry_size("cycle", value, stop_after=None)

    assert size >= MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_counts_shared_object_once_per_entry() -> None:
    shared = [bytearray(1024)]

    shared_size = estimate_cache_entry_size("key", [shared, shared], stop_after=None)
    copied_size = estimate_cache_entry_size(
        "key", [[bytearray(1024)], [bytearray(1024)]], stop_after=None
    )

    assert shared_size < copied_size


def test_estimate_cache_entry_size_stops_after_budget() -> None:
    size = estimate_cache_entry_size(
        "key",
        [b"x" * 1024 for _ in range(100)],
        stop_after=512,
    )

    assert size == 513


def test_estimate_cache_entry_size_traverses_list_declared_slots() -> None:
    class SlotValue:
        __slots__ = ["payload"]

        def __init__(self, payload: object) -> None:
            self.payload = payload

    empty_size = estimate_cache_entry_size("key", SlotValue(None), stop_after=None)
    payload_size = estimate_cache_entry_size(
        "key", SlotValue(bytearray(1024)), stop_after=None
    )

    assert empty_size < payload_size


def test_estimate_cache_entry_size_traverses_private_slots() -> None:
    class PrivateSlotValue:
        __slots__ = ("__payload",)

        def __init__(self, payload: object) -> None:
            self.__payload = payload

    empty_size = estimate_cache_entry_size(
        "key", PrivateSlotValue(None), stop_after=None
    )
    payload_size = estimate_cache_entry_size(
        "key", PrivateSlotValue(bytearray(1024)), stop_after=None
    )

    assert empty_size < payload_size


def test_estimate_cache_entry_size_treats_module_subclasses_as_shallow_leaves() -> None:
    class CustomModule(ModuleType):
        pass

    module = CustomModule("custom")
    module.payload = bytearray(1024)

    size = estimate_cache_entry_size("key", module, stop_after=None)

    assert size == MIN_TRACKED_ENTRY_BYTES
