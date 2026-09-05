from datetime import datetime, time, tzinfo
from datetime import timedelta, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from general_manager.api import as_of
from general_manager.as_of import normalize_search_date
from general_manager.measurement import Measurement
from general_manager.utils._make_cache_key import CALL_KEY_NAMESPACE, make_cache_key
from general_manager.utils.json_encoder import CustomJSONEncoder


class TestMakeCacheKey(SimpleTestCase):
    @staticmethod
    def _manager_class():
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id: object, search_date: object = None) -> None:
                self.identification = {"id": manager_id}
                self._search_date = (
                    None if search_date is None else normalize_search_date(search_date)  # type: ignore[arg-type]
                )

            _as_of_behavior = "historical"

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface
        return CacheKeyManager

    def test_uses_versioned_call_namespace_and_normalized_arguments(self) -> None:
        def sample_function(first: int, second: int = 2) -> int:
            return first + second

        positional = make_cache_key(sample_function, (1, 2), {})
        keyword = make_cache_key(sample_function, (1,), {"second": 2})

        self.assertTrue(positional.startswith(f"{CALL_KEY_NAMESPACE}:"))
        self.assertEqual(positional, keyword)

    def test_namespaces_ambient_historical_snapshots(self) -> None:
        def sample_function(value: int) -> int:
            return value

        current_key = make_cache_key(sample_function, (1,), {})
        with as_of("2022-01-01"):
            first_historical_key = make_cache_key(sample_function, (1,), {})
        with as_of(datetime(2022, 1, 1)):
            equivalent_historical_key = make_cache_key(sample_function, (1,), {})
        with as_of("2022-01-02"):
            second_historical_key = make_cache_key(sample_function, (1,), {})

        self.assertNotEqual(current_key, first_historical_key)
        self.assertEqual(first_historical_key, equivalent_historical_key)
        self.assertNotEqual(first_historical_key, second_historical_key)

    def test_equivalent_offset_instants_share_ambient_snapshot_identity(self) -> None:
        def sample_function(value: int) -> int:
            return value

        with as_of("2022-01-01T01:00:00+01:00"):
            offset_key = make_cache_key(sample_function, (1,), {})
        with as_of("2022-01-01T00:00:00+00:00"):
            utc_key = make_cache_key(sample_function, (1,), {})

        self.assertEqual(offset_key, utc_key)

    def test_manager_identity_includes_qualified_class_and_snapshot(self) -> None:
        manager_class = self._manager_class()

        def sample_function(manager: object) -> object:
            return manager

        current_key = make_cache_key(sample_function, (manager_class(1),), {})
        historical_key = make_cache_key(
            sample_function,
            (manager_class(1, search_date="2022-01-01"),),
            {},
        )
        original_module = manager_class.__module__
        try:
            manager_class.__module__ = "another_manager_module"
            different_manager_class_key = make_cache_key(
                sample_function, (manager_class(1),), {}
            )
        finally:
            manager_class.__module__ = original_module

        self.assertNotEqual(current_key, historical_key)
        self.assertNotEqual(current_key, different_manager_class_key)

    def test_equivalent_offset_manager_snapshots_share_identity(self) -> None:
        manager_class = self._manager_class()

        def sample_function(manager: object) -> object:
            return manager

        self.assertEqual(
            make_cache_key(
                sample_function,
                (manager_class(1, search_date="2022-01-01T01:00:00+01:00"),),
                {},
            ),
            make_cache_key(
                sample_function,
                (manager_class(1, search_date="2022-01-01T00:00:00+00:00"),),
                {},
            ),
        )

    def test_manager_identity_preserves_unicode_identification(self) -> None:
        manager_class = self._manager_class()

        def sample_function(manager: object) -> object:
            return manager

        self.assertNotEqual(
            make_cache_key(sample_function, (manager_class("Müller"),), {}),
            make_cache_key(sample_function, (manager_class("Mueller"),), {}),
        )

    def test_single_manager_fast_path_matches_generic_canonical_identity(self) -> None:
        manager_class = self._manager_class()

        def sample_function(manager: object) -> object:
            return manager

        manager = manager_class(1, search_date="2022-01-01")

        fast_key = make_cache_key(sample_function, (manager,), {})
        self.assertEqual(
            fast_key,
            make_cache_key(sample_function, (), {"manager": manager}),
        )
        manager.identification["id"] = 2
        self.assertNotEqual(
            fast_key,
            make_cache_key(sample_function, (manager,), {}),
        )

    def test_distinguishes_typed_lookalikes(self) -> None:
        def sample_function(value: object) -> object:
            return value

        values: tuple[object, ...] = (
            Decimal("1.25"),
            "1.25",
            1,
            True,
            ["value"],
            ("value",),
            {"value"},
            frozenset({"value"}),
        )

        keys = {make_cache_key(sample_function, (value,), {}) for value in values}

        self.assertEqual(len(keys), len(values))

    def test_preserves_decimal_scale_and_measurement_unit(self) -> None:
        def sample_function(value: object) -> object:
            return value

        self.assertNotEqual(
            make_cache_key(sample_function, (Decimal("1.0"),), {}),
            make_cache_key(sample_function, (Decimal("1.00"),), {}),
        )
        self.assertNotEqual(
            make_cache_key(sample_function, (Measurement(Decimal("1"), "meter"),), {}),
            make_cache_key(
                sample_function,
                (Measurement(Decimal("100"), "centimeter"),),
                {},
            ),
        )

    def test_is_deterministic_for_mapping_order(self) -> None:
        def sample_function(value: object) -> object:
            return value

        self.assertEqual(
            make_cache_key(sample_function, ({"first": 1, "second": 2},), {}),
            make_cache_key(sample_function, ({"second": 2, "first": 1},), {}),
        )

    def test_rejects_cycles_and_opaque_values(self) -> None:
        def sample_function(value: object) -> object:
            return value

        cyclic: list[object] = []
        cyclic.append(cyclic)

        with self.assertRaisesRegex(TypeError, "cycle"):
            make_cache_key(sample_function, (cyclic,), {})
        with self.assertRaisesRegex(TypeError, "Unsupported cache-key value"):
            make_cache_key(sample_function, (object(),), {})

    def test_preserves_public_json_fallback_independently(self) -> None:
        class DisplayOnly:
            def __str__(self) -> str:
                return "display-value"

        self.assertEqual(
            json.dumps(DisplayOnly(), cls=CustomJSONEncoder), '"display-value"'
        )

    def test_rejects_invalid_argument_binding(self) -> None:
        def sample_function(first: int, second: int) -> int:
            return first + second

        with self.assertRaises(TypeError):
            make_cache_key(sample_function, (1,), {"first": 2})

    def test_supports_unhashable_callable_targets(self) -> None:
        class CallableWithoutHash:
            __hash__ = None  # type: ignore[assignment]

            def __init__(self) -> None:
                self.__module__ = __name__
                self.__qualname__ = "CallableWithoutHash"

            def __call__(self, value: object) -> object:
                return value

        callable_target = CallableWithoutHash()

        self.assertEqual(
            make_cache_key(callable_target, ("value",), {}),
            make_cache_key(callable_target, ("value",), {}),
        )

    def test_function_module_is_part_of_identity(self) -> None:
        def sample_function(value: int) -> int:
            return value

        original_module = sample_function.__module__
        first_key = make_cache_key(sample_function, (1,), {})
        try:
            sample_function.__module__ = "another_module"
            second_key = make_cache_key(sample_function, (1,), {})
        finally:
            sample_function.__module__ = original_module

        self.assertNotEqual(first_key, second_key)

    def test_falsey_kwargs_mapping_is_normalized(self) -> None:
        class FalseyMapping(dict[str, object]):
            def __bool__(self) -> bool:
                return False

        def sample_function(value: int = 1) -> int:
            return value

        self.assertEqual(
            make_cache_key(sample_function, (), FalseyMapping()),
            make_cache_key(sample_function, (), {}),
        )

    def test_ordinary_datetime_arguments_preserve_walltime_and_offset(self) -> None:
        """Equal instants in different caller timezones remain distinct arguments."""
        from general_manager.cache.cache_decorator import cached
        from general_manager.cache.run_context import CalculationRunContext

        calls = 0

        @cached
        def sample_function(value: datetime) -> int:
            nonlocal calls
            calls += 1
            return calls

        with CalculationRunContext():
            self.assertEqual(
                sample_function(datetime(2022, 1, 1, tzinfo=timezone.utc)),
                1,
            )
            self.assertEqual(
                sample_function(
                    datetime(2022, 1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
                ),
                2,
            )

    def test_ordinary_temporal_arguments_preserve_fold_and_reject_unknown_types(
        self,
    ) -> None:
        def sample_function(value: object) -> object:
            return value

        self.assertNotEqual(
            make_cache_key(sample_function, (datetime(2022, 11, 6, 1, fold=0),), {}),
            make_cache_key(sample_function, (datetime(2022, 11, 6, 1, fold=1),), {}),
        )

        class DatetimeSubclass(datetime):
            pass

        class UnknownTimezone(tzinfo):
            def utcoffset(self, value: datetime | None) -> None:
                return None

            def dst(self, value: datetime | None) -> None:
                return None

            def tzname(self, value: datetime | None) -> str:
                return "unknown"

        with self.assertRaisesRegex(TypeError, "Unsupported cache-key value"):
            make_cache_key(sample_function, (DatetimeSubclass(2022, 1, 1),), {})
        with self.assertRaisesRegex(TypeError, "Unsupported cache-key value"):
            make_cache_key(
                sample_function, (time(1, 30, tzinfo=UnknownTimezone()),), {}
            )

    def test_zoneinfo_time_does_not_share_a_naive_cache_entry(self) -> None:
        """A time without a date still carries its ZoneInfo identity."""
        from general_manager.cache.cache_decorator import cached
        from general_manager.cache.run_context import CalculationRunContext

        calls = 0

        @cached
        def sample_function(value: time) -> int:
            nonlocal calls
            calls += 1
            return calls

        with CalculationRunContext():
            self.assertEqual(sample_function(time(1, 30)), 1)
            self.assertEqual(
                sample_function(time(1, 30, tzinfo=ZoneInfo("America/New_York"))),
                2,
            )

        def direct_key(value: time) -> time:
            return value

        naive = make_cache_key(direct_key, (time(1, 30),), {})
        new_york = make_cache_key(
            direct_key,
            (time(1, 30, tzinfo=ZoneInfo("America/New_York")),),
            {},
        )
        new_york_fold = make_cache_key(
            direct_key,
            (time(1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1),),
            {},
        )
        utc = make_cache_key(
            direct_key,
            (time(1, 30, tzinfo=ZoneInfo("UTC")),),
            {},
        )

        self.assertEqual(len({naive, new_york, new_york_fold, utc}), 4)

    def test_manager_legacy_snapshot_state_is_used_only_when_effective_key_missing(
        self,
    ) -> None:
        manager_class = self._manager_class()

        def sample_function(manager: object) -> object:
            return manager

        live_manager = manager_class(1)
        legacy_manager = manager_class(1, search_date="2022-01-01")
        del legacy_manager.__dict__["_effective_search_date"]

        self.assertNotEqual(
            make_cache_key(sample_function, (live_manager,), {}),
            make_cache_key(sample_function, (legacy_manager,), {}),
        )
