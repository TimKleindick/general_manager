import gc
import inspect
import threading
from types import FunctionType
from typing import ClassVar
from unittest.mock import patch
from weakref import ref

from django.test import TestCase, override_settings
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.interface import CalculationInterface
from general_manager.interface.capabilities.calculation import (
    CalculationLifecycleCapability,
    CalculationQueryCapability,
    lifecycle as calculation_lifecycle_module,
)
from general_manager.interface.capabilities.calculation.lifecycle import (
    CalculationReadCapability,
)
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface import base_interface as base_interface_module
from general_manager.interface.capabilities.calculation.lifecycle import (
    _is_canonical_calculation_input_accessor,
)
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.manager.input import Input
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import AttributeEvaluationError, GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from tests.perf.support import count_profiled_calls


class DummyCalculationInterface(CalculationInterface):
    input_fields: ClassVar[dict[str, Input]] = {
        "field1": Input(type=str),
        "field2": Input(type=int, required=False),
    }


class DummyGeneralManager:
    Interface = DummyCalculationInterface


DummyCalculationInterface._parent_class = DummyGeneralManager


class CustomQueryCapability(CalculationQueryCapability):
    def __init__(self, label: str):
        """
        Initialize the capability and store a human-readable label on the instance.

        Parameters:
            label (str): Human-readable label assigned to the capability.
        """
        super().__init__()
        self.label = label


class CustomCalculationInterface(DummyCalculationInterface):
    configured_capabilities: ClassVar[tuple] = (
        *DummyCalculationInterface.configured_capabilities,
        InterfaceCapabilityConfig(CustomQueryCapability, {"label": "custom"}),
    )


CustomCalculationInterface._parent_class = DummyGeneralManager


class TestCalculationInterface(TestCase):
    def setUp(self):
        """
        Initializes a DummyCalculationInterface instance for use in test methods.
        """
        self.interface = DummyCalculationInterface("test", 1)

    def test_get_data(self):
        """
        Tests that get_data() raises a NotImplementedError when called on the interface instance.
        """
        with self.assertRaises(NotImplementedError):
            self.interface.get_data()

    def test_get_attribute_types(self):
        """
        Tests that get_attribute_types() returns a dictionary with expected attribute metadata keys.
        """
        attribute_types = DummyCalculationInterface.get_attribute_types()
        self.assertIsInstance(attribute_types, dict)
        for _name, attr in attribute_types.items():
            self.assertIn("type", attr)
            self.assertIn("default", attr)
            self.assertIn("is_editable", attr)
            self.assertIn("is_required", attr)
        self.assertTrue(attribute_types["field1"]["is_required"])
        self.assertFalse(attribute_types["field2"]["is_required"])

    def test_get_attributes(self):
        """
        Tests that get_attributes() returns a dictionary mapping attribute names to callables that produce the correct values for the interface instance.
        """
        attributes = DummyCalculationInterface.get_attributes()
        self.assertIsInstance(attributes, dict)
        for _name, attr in attributes.items():
            self.assertTrue(callable(attr))
            self.assertIn(attr(self.interface), ("test", 1))

    def test_calculation_input_accessors_carry_exact_private_provenance(self):
        attributes = DummyCalculationInterface.get_attributes()

        self.assertTrue(
            _is_canonical_calculation_input_accessor(
                attributes["field1"], DummyCalculationInterface, "field1"
            )
        )
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                attributes["field1"], DummyCalculationInterface, "field2"
            )
        )

    def test_calculation_input_accessor_provenance_rejects_substitution(self):
        class DerivedCalculationInterface(DummyCalculationInterface):
            pass

        accessor = DummyCalculationInterface.get_attributes()["field1"]

        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                lambda interface: interface, DummyCalculationInterface, "field1"
            )
        )
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DerivedCalculationInterface, "field1"
            )
        )
        accessor.__dict__["substituted"] = True
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_copied_or_partial_provenance(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]

        def copied(interface):
            return interface

        copied.__dict__.update(accessor.__dict__)
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                copied, DummyCalculationInterface, "field1"
            )
        )

        def partial(interface):
            return interface

        partial.__dict__.update(accessor.__dict__)
        partial.__dict__.pop("_gm_calculation_field_name")
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                partial, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_self_rebound_copied_provenance(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]

        def copied(interface):
            return interface

        copied.__dict__.update(accessor.__dict__)
        copied.__dict__["_gm_calculation_input_accessor_self"] = copied

        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                copied, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_unregistered_same_implementation(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]
        copied = FunctionType(
            accessor.__code__,
            accessor.__globals__,
            accessor.__name__,
            accessor.__defaults__,
            accessor.__closure__,
        )
        copied.__dict__.update(accessor.__dict__)
        copied.__dict__["_gm_calculation_input_accessor_self"] = copied

        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                copied, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_code_or_closure_mutation(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]

        def make_replacement():
            first = None
            second = None

            def replacement(interface):
                if first is second:
                    return interface
                return interface

            return replacement

        accessor.__code__ = make_replacement().__code__
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

        accessor = DummyCalculationInterface.get_attributes()["field1"]
        stored_field_name = accessor.__dict__["_gm_calculation_field_name"]
        field_cell = next(
            cell
            for cell in accessor.__closure__
            if cell.cell_contents is stored_field_name
        )
        field_cell.cell_contents = "field2"
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_resolver_code_mutation(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]
        resolver = next(
            cell.cell_contents
            for cell in accessor.__closure__
            if type(cell.cell_contents) is FunctionType
        )
        resolver = next(
            cell.cell_contents
            for cell in resolver.__closure__
            if type(cell.cell_contents) is FunctionType
        )

        def make_replacement():
            first = None
            second = None
            third = None
            fourth = None
            fifth = None

            def replacement(interface, field_name):
                if first is second is third is fourth is fifth:
                    return interface
                return field_name

            return replacement

        resolver.__code__ = make_replacement().__code__

        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_resolver_interface_mutation(self):
        class SubstitutedInterface(DummyCalculationInterface):
            pass

        accessor = DummyCalculationInterface.get_attributes()["field1"]
        resolver = next(
            cell.cell_contents
            for cell in accessor.__closure__
            if type(cell.cell_contents) is FunctionType
        )
        resolver = next(
            cell.cell_contents
            for cell in resolver.__closure__
            if type(cell.cell_contents) is FunctionType
        )
        interface_cell = next(
            cell
            for cell in resolver.__closure__
            if cell.cell_contents is DummyCalculationInterface
        )
        interface_cell.cell_contents = SubstitutedInterface

        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

    def test_calculation_input_accessor_rejects_resolver_dependency_mutation(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]
        resolver = next(
            cell.cell_contents
            for cell in accessor.__closure__
            if type(cell.cell_contents) is FunctionType
        )
        core_resolver = next(
            cell.cell_contents
            for cell in resolver.__closure__
            if type(cell.cell_contents) is FunctionType
        )
        recursive_cell = next(
            cell for cell in core_resolver.__closure__ if cell.cell_contents is resolver
        )
        recursive_cell.cell_contents = lambda interface, field_name: (
            interface,
            field_name,
        )[0]

        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

    def test_shared_resolver_remains_canonical_for_unchanged_accessors(self):
        attributes = DummyCalculationInterface.get_attributes()
        first = attributes["field1"]
        second = attributes["field2"]
        first_resolver = next(
            cell.cell_contents
            for cell in first.__closure__
            if type(cell.cell_contents) is FunctionType
        )
        second_resolver = next(
            cell.cell_contents
            for cell in second.__closure__
            if type(cell.cell_contents) is FunctionType
        )

        self.assertIs(first_resolver, second_resolver)
        self.assertTrue(
            _is_canonical_calculation_input_accessor(
                first, DummyCalculationInterface, "field1"
            )
        )
        self.assertTrue(
            _is_canonical_calculation_input_accessor(
                second, DummyCalculationInterface, "field2"
            )
        )

    def test_calculation_input_accessor_rejects_function_state_mutation(self):
        mutations = (
            lambda accessor: setattr(accessor, "__defaults__", (None,)),
            lambda accessor: setattr(accessor, "__kwdefaults__", {"value": None}),
            lambda accessor: accessor.__annotations__.__setitem__("extra", object),
        )

        for mutation in mutations:
            accessor = DummyCalculationInterface.get_attributes()["field1"]
            mutation(accessor)
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    _is_canonical_calculation_input_accessor(
                        accessor, DummyCalculationInterface, "field1"
                    )
                )

    def test_calculation_input_accessor_registry_does_not_retain_accessor(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]
        accessor_ref = ref(accessor)
        self.assertTrue(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

        del accessor
        gc.collect()

        self.assertIsNone(accessor_ref())

    def test_calculation_input_accessor_rejects_mutated_owner_and_field(self):
        accessor = DummyCalculationInterface.get_attributes()["field1"]
        accessor.__dict__["_gm_calculation_interface_cls"] = object
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

        accessor = DummyCalculationInterface.get_attributes()["field1"]
        accessor.__dict__["_gm_calculation_field_name"] = "field2"
        self.assertFalse(
            _is_canonical_calculation_input_accessor(
                accessor, DummyCalculationInterface, "field1"
            )
        )

    def test_get_attributes_passes_identification_to_dependent_inputs(self):
        class DependentCalculationInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "field1": Input(type=str),
                "field2": Input(
                    type=str,
                    possible_values=lambda field1: [field1.upper()],
                    depends_on=["field1"],
                    normalizer=lambda value: value.upper(),
                ),
            }

        class DependentManager:
            Interface = DependentCalculationInterface

        DependentCalculationInterface._parent_class = DependentManager
        interface = DependentCalculationInterface("alpha", "alpha")

        attributes = DependentCalculationInterface.get_attributes()

        self.assertEqual(attributes["field1"](interface), "alpha")
        self.assertEqual(attributes["field2"](interface), "ALPHA")
        self.assertEqual(attributes["field2"](interface), "ALPHA")

    def test_cached_manager_input_uses_manager_dependency_fast_path(self):
        class RelatedInterface:
            def __init__(self, manager_id=None, *, id=None):
                if id is not None:
                    manager_id = id
                self.identification = {"id": manager_id}

        with override_settings(AUTOCREATE_GRAPHQL=False):

            class RelatedManager(GeneralManager):
                pass

        RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]
        RelatedManager.Permission = ManagerBasedPermission  # type: ignore[assignment]
        RelatedManager._attributes = {}

        class ManagerInputCalculationInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "manager": Input(type=RelatedManager),
            }

        class ManagerInputCalculation:
            Interface = ManagerInputCalculationInterface

        ManagerInputCalculationInterface._parent_class = ManagerInputCalculation
        interface = ManagerInputCalculationInterface("related-id")
        attributes = ManagerInputCalculationInterface.get_attributes()
        manager = attributes["manager"](interface)
        self.assertIsInstance(manager, RelatedManager)

        track_calls = []
        original_track = RelatedManager._track_identification_dependency

        def track_identification(identification):
            track_calls.append(identification)
            return original_track(identification)

        with (
            DependencyTracker() as dependencies,
            patch.object(
                RelatedManager,
                "_track_identification_dependency",
                side_effect=track_identification,
            ),
        ):
            self.assertIs(attributes["manager"](interface), manager)

        self.assertEqual(track_calls, [manager.identification])
        self.assertIn(
            (RelatedManager.__name__, "identification", '{"id": "related-id"}'),
            dependencies,
        )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_first_hydrated_public_access_tracks_nested_dependency_once(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        track_calls = []
        original_track = calculation_lifecycle_module._track_cached_manager

        def track_cached_manager(value):
            track_calls.append(value)
            original_track(value)

        with (
            patch.object(
                calculation_lifecycle_module,
                "_track_cached_manager",
                side_effect=track_cached_manager,
            ),
            DependencyTracker() as dependencies,
        ):
            resolved = manager.related

        expected_dependency = (
            RelatedManager.__name__,
            "identification",
            '{"id": "related-id"}',
        )
        self.assertIs(resolved, original)
        self.assertEqual(track_calls, [original])
        self.assertEqual(dependencies, {expected_dependency})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hydrated_descriptor_cache_replays_dependency_per_access(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)

        self.assertIs(manager.related, original)
        self.assertEqual(manager._attribute_value_cache, {"related": original})

        replay_calls = []
        original_replay = RelatedManager._track_own_identification_dependency_active

        def replay_dependency(current_manager):
            replay_calls.append(current_manager)
            original_replay(current_manager)

        expected_dependency = (
            RelatedManager.__name__,
            "identification",
            '{"id": "related-id"}',
        )
        with (
            patch.object(
                calculation_lifecycle_module,
                "_track_cached_manager",
                side_effect=AssertionError(
                    "descriptor cache must bypass the lifecycle accessor"
                ),
            ),
            patch.object(
                RelatedManager,
                "_track_own_identification_dependency_active",
                replay_dependency,
            ),
        ):
            with DependencyTracker() as separate_dependencies:
                self.assertIs(manager.related, original)
            with DependencyTracker() as outer_dependencies:
                with DependencyTracker() as nested_dependencies:
                    self.assertIs(manager.related, original)

        self.assertEqual(replay_calls, [original, original])
        self.assertEqual(separate_dependencies, {expected_dependency})
        self.assertEqual(outer_dependencies, {expected_dependency})
        self.assertEqual(nested_dependencies, {expected_dependency})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hydrated_dependency_uses_only_one_first_access_cache_path(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        with DependencyTracker() as construction_dependencies:
            manager = HydratedCalculation(original)

        outer_dependency = (
            HydratedCalculation.__name__,
            "identification",
            serialize_dependency_identifier(manager.identification),
        )
        nested_dependency = (
            RelatedManager.__name__,
            "identification",
            '{"id": "related-id"}',
        )
        self.assertEqual(construction_dependencies, {outer_dependency})
        self.assertEqual(manager._attribute_value_cache, {})

        lifecycle_calls = []
        original_track = calculation_lifecycle_module._track_cached_manager

        def track_cached_manager(value):
            lifecycle_calls.append(value)
            original_track(value)

        with (
            patch.object(
                calculation_lifecycle_module,
                "_track_cached_manager",
                side_effect=track_cached_manager,
            ),
            patch(
                "general_manager.manager.meta._manager_dependency_tracking_class",
                side_effect=AssertionError(
                    "empty descriptor cache must not replay manager tracking"
                ),
            ),
            DependencyTracker() as access_dependencies,
        ):
            self.assertIs(manager.related, original)

        self.assertEqual(lifecycle_calls, [original])
        self.assertEqual(access_dependencies, {nested_dependency})
        self.assertEqual(manager._attribute_value_cache, {"related": original})

    def test_nonseeded_custom_interface_keeps_virtual_lazy_cache_dispatch(self):
        dispatch_calls = []

        class RelatedInterface:
            def __init__(self, manager_id=None, *, id=None):
                self.identification = {
                    "id": manager_id if id is None else id,
                }

        with override_settings(AUTOCREATE_GRAPHQL=False):

            class RelatedManager(GeneralManager):
                pass

        RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]
        RelatedManager.Permission = ManagerBasedPermission  # type: ignore[assignment]
        RelatedManager._attributes = {}

        class CustomCalculationInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "manager": Input(RelatedManager),
            }

            def __getattribute__(self, name):
                if name in {"_resolved_input_values", "identification"}:
                    dispatch_calls.append(("get", name))
                return super().__getattribute__(name)

            def __setattr__(self, name, value):
                if name == "_resolved_input_values":
                    dispatch_calls.append(("set", name))
                super().__setattr__(name, value)

        class CustomCalculation:
            Interface = CustomCalculationInterface

        CustomCalculationInterface._parent_class = CustomCalculation
        interface = CustomCalculationInterface("related-id")
        attributes = CustomCalculationInterface.get_attributes()
        dispatch_calls.clear()

        first = attributes["manager"](interface)
        first_calls = list(dispatch_calls)
        dispatch_calls.clear()
        second = attributes["manager"](interface)

        self.assertIs(second, first)
        self.assertEqual(
            first_calls,
            [
                ("get", "_resolved_input_values"),
                ("set", "_resolved_input_values"),
                ("get", "identification"),
            ],
        )
        self.assertEqual(dispatch_calls, [("get", "_resolved_input_values")])

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_initialized_manager_input_is_seeded_from_parsed_wrapper(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                label = Input(str)

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        related = RelatedManager("related-id")

        manager = HydratedCalculation(related, "label")

        self.assertEqual(
            manager.identification,
            {"related": {"id": "related-id"}, "label": "label"},
        )
        self.assertEqual(
            manager._interface._resolved_input_values,
            {"related": related},
        )
        self.assertIs(manager._interface._resolved_input_values["related"], related)
        self.assertIs(manager.related, related)
        self.assertNotIn("label", manager._interface._resolved_input_values)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_invalid_seeded_manager_is_evicted_before_first_outer_access(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")

        resolved = manager.related

        self.assertIsInstance(resolved, RelatedManager)
        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_marked_invalid_seed_with_unexpected_outer_state_still_evicts(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")
        object.__setattr__(manager._interface, "unexpected_state", object())

        resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seeded_manager_with_unexpected_nested_state_is_evicted(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        object.__setattr__(original._interface, "unexpected_state", object())

        resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_substituted_marked_cache_value_is_evicted_and_recast(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("good-id")
        substitute = RelatedManager("evil-id")
        manager = HydratedCalculation(original)
        manager._interface._resolved_input_values["related"] = substitute

        resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertIsNot(resolved, substitute)
        self.assertEqual(resolved.identification, {"id": "good-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_missing_seeded_cache_entry_releases_marker_and_recaches_lazily(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        original_ref = ref(original)
        manager = HydratedCalculation(original)
        manager._interface._resolved_input_values.pop("related")
        del original
        gc.collect()
        self.assertIsNotNone(original_ref())

        resolved = manager.related
        gc.collect()

        self.assertIsNone(original_ref())
        self.assertEqual(resolved.identification, {"id": "related-id"})
        self.assertNotIn(
            "_gm_seeded_input_values_cache",
            vars(manager._interface),
        )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_mutated_seed_marker_state_never_demotes_stale_value_to_lazy(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)

        def clear_marker(interface):
            vars(interface)["_gm_seeded_input_values_cache"].clear()

        def replace_marker(interface):
            vars(interface)["_gm_seeded_input_values_cache"] = {}

        def delete_field_marker(interface):
            vars(interface)["_gm_seeded_input_values_cache"].pop("related")

        for mutation in (clear_marker, replace_marker, delete_field_marker):
            original = RelatedManager("related-id")
            manager = HydratedCalculation(original)
            original._invalidate_manager_state("stale")
            mutation(manager._interface)

            with self.subTest(mutation=mutation):
                resolved = manager.related
                self.assertIsNot(resolved, original)
                self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_deleting_all_instance_markers_cannot_demote_invalid_seed(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")
        interface_state = vars(manager._interface)
        interface_state.pop("_gm_seeded_input_values_cache")
        interface_state.pop("_gm_lazy_input_values_cache")

        resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_post_seed_resolved_descriptor_mutation_invokes_zero_hooks(self):
        hook_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")
        interface_class = HydratedCalculation.Interface
        hostile_descriptor = property(
            lambda _self: hook_calls.append("get") or original,
            lambda _self, _value: hook_calls.append("set"),
        )
        type.__setattr__(
            interface_class,
            "_resolved_input_values",
            hostile_descriptor,
        )
        try:
            resolved = manager.related
        finally:
            type.__delattr__(interface_class, "_resolved_input_values")

        self.assertEqual(hook_calls, [])
        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hostile_seed_container_keys_invoke_zero_hooks(self):
        hook_calls = []

        class HostileFieldName(str):
            def __hash__(self):
                hook_calls.append("hash")
                return str.__hash__(self)

            def __eq__(self, other):
                hook_calls.append("eq")
                return str.__eq__(self, other)

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)

        def mutate_seed_map(interface):
            seed_map = vars(interface)["_gm_seeded_input_values_cache"]
            original_value = seed_map.pop("related")
            seed_map[HostileFieldName("related")] = original_value

        def mutate_lazy_set(interface):
            lazy_fields = vars(interface)["_gm_lazy_input_values_cache"]
            lazy_fields.add(HostileFieldName("related"))

        def mutate_resolved_map(interface):
            resolved_values = vars(interface)["_resolved_input_values"]
            original_value = resolved_values.pop("related")
            resolved_values[HostileFieldName("related")] = original_value

        for mutation in (mutate_seed_map, mutate_lazy_set, mutate_resolved_map):
            original = RelatedManager("related-id")
            manager = HydratedCalculation(original)
            mutation(manager._interface)
            hook_calls.clear()

            with self.subTest(mutation=mutation):
                resolved = manager.related
                self.assertEqual(hook_calls, [])
                self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_external_seed_origin_registry_does_not_retain_interface(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        registry_size_before = base_interface_module._seeded_interface_registry_size()
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        interface = manager._interface
        interface_id = id(interface)
        interface_ref = ref(interface)
        self.assertEqual(
            base_interface_module._seeded_interface_registry_size(),
            registry_size_before + 1,
        )

        del interface
        del manager
        del original
        gc.collect()

        self.assertIsNone(interface_ref())
        self.assertIsNone(
            base_interface_module._seeded_interface_origin_by_id(interface_id)
        )
        self.assertLessEqual(
            base_interface_module._seeded_interface_registry_size(),
            registry_size_before,
        )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_replaced_seeded_manager_identification_is_evicted(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        object.__setattr__(
            original,
            "_GeneralManager__id",
            {"id": "replacement-id"},
        )

        resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_replaced_live_outer_identification_recasts_current_value(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("old-id")
        manager = HydratedCalculation(original)
        manager.identification["related"] = {"id": "new-id"}

        resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "new-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_live_interface_dispatch_change_uses_virtual_fallback(self):
        dispatch_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("old-id")
        manager = HydratedCalculation(original)
        interface_class = type(manager._interface)

        def custom_getattribute(interface, name):
            if name in {"_resolved_input_values", "identification"}:
                dispatch_calls.append(name)
            if name == "identification":
                return {"related": {"id": "alternate-id"}}
            return object.__getattribute__(interface, name)

        type.__setattr__(interface_class, "__getattribute__", custom_getattribute)
        try:
            resolved = manager.related
        finally:
            type.__delattr__(interface_class, "__getattribute__")

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "alternate-id"})
        self.assertEqual(
            dispatch_calls,
            ["_resolved_input_values", "identification"],
        )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_dispatch_fallback_releases_seed_mirror_manager_references(self):
        mirror_dispatch_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        original_ref = ref(original)
        manager = HydratedCalculation(original)
        interface_class = type(manager._interface)

        def custom_getattribute(interface, name):
            if name in {
                "_gm_seeded_input_values_cache",
                "_gm_lazy_input_values_cache",
            }:
                mirror_dispatch_calls.append(name)
            return object.__getattribute__(interface, name)

        type.__setattr__(interface_class, "__getattribute__", custom_getattribute)
        try:
            resolved = manager.related
        finally:
            type.__delattr__(interface_class, "__getattribute__")

        interface_state = vars(manager._interface)
        self.assertFalse(interface_state.get("_gm_seeded_input_values_cache"))
        self.assertFalse(interface_state.get("_gm_lazy_input_values_cache"))
        self.assertEqual(mirror_dispatch_calls, [])
        self.assertIsNot(resolved, original)
        del original
        gc.collect()
        self.assertIsNone(original_ref())

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_dispatch_fallback_clears_mirrors_with_hostile_state_key(self):
        key_hook_calls = []
        mirror_dispatch_calls = []

        class HostileStateKey(str):
            def __hash__(self):
                return str.__hash__(self)

            def __eq__(self, other):
                key_hook_calls.append(other)
                raise AssertionError

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        original_ref = ref(original)
        manager = HydratedCalculation(original)
        interface = manager._interface
        interface_state = vars(interface)
        interface_state[HostileStateKey("unrelated-hostile-key")] = object()
        for key in tuple(interface_state):
            if type(key) is not str:
                continue
            if key == "_gm_seeded_input_values_cache":
                interface_state[key] = [original]
            elif key == "_gm_lazy_input_values_cache":
                interface_state[key] = {"related": original}
        interface_class = type(interface)

        def custom_getattribute(current_interface, name):
            if name in {
                "_gm_seeded_input_values_cache",
                "_gm_lazy_input_values_cache",
            }:
                mirror_dispatch_calls.append(name)
            return object.__getattribute__(current_interface, name)

        type.__setattr__(interface_class, "__getattribute__", custom_getattribute)
        try:
            resolved = manager.related
        finally:
            type.__delattr__(interface_class, "__getattribute__")

        marker_names = {
            key
            for key in vars(interface)
            if type(key) is str
            and key
            in {
                "_gm_seeded_input_values_cache",
                "_gm_lazy_input_values_cache",
            }
        }
        self.assertEqual(marker_names, set())
        self.assertEqual(key_hook_calls, [])
        self.assertEqual(mirror_dispatch_calls, [])
        self.assertIsNot(resolved, original)
        del original
        gc.collect()
        self.assertIsNone(original_ref())

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_dispatch_fallback_avoids_colliding_state_key_hooks(self):
        equality_calls = []

        class CollidingStateKey(str):
            armed = False

            def __new__(cls, value, target):
                instance = super().__new__(cls, value)
                instance.target = target
                return instance

            def __hash__(self):
                return hash(self.target)

            def __eq__(self, other):
                if type(self).armed:
                    equality_calls.append(other)
                    raise AssertionError
                return False

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        original_ref = ref(original)
        manager = HydratedCalculation(original)
        interface = manager._interface
        state = vars(interface)
        resolved_name = "_resolved_input_values"
        seeded_name = "_gm_seeded_input_values_cache"
        lazy_name = "_gm_lazy_input_values_cache"
        dict.pop(state, resolved_name)
        seeded_cache = dict.pop(state, seeded_name)
        lazy_cache = dict.pop(state, lazy_name)
        for index, target in enumerate((resolved_name, seeded_name, lazy_name)):
            hostile_key = CollidingStateKey(f"hostile-{index}", target)
            dict.__setitem__(state, hostile_key, object())
            if target == seeded_name:
                dict.__setitem__(state, seeded_name, seeded_cache)
            elif target == lazy_name:
                dict.__setitem__(state, lazy_name, lazy_cache)
        interface_class = type(interface)

        def custom_getattribute(current_interface, name):
            return object.__getattribute__(current_interface, name)

        CollidingStateKey.armed = True
        type.__setattr__(interface_class, "__getattribute__", custom_getattribute)
        try:
            resolved = manager.related
        finally:
            type.__delattr__(interface_class, "__getattribute__")
            CollidingStateKey.armed = False

        marker_names = {
            key
            for key in vars(interface)
            if type(key) is str and key in {resolved_name, seeded_name, lazy_name}
        }
        self.assertEqual(marker_names, {resolved_name})
        self.assertEqual(equality_calls, [])
        self.assertIsNot(resolved, original)
        del seeded_cache
        del lazy_cache
        del original
        gc.collect()
        self.assertIsNone(original_ref())

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_dispatch_fallback_excludes_exact_reserved_name_aliases(self):
        mirror_dispatch_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        original_ref = ref(original)
        manager = HydratedCalculation(original)
        outer_identification = manager.identification
        interface = manager._interface

        def dynamic_name(value):
            return value.encode().decode()

        resolved_name = dynamic_name("_resolved_input_values")
        seeded_name = dynamic_name("_gm_seeded_input_values_cache")
        lazy_name = dynamic_name("_gm_lazy_input_values_cache")
        self.assertIsNot(resolved_name, "_resolved_input_values")
        self.assertIsNot(seeded_name, "_gm_seeded_input_values_cache")
        self.assertIsNot(lazy_name, "_gm_lazy_input_values_cache")
        replacement_state = {
            dynamic_name("identification"): outer_identification,
            resolved_name: {"related": original},
            seeded_name: {"related": original},
            lazy_name: set(),
        }
        object.__setattr__(interface, "__dict__", replacement_state)
        original._invalidate_manager_state("stale")
        interface_class = type(interface)

        def custom_getattribute(current_interface, name):
            if name in {
                "_gm_seeded_input_values_cache",
                "_gm_lazy_input_values_cache",
            }:
                mirror_dispatch_calls.append(name)
            return object.__getattribute__(current_interface, name)

        type.__setattr__(interface_class, "__getattribute__", custom_getattribute)
        try:
            resolved = manager.related
        finally:
            type.__delattr__(interface_class, "__getattribute__")

        reserved_names = {
            key
            for key in vars(interface)
            if type(key) is str
            and key
            in {
                "_gm_seeded_input_values_cache",
                "_gm_lazy_input_values_cache",
            }
        }
        self.assertEqual(reserved_names, set())
        self.assertEqual(mirror_dispatch_calls, [])
        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})
        del original
        gc.collect()
        self.assertIsNone(original_ref())

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_cross_field_normalizer_can_resolve_seeded_field_concurrently(self):
        worker_results = []
        worker_finished = threading.Event()
        manager = None
        second_accessor = None
        exercise_concurrent_access = False

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        def resolve_second(value):
            if not exercise_concurrent_access:
                return value

            def resolve():
                worker_results.append(second_accessor(manager._interface))
                worker_finished.set()

            worker = threading.Thread(target=resolve)
            worker.start()
            self.assertTrue(worker_finished.wait(1))
            worker.join(1)
            self.assertFalse(worker.is_alive())
            return value

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                first = Input(RelatedManager, normalizer=resolve_second)
                second = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        first = RelatedManager("first-id")
        second = RelatedManager("second-id")
        manager = HydratedCalculation(first, second)
        first._invalidate_manager_state("stale")
        second._invalidate_manager_state("stale")
        first_accessor = HydratedCalculation._attributes["first"]
        second_accessor = HydratedCalculation._attributes["second"]
        exercise_concurrent_access = True

        resolved = first_accessor(manager._interface)

        self.assertIsNot(resolved, first)
        self.assertEqual(len(worker_results), 1)
        self.assertIsNot(worker_results[0], second)
        self.assertEqual(
            worker_results[0].identification,
            {"id": "second-id"},
        )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_same_thread_recursive_seed_resolution_resets_claim(self):
        manager = None
        accessor = None
        recurse = False

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        def resolve_again(value):
            if recurse:
                return accessor(manager._interface)
            return value

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager, normalizer=resolve_again)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")
        accessor = HydratedCalculation._attributes["related"]
        recurse = True

        with self.assertRaisesRegex(RuntimeError, "related"):
            accessor(manager._interface)

        recurse = False
        resolved = accessor(manager._interface)
        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_concurrent_cross_field_dependency_cycle_does_not_deadlock(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                first = Input(RelatedManager)
                second = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)

        for repeat in range(3):
            with self.subTest(repeat=repeat):
                barrier = threading.Barrier(2)

                class BarrierDependencies(list):
                    def __init__(self, values):
                        super().__init__(values)
                        self._barrier_pending = True
                        self._barrier_lock = threading.Lock()

                    def __iter__(self, current_barrier=barrier):
                        with self._barrier_lock:
                            wait_at_barrier = self._barrier_pending
                            self._barrier_pending = False
                        if wait_at_barrier:
                            current_barrier.wait(5)
                        return super().__iter__()

                first_input = HydratedCalculation.Interface.input_fields["first"]
                second_input = HydratedCalculation.Interface.input_fields["second"]
                first_input.depends_on = []
                second_input.depends_on = []
                first = RelatedManager(f"first-{repeat}")
                second = RelatedManager(f"second-{repeat}")
                manager = HydratedCalculation(first, second)
                first_input.depends_on = BarrierDependencies(["second"])
                second_input.depends_on = BarrierDependencies(["first"])
                first._invalidate_manager_state("stale")
                second._invalidate_manager_state("stale")
                errors = []

                def resolve(
                    field_name,
                    current_manager=manager,
                    current_errors=errors,
                ):
                    try:
                        getattr(current_manager, field_name)
                    except AttributeEvaluationError as error:
                        current_errors.append(error)

                threads = [
                    threading.Thread(
                        target=resolve,
                        args=(field_name,),
                        daemon=True,
                    )
                    for field_name in ("first", "second")
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(1)

                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertEqual(len(errors), 2)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_concurrent_stale_seed_recast_publishes_one_wrapper(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")
        accessor = HydratedCalculation._attributes["related"]
        outer_input = HydratedCalculation.Interface.input_fields["related"]
        original_cast = Input.cast
        cast_count = 0
        count_lock = threading.Lock()
        first_cast_started = threading.Event()
        second_cast_started = threading.Event()
        release_cast = threading.Event()
        second_worker_entered = threading.Event()
        results = []

        def blocking_cast(
            input_field,
            value,
            identification=None,
            *,
            cache_context=None,
        ):
            nonlocal cast_count
            if input_field is outer_input:
                with count_lock:
                    cast_count += 1
                    current_count = cast_count
                first_cast_started.set()
                if current_count > 1:
                    second_cast_started.set()
                self.assertTrue(release_cast.wait(5))
            return original_cast(
                input_field,
                value,
                identification,
                cache_context=cache_context,
            )

        def resolve(*, second=False):
            if second:
                second_worker_entered.set()
            results.append(accessor(manager._interface))

        with patch.object(Input, "cast", blocking_cast):
            first_thread = threading.Thread(target=resolve)
            second_thread = threading.Thread(
                target=resolve,
                kwargs={"second": True},
            )
            first_thread.start()
            self.assertTrue(first_cast_started.wait(5))
            second_thread.start()
            self.assertTrue(second_worker_entered.wait(5))
            second_started_before_release = second_cast_started.wait(0.2)
            release_cast.set()
            first_thread.join(5)
            second_thread.join(5)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertFalse(second_started_before_release)
        self.assertEqual(cast_count, 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])
        self.assertIsNot(results[0], original)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_failed_seed_recast_notifies_waiter_and_allows_retry(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original._invalidate_manager_state("stale")
        accessor = HydratedCalculation._attributes["related"]
        outer_input = HydratedCalculation.Interface.input_fields["related"]
        origin = base_interface_module._seeded_interface_origin_by_id(
            id(manager._interface)
        )
        self.assertIsNotNone(origin)
        field_origin = origin.fields["related"]
        waiter_waiting = threading.Event()

        class RecordingCondition(threading.Condition):
            def wait(self, timeout=None):
                waiter_waiting.set()
                return super().wait(timeout)

        field_origin.condition = RecordingCondition()
        original_cast = Input.cast
        first_cast_started = threading.Event()
        release_first_cast = threading.Event()
        cast_count = 0
        count_lock = threading.Lock()
        errors = []
        results = []

        def flaky_cast(
            input_field,
            value,
            identification=None,
            *,
            cache_context=None,
        ):
            nonlocal cast_count
            if input_field is outer_input:
                with count_lock:
                    cast_count += 1
                    current_count = cast_count
                if current_count == 1:
                    first_cast_started.set()
                    self.assertTrue(release_first_cast.wait(5))
                    error = ValueError("first cast failed")
                    raise error
            return original_cast(
                input_field,
                value,
                identification,
                cache_context=cache_context,
            )

        def resolve():
            try:
                results.append(accessor(manager._interface))
            except ValueError as error:
                errors.append(error)

        with patch.object(Input, "cast", flaky_cast):
            first_thread = threading.Thread(target=resolve)
            second_thread = threading.Thread(target=resolve)
            first_thread.start()
            self.assertTrue(first_cast_started.wait(5))
            second_thread.start()
            self.assertTrue(waiter_waiting.wait(5))
            release_first_cast.set()
            first_thread.join(5)
            second_thread.join(5)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(str(errors[0]), "first cast failed")
        self.assertEqual(cast_count, 2)
        self.assertEqual(len(results), 1)
        self.assertIsNot(results[0], original)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_in_place_seeded_identification_mutation_reuses_aligned_manager(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        original.identification["id"] = "updated-id"

        resolved = manager.related

        self.assertIs(resolved, original)
        self.assertIs(manager.identification["related"], original.identification)
        self.assertEqual(resolved.identification, {"id": "updated-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seeded_manager_reuses_after_benign_nested_attribute_cache_fill(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)

        self.assertEqual(original.id, "related-id")
        resolved = manager.related

        self.assertIs(resolved, original)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seeded_manager_reuses_after_dependency_cache_fill(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        first_outer = HydratedCalculation(original)
        second_outer = HydratedCalculation(original)

        with DependencyTracker():
            self.assertIs(first_outer.related, original)
            self.assertIs(first_outer.related, original)
        self.assertIsNotNone(original._identification_dependency_cache)

        self.assertIs(second_outer.related, original)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_stale_seed_recast_transitions_to_lazy_dependency_reuse(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                first = Input(str, depends_on=["related"])
                second = Input(str, depends_on=["related"])

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original, "first", "second")
        original._invalidate_manager_state("stale")

        self.assertEqual(manager.first, "first")
        replacement = manager._interface._resolved_input_values["related"]
        self.assertIsNot(replacement, original)
        self.assertEqual(manager.second, "second")

        self.assertIs(
            manager._interface._resolved_input_values["related"],
            replacement,
        )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seeded_manager_provenance_mutation_evicts_cached_wrapper(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original_init = RelatedManager.__init__

        def delegated_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)

        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        with patch.object(RelatedManager, "__init__", delegated_init):
            resolved = manager.related

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_input_state_descriptor_mutation_is_not_invoked_during_eviction(self):
        hook_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        outer_input = HydratedCalculation.Interface.input_fields["related"]

        def invoke_hostile_is_manager(_self):
            if _self is outer_input:
                hook_calls.append("is_manager")
                raise AssertionError
            return object.__getattribute__(_self, "__dict__")["is_manager"]

        hostile_is_manager = property(invoke_hostile_is_manager)

        with patch.object(Input, "is_manager", hostile_is_manager, create=True):
            resolved = manager.related

        self.assertEqual(hook_calls, [])
        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_installed_descriptor_provenance_mutation_evicts_seed(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        descriptor = inspect.getattr_static(HydratedCalculation, "related")
        installation = descriptor._gm_manager_attribute_descriptor_installation
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        descriptor._gm_manager_attribute_descriptor_installation = None
        try:
            resolved = manager.related
        finally:
            descriptor._gm_manager_attribute_descriptor_installation = installation

        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hostile_parent_class_descriptor_is_not_invoked_during_eviction(self):
        hook_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        class HostileParentDescriptor:
            def __get__(self, _instance, _owner=None):
                hook_calls.append("parent")
                raise AssertionError

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        interface_class = HydratedCalculation.Interface
        parent_class = interface_class.__dict__["_parent_class"]
        type.__setattr__(
            interface_class,
            "_parent_class",
            HostileParentDescriptor(),
        )
        try:
            resolved = manager.related
        finally:
            type.__setattr__(interface_class, "_parent_class", parent_class)

        self.assertEqual(hook_calls, [])
        self.assertIsNot(resolved, original)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_invalidation_after_first_outer_access_keeps_existing_cache_boundary(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        original = RelatedManager("related-id")
        manager = HydratedCalculation(original)
        first = manager.related
        first._invalidate_manager_state("later")

        second = manager.related

        self.assertIs(first, original)
        self.assertIs(second, first)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_manager_input_cast_from_id_and_dict_seeds_exact_wrapper(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class CompositeManager(GeneralManager):
            class Interface(CalculationInterface):
                code = Input(str)
                version = Input(int)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                composite = Input(CompositeManager)

        for manager_class in (RelatedManager, CompositeManager, HydratedCalculation):
            GeneralManagerMeta.ensure_attributes_initialized(manager_class)

        manager = HydratedCalculation("related-id", {"code": "x", "version": 2})

        related = manager._interface._resolved_input_values["related"]
        composite = manager._interface._resolved_input_values["composite"]
        self.assertIsInstance(related, RelatedManager)
        self.assertIsInstance(composite, CompositeManager)
        self.assertEqual(manager.identification["related"], {"id": "related-id"})
        self.assertEqual(
            manager.identification["composite"], {"code": "x", "version": 2}
        )
        self.assertIs(manager.related, related)
        self.assertIs(manager.composite, composite)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_failed_input_processing_does_not_seed_resolved_values(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class RejectedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                accepted = Input(int, validator=lambda value: value > 0)

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(RejectedCalculation)
        interface = RejectedCalculation.Interface.__new__(RejectedCalculation.Interface)

        with self.assertRaises(ValueError):
            RejectedCalculation.Interface.__init__(interface, "related-id", -1)

        self.assertNotIn("_resolved_input_values", vars(interface))

    @override_settings(
        AUTOCREATE_GRAPHQL=False,
        GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True,
    )
    def test_parse_cast_value_and_normalizer_failures_do_not_seed(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class MissingCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                value = Input(int)

        class TypeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                value = Input(int)

        class ValueCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                value = Input(int, possible_values=[1])

        def reject_normalization(_value):
            raise RuntimeError

        class NormalizerCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)
                value = Input(int, normalizer=reject_normalization)

        cases = (
            (MissingCalculation, ("related-id",), TypeError),
            (TypeCalculation, ("related-id", object()), TypeError),
            (ValueCalculation, ("related-id", 2), ValueError),
            (NormalizerCalculation, ("related-id", 1), RuntimeError),
        )
        for manager_class, args, error_type in cases:
            interface_class = manager_class.Interface
            interface = interface_class.__new__(interface_class)
            with self.subTest(manager_class=manager_class):
                with self.assertRaises(error_type):
                    interface_class.__init__(interface, *args)
                self.assertNotIn("_resolved_input_values", vars(interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_when_calculation_accessor_is_substituted(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        HydratedCalculation._attributes["related"] = lambda _interface: object()

        manager = HydratedCalculation("related-id")

        self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_custom_interface_instance_dispatch(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class CustomGetattributeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

                def __getattribute__(self, name):
                    return super().__getattribute__(name)

        class CustomGetattrCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

                def __getattr__(self, name):
                    raise AttributeError(name)

        class CustomSetattrCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

                def __setattr__(self, name, value):
                    super().__setattr__(name, value)

        for manager_class in (
            CustomGetattributeCalculation,
            CustomGetattrCalculation,
            CustomSetattrCalculation,
        ):
            GeneralManagerMeta.ensure_attributes_initialized(manager_class)
            with self.subTest(manager_class=manager_class):
                manager = manager_class("related-id")
                self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_custom_nested_manager_instance_dispatch(self):
        class CustomGetattributeManager(GeneralManager):
            def __getattribute__(self, name):
                return super().__getattribute__(name)

            class Interface(CalculationInterface):
                id = Input(str)

        class CustomGetattrManager(GeneralManager):
            def __getattr__(self, name):
                return super().__getattr__(name)

            class Interface(CalculationInterface):
                id = Input(str)

        class CustomSetattrManager(GeneralManager):
            def __setattr__(self, name, value):
                super().__setattr__(name, value)

            class Interface(CalculationInterface):
                id = Input(str)

        related_classes = (
            CustomGetattributeManager,
            CustomGetattrManager,
            CustomSetattrManager,
        )
        for related_class in related_classes:
            interface_class = type(
                f"{related_class.__name__}InputInterface",
                (CalculationInterface,),
                {"related": Input(related_class)},
            )
            manager_class = GeneralManagerMeta(
                f"{related_class.__name__}InputCalculation",
                (GeneralManager,),
                {
                    "__module__": __name__,
                    "Interface": interface_class,
                },
            )
            GeneralManagerMeta.ensure_attributes_initialized(manager_class)
            with self.subTest(related_class=related_class):
                manager = manager_class("related-id")
                self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_nested_manager_construction_fallback_matrix_rebuilds_once(self):
        """Nested manager customizations never opt into the hydration fast path."""

        constructor_code = GeneralManager.__dict__["__init__"].__code__

        def build_related(
            case: str,
            hook_calls: list[str],
            dispatch_active: dict[str, bool],
            hostile_calls: list[str],
        ) -> type[GeneralManager]:
            interface_class = type(
                f"{case.title()}RelatedInterface",
                (CalculationInterface,),
                {
                    "__module__": __name__,
                    "id": Input(str),
                },
            )

            def custom_new(cls, *_args, **_kwargs):
                hook_calls.append("new")
                return object.__new__(cls)

            def custom_init(instance, *args, **kwargs):
                hook_calls.append("init")
                return GeneralManager.__init__(instance, *args, **kwargs)

            def custom_identification(instance):
                hook_calls.append("identification")
                return object.__getattribute__(
                    instance,
                    "_GeneralManager__id",
                )

            def custom_validity(instance, *args, **kwargs):
                hook_calls.append("validity")
                return GeneralManager._ensure_manager_state_valid(
                    instance,
                    *args,
                    **kwargs,
                )

            def custom_getattribute(instance, name):
                if dispatch_active["value"] and name in {
                    "_interface",
                    "_attribute_value_cache",
                    "_identification_dependency_cache",
                    "_manager_state_valid",
                    "_manager_state_reason",
                }:
                    hostile_calls.append(name)
                    raise AssertionError
                return object.__getattribute__(instance, name)

            def custom_getattr(instance, name):
                if dispatch_active["value"]:
                    hostile_calls.append(name)
                    raise AssertionError
                raise AttributeError(name)

            def custom_setattr(instance, name, value):
                if dispatch_active["value"] and name in {
                    "_interface",
                    "_attribute_value_cache",
                    "_identification_dependency_cache",
                    "_manager_state_valid",
                    "_manager_state_reason",
                }:
                    hostile_calls.append(name)
                    raise AssertionError
                object.__setattr__(instance, name, value)

            @classmethod
            def custom_dependency(cls, identification):
                hook_calls.append("dependency")
                return GeneralManager._track_identification_dependency_active.__func__(
                    cls,
                    identification,
                )

            @classmethod
            def custom_dependency_entry(cls, identification):
                hook_calls.append("dependency-entry")
                return GeneralManager._track_identification_dependency.__func__(
                    cls,
                    identification,
                )

            def custom_own_dependency(instance):
                hook_calls.append("own-dependency")
                return GeneralManager._track_own_identification_dependency_active(
                    instance,
                )

            manager_attrs: dict[str, object] = {
                "__module__": __name__,
                "Interface": interface_class,
            }
            if case == "new":
                manager_attrs["__new__"] = custom_new
            elif case == "init":
                manager_attrs["__init__"] = custom_init
            elif case == "getattribute":
                manager_attrs["__getattribute__"] = custom_getattribute
            elif case == "getattr":
                manager_attrs["__getattr__"] = custom_getattr
            elif case == "setattr":
                manager_attrs["__setattr__"] = custom_setattr
            elif case == "identification":
                manager_attrs["identification"] = property(custom_identification)
            elif case == "validity":
                manager_attrs["_ensure_manager_state_valid"] = custom_validity
            elif case == "dependency":
                manager_attrs["_gm_uses_default_identification_dependency_active"] = (
                    False
                )
                manager_attrs["_track_identification_dependency"] = (
                    custom_dependency_entry
                )
                manager_attrs["_track_identification_dependency_active"] = (
                    custom_dependency
                )
                manager_attrs["_track_own_identification_dependency_active"] = (
                    custom_own_dependency
                )

            if case == "metaclass_call":

                class CustomMeta(GeneralManagerMeta):
                    def __call__(cls, *args, **kwargs):
                        hook_calls.append("metaclass-call")
                        return super().__call__(*args, **kwargs)

                manager_class = CustomMeta(
                    f"{case.title()}RelatedManager",
                    (GeneralManager,),
                    manager_attrs,
                )
            else:
                manager_class = GeneralManagerMeta(
                    f"{case.title()}RelatedManager",
                    (GeneralManager,),
                    manager_attrs,
                )
            GeneralManagerMeta.ensure_attributes_initialized(manager_class)
            return manager_class

        cases = (
            "new",
            "init",
            "metaclass_call",
            "getattribute",
            "getattr",
            "setattr",
            "identification",
            "validity",
            "dependency",
            "state_shape",
        )
        for case in cases:
            with self.subTest(case=case):
                hook_calls: list[str] = []
                dispatch_active = {"value": False}
                hostile_calls: list[str] = []
                related_class = build_related(
                    case,
                    hook_calls,
                    dispatch_active,
                    hostile_calls,
                )

                class OuterCalculation(GeneralManager):
                    class Interface(CalculationInterface):
                        related = Input(related_class)

                GeneralManagerMeta.ensure_attributes_initialized(OuterCalculation)
                nested = related_class("related-id")
                hook_calls.clear()
                dispatch_active["value"] = True
                if case == "state_shape":
                    vars(nested)["unexpected_state"] = object()

                outer = OuterCalculation(nested)
                dispatch_active["value"] = False
                self.assertNotIn(
                    "_resolved_input_values",
                    vars(outer._interface),
                )
                self.assertEqual(
                    outer.identification,
                    {"related": {"id": "related-id"}},
                )
                if case == "identification":
                    self.assertEqual(hook_calls, ["identification"])
                else:
                    self.assertEqual(hook_calls, [])
                self.assertEqual(hostile_calls, [])
                hook_calls.clear()
                with count_profiled_calls(
                    constructor_code,
                    lambda value, expected=related_class: value.__class__ is expected,
                ) as nested_constructors:
                    resolved = outer.related

                self.assertEqual(nested_constructors.value, 1)
                self.assertIsNot(resolved, nested)
                self.assertEqual(
                    resolved.identification,
                    {"id": "related-id"},
                )

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_nested_manager_identification_error_timing_is_unchanged(self):
        hook_calls: list[str] = []

        def raising_identification(_instance):
            hook_calls.append("identification")
            raise RuntimeError

        class RelatedManager(GeneralManager):
            identification = property(raising_identification)

            class Interface(CalculationInterface):
                id = Input(str)

        class OuterCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(OuterCalculation)
        nested = RelatedManager("related-id")
        with self.assertRaises(RuntimeError):
            OuterCalculation(nested)
        self.assertEqual(hook_calls, ["identification"])

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_nested_manager_hostile_state_is_hook_free_during_eligibility(self):
        """Unsafe nested state is rejected without dispatching user objects."""

        class HostileStateKey:
            active = False
            calls = 0

            def __hash__(self):
                if type(self).active:
                    type(self).calls += 1
                    raise AssertionError
                return 1

            def __eq__(self, _other):
                if type(self).active:
                    type(self).calls += 1
                    raise AssertionError
                return False

        class HostileDescriptor:
            def __get__(self, _instance, _owner):
                HostileStateKey.calls += 1
                raise AssertionError

        class HostileMapping(dict):
            def items(self):
                HostileStateKey.calls += 1
                raise AssertionError

            def __iter__(self):
                HostileStateKey.calls += 1
                raise AssertionError

        class HostileIdentificationValue:
            active = False
            calls = 0

            def __getattribute__(self, name):
                if type.__getattribute__(type(self), "active") and name not in {
                    "active",
                    "calls",
                }:
                    type.__setattr__(type(self), "calls", type(self).calls + 1)
                    raise AssertionError
                return object.__getattribute__(self, name)

            def __hash__(self):
                type(self).calls += 1
                raise AssertionError

            def __eq__(self, _other):
                type(self).calls += 1
                raise AssertionError

            def __repr__(self):
                type(self).calls += 1
                raise AssertionError

        def build_classes():
            class RelatedManager(GeneralManager):
                class Interface(CalculationInterface):
                    id = Input(str)

            class OuterCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    related = Input(RelatedManager)

            GeneralManagerMeta.ensure_attributes_initialized(OuterCalculation)
            return RelatedManager, OuterCalculation

        for case in (
            "non_string_state_key",
            "descriptor",
            "mapping",
            "identification_value",
        ):
            with self.subTest(case=case):
                RelatedManager, OuterCalculation = build_classes()
                nested = RelatedManager("related-id")
                hostile_key = HostileStateKey()
                if case == "non_string_state_key":
                    dict.__setitem__(
                        vars(nested),
                        hostile_key,
                        object(),
                    )
                    HostileStateKey.active = True
                elif case == "descriptor":
                    type.__setattr__(
                        RelatedManager,
                        "_identification_dependency_cache",
                        HostileDescriptor(),
                    )
                elif case == "mapping":
                    dict.__setitem__(
                        vars(nested._interface),
                        "identification",
                        HostileMapping(id="related-id"),
                    )
                else:
                    hostile_value = HostileIdentificationValue()
                    private_id = vars(nested)["_GeneralManager__id"]
                    dict.__setitem__(private_id, "id", hostile_value)
                    HostileIdentificationValue.active = True

                try:
                    HostileStateKey.calls = 0
                    HostileIdentificationValue.calls = 0
                    outer = OuterCalculation(nested)
                    self.assertIs(
                        outer.identification["related"].get("id"),
                        (
                            hostile_value
                            if case == "identification_value"
                            else "related-id"
                        ),
                    )
                    self.assertEqual(HostileStateKey.calls, 0)
                    self.assertEqual(HostileIdentificationValue.calls, 0)
                    if case == "identification_value":
                        self.assertIs(outer.related, nested)
                        self.assertIn(
                            "_resolved_input_values",
                            vars(outer._interface),
                        )
                    else:
                        self.assertNotIn(
                            "_resolved_input_values",
                            vars(outer._interface),
                        )
                finally:
                    HostileStateKey.active = False
                    HostileIdentificationValue.active = False

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_nested_fallback_dependency_sets_are_exact_across_cache_paths(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

            def __init__(self, *args, **kwargs):
                return super().__init__(*args, **kwargs)

        class OuterCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(OuterCalculation)
        nested = RelatedManager("related-id")

        with DependencyTracker() as construction_dependencies:
            outer = OuterCalculation(nested)

        expected_outer = (
            OuterCalculation.__name__,
            "identification",
            serialize_dependency_identifier(outer.identification),
        )
        expected_nested = (
            RelatedManager.__name__,
            "identification",
            '{"id": "related-id"}',
        )
        self.assertEqual(construction_dependencies, {expected_outer})
        self.assertNotIn("_resolved_input_values", vars(outer._interface))

        with DependencyTracker() as first_dependencies:
            first = outer.related
        with DependencyTracker() as later_dependencies:
            second = outer.related

        self.assertIsNot(first, nested)
        self.assertIs(second, first)
        self.assertEqual(first_dependencies, {expected_nested})
        self.assertEqual(later_dependencies, {expected_nested})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_custom_interface_metaclass_equality_is_not_invoked_by_seed(self):
        equality_calls = []

        class RecordingMeta(type(CalculationInterface)):
            def __eq__(cls, other):
                equality_calls.append(other)
                return cls is other

            __hash__ = type.__hash__

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class MetaclassInterface(CalculationInterface, metaclass=RecordingMeta):
            related = Input(RelatedManager)

        class MetaclassCalculation(GeneralManager):
            Interface = MetaclassInterface

        GeneralManagerMeta.ensure_attributes_initialized(MetaclassCalculation)
        equality_calls.clear()

        manager = MetaclassCalculation("related-id")

        self.assertEqual(equality_calls, [])
        self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_custom_interface_metaclass_dispatch(self):
        class CustomCallMeta(type(CalculationInterface)):
            def __call__(cls, *args, **kwargs):
                return super().__call__(*args, **kwargs)

            def __getattribute__(cls, name):
                return super().__getattribute__(name)

            def __getattr__(cls, name):
                raise AttributeError(name)

            def __setattr__(cls, name, value):
                super().__setattr__(name, value)

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class MetaclassInterface(CalculationInterface, metaclass=CustomCallMeta):
            related = Input(RelatedManager)

        class MetaclassCalculation(GeneralManager):
            Interface = MetaclassInterface

        GeneralManagerMeta.ensure_attributes_initialized(MetaclassCalculation)

        manager = MetaclassCalculation("related-id")

        self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hostile_interface_dict_descriptor_is_not_invoked_by_seed(self):
        hook_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HostileCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

                @property
                def __dict__(self):
                    hook_calls.append("interface-dict")
                    raise AssertionError

        GeneralManagerMeta.ensure_attributes_initialized(HostileCalculation)

        manager = HostileCalculation("related-id")

        self.assertEqual(manager.identification, {"related": {"id": "related-id"}})
        self.assertEqual(hook_calls, [])

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hostile_nested_manager_dict_descriptor_is_not_invoked_by_seed(self):
        hook_calls = []

        class HostileRelatedManager(GeneralManager):
            @property
            def __dict__(self):
                hook_calls.append("manager-dict")
                raise AssertionError

            class Interface(CalculationInterface):
                id = Input(str)

        class HostileCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(HostileRelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HostileCalculation)

        manager = HostileCalculation("related-id")

        self.assertEqual(manager.identification, {"related": {"id": "related-id"}})
        self.assertEqual(hook_calls, [])
        self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_hostile_resolved_values_setter_is_not_invoked_by_seed(self):
        hook_calls = []

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HostileCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

                @property
                def _resolved_input_values(self):
                    return None

                @_resolved_input_values.setter
                def _resolved_input_values(self, value):
                    hook_calls.append(value)

        GeneralManagerMeta.ensure_attributes_initialized(HostileCalculation)

        manager = HostileCalculation("related-id")

        self.assertEqual(manager.identification, {"related": {"id": "related-id"}})
        self.assertEqual(hook_calls, [])
        self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_dynamic_hostile_state_names_are_rejected_without_hooks(self):
        hook_calls = []
        dynamic_dict_name = "".join(("__", "dict", "__"))
        dynamic_resolved_name = "".join(("_resolved", "_input", "_values"))

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        hostile_interface = type(
            "DynamicHostileInterface",
            (CalculationInterface,),
            {
                "related": Input(RelatedManager),
                dynamic_dict_name: property(lambda _self: hook_calls.append("dict")),
                dynamic_resolved_name: property(
                    lambda _self: None,
                    lambda _self, _value: hook_calls.append("resolved"),
                ),
            },
        )

        class HostileCalculation(GeneralManager):
            Interface = hostile_interface

        GeneralManagerMeta.ensure_attributes_initialized(HostileCalculation)

        manager = HostileCalculation("related-id")

        self.assertEqual(manager.identification, {"related": {"id": "related-id"}})
        self.assertEqual(hook_calls, [])

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_in_place_canonical_function_mutations_revoke_seed(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        interface_class = HydratedCalculation.Interface
        original_code = Input.cast.__code__
        original_defaults = Input.cast.__defaults__
        original_kwdefaults = Input.cast.__kwdefaults__
        original_annotations = Input.cast.__annotations__
        original_annotation_items = tuple(original_annotations.items())
        related = RelatedManager("related-id")

        def replacement(_self, _value):
            return None

        def run_seed() -> object:
            interface = interface_class.__new__(interface_class)
            identification = {
                "related": related,
            }
            base_interface_module._seed_calculation_resolved_manager_values(
                interface, identification
            )
            return interface

        mutations = (
            lambda: setattr(Input.cast, "__code__", replacement.__code__),
            lambda: setattr(Input.cast, "__defaults__", (object(),)),
            lambda: setattr(Input.cast, "__kwdefaults__", {"changed": object()}),
            lambda: Input.cast.__annotations__.__setitem__("changed", object()),
        )
        try:
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    mutation()
                    interface = run_seed()
                    self.assertNotIn("_resolved_input_values", vars(interface))
                    Input.cast.__code__ = original_code
                    Input.cast.__defaults__ = original_defaults
                    Input.cast.__kwdefaults__ = original_kwdefaults
                    original_annotations.clear()
                    original_annotations.update(original_annotation_items)
                    Input.cast.__annotations__ = original_annotations
        finally:
            Input.cast.__code__ = original_code
            Input.cast.__defaults__ = original_defaults
            Input.cast.__kwdefaults__ = original_kwdefaults
            original_annotations.clear()
            original_annotations.update(original_annotation_items)
            Input.cast.__annotations__ = original_annotations

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_in_place_code_mutations_across_canonical_owners_revoke_seed(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        interface_class = HydratedCalculation.Interface
        related = RelatedManager("related-id")

        def replacement(*_args, **_kwargs):
            return None

        functions = (
            CalculationReadCapability.get_attributes,
            CalculationLifecycleCapability.post_create,
            InterfaceBase._process_input_field,
            GeneralManager._ensure_manager_state_valid,
            GeneralManagerMeta.__getattribute__,
        )
        for function in functions:
            original_code = function.__code__
            try:
                with self.subTest(function=function):
                    function.__code__ = replacement.__code__
                    interface = interface_class.__new__(interface_class)
                    base_interface_module._seed_calculation_resolved_manager_values(
                        interface,
                        {"related": related},
                    )
                    self.assertNotIn("_resolved_input_values", vars(interface))
            finally:
                function.__code__ = original_code

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_rejects_monkeypatched_canonical_implementations(self):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)

        original_read = CalculationReadCapability.get_attributes
        original_lifecycle = CalculationLifecycleCapability.post_create
        original_cast = Input.cast
        original_normalize = Input.normalize
        original_interface_init = InterfaceBase.__init__
        original_interface_getattribute = InterfaceBase.__getattribute__
        original_interface_setattr = InterfaceBase.__setattr__
        original_parse = InterfaceBase.parse_input_fields_to_identification
        original_process = InterfaceBase._process_input_field
        original_format = InterfaceBase.format_identification
        original_calculation_setattr = CalculationInterface.__setattr__
        original_manager_init = GeneralManager.__init__
        original_manager_getattribute = GeneralManager.__getattribute__
        original_manager_setattr = GeneralManager.__setattr__
        original_identification = GeneralManager.identification
        original_meta_getattribute = GeneralManagerMeta.__getattribute__
        original_meta_setattr = GeneralManagerMeta.__setattr__
        mutations = (
            (
                CalculationReadCapability,
                "get_attributes",
                lambda self, interface_cls: original_read(self, interface_cls),
            ),
            (
                CalculationLifecycleCapability,
                "post_create",
                lambda self, **kwargs: original_lifecycle(self, **kwargs),
            ),
            (
                Input,
                "cast",
                lambda self, value, identification=None, *, cache_context=None: (
                    original_cast(
                        self,
                        value,
                        identification,
                        cache_context=cache_context,
                    )
                ),
            ),
            (
                Input,
                "normalize",
                lambda self, value, identification=None, *, cache_context=None: (
                    original_normalize(
                        self,
                        value,
                        identification,
                        cache_context=cache_context,
                    )
                ),
            ),
            (
                InterfaceBase,
                "__init__",
                lambda self, *args, **kwargs: original_interface_init(
                    self, *args, **kwargs
                ),
            ),
            (
                InterfaceBase,
                "__getattribute__",
                lambda self, name: original_interface_getattribute(self, name),
            ),
            (
                InterfaceBase,
                "__setattr__",
                lambda self, name, value: original_interface_setattr(self, name, value),
            ),
            (
                InterfaceBase,
                "parse_input_fields_to_identification",
                lambda self, *args, **kwargs: original_parse(self, *args, **kwargs),
            ),
            (
                InterfaceBase,
                "_process_input_field",
                lambda self, name, field, value, identification, *, cache_context: (
                    original_process(
                        self,
                        name,
                        field,
                        value,
                        identification,
                        cache_context=cache_context,
                    )
                ),
            ),
            (
                InterfaceBase,
                "format_identification",
                staticmethod(lambda identification: original_format(identification)),
            ),
            (
                CalculationInterface,
                "__setattr__",
                lambda self, name, value: original_calculation_setattr(
                    self, name, value
                ),
            ),
            (
                GeneralManager,
                "__init__",
                lambda self, *args, **kwargs: original_manager_init(
                    self, *args, **kwargs
                ),
            ),
            (
                GeneralManager,
                "__getattribute__",
                lambda self, name: original_manager_getattribute(self, name),
            ),
            (
                GeneralManager,
                "__setattr__",
                lambda self, name, value: original_manager_setattr(self, name, value),
            ),
            (
                GeneralManager,
                "identification",
                property(lambda self: original_identification.__get__(self)),
            ),
            (
                GeneralManagerMeta,
                "__getattribute__",
                lambda cls, name: original_meta_getattribute(cls, name),
            ),
            (
                GeneralManagerMeta,
                "__setattr__",
                lambda cls, name, value: original_meta_setattr(cls, name, value),
            ),
        )
        for owner, name, replacement in mutations:
            with self.subTest(owner=owner, name=name):
                with patch.object(owner, name, replacement):
                    manager = HydratedCalculation("related-id")
                    self.assertNotIn("_resolved_input_values", vars(manager._interface))

        with patch.object(
            InterfaceBase,
            "__getattr__",
            lambda _self, name: (_ for _ in ()).throw(AttributeError(name)),
            create=True,
        ):
            manager = HydratedCalculation("related-id")
            self.assertNotIn("_resolved_input_values", vars(manager._interface))

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_hostile_calculation_capability_dispatch(self):
        """Capability instance dispatch changes must never run during seeding."""

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class HydratedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager)

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
        constructor_code = GeneralManager.__dict__["__init__"].__code__

        for capability_class in (
            CalculationReadCapability,
            CalculationLifecycleCapability,
        ):
            hook_calls = []

            def hostile_getattribute(_self, name, calls=hook_calls):
                calls.append(name)
                raise AssertionError

            original = capability_class.__dict__.get("__getattribute__")
            type.__setattr__(
                capability_class,
                "__getattribute__",
                hostile_getattribute,
            )
            try:
                with self.subTest(capability=capability_class.__name__):
                    manager = HydratedCalculation("related-id")
            finally:
                if original is None:
                    type.__delattr__(capability_class, "__getattribute__")
                else:
                    type.__setattr__(capability_class, "__getattribute__", original)

            self.assertEqual(hook_calls, [])
            self.assertNotIn("_resolved_input_values", vars(manager._interface))
            with count_profiled_calls(
                constructor_code,
                lambda value: value.__class__ is RelatedManager,
            ) as nested_constructors:
                resolved = manager.related
            self.assertEqual(nested_constructors.value, 1)
            self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_custom_interface_plan_dispatch(self):
        """Every virtual interface plan override keeps the baseline cast path."""

        def make_manager():
            class RelatedManager(GeneralManager):
                class Interface(CalculationInterface):
                    id = Input(str)

            class HydratedCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    related = Input(RelatedManager)

            GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
            GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
            return RelatedManager, HydratedCalculation

        def custom_init(self, *args, **kwargs):
            return InterfaceBase.__init__(self, *args, **kwargs)

        def custom_parser(self, *args, **kwargs):
            return InterfaceBase.parse_input_fields_to_identification(
                self,
                *args,
                **kwargs,
            )

        def custom_processor(
            self,
            name,
            input_field,
            value,
            identification,
            *,
            cache_context,
        ):
            return InterfaceBase._process_input_field(
                self,
                name,
                input_field,
                value,
                identification,
                cache_context=cache_context,
            )

        def custom_formatter(identification):
            return InterfaceBase.format_identification(identification)

        def custom_getattribute(self, name):
            return object.__getattribute__(self, name)

        def custom_getattr(self, name):
            raise AttributeError(name)

        def custom_setattr(self, name, value):
            object.__setattr__(self, name, value)

        mutations = (
            ("__init__", custom_init),
            ("parse_input_fields_to_identification", custom_parser),
            ("_process_input_field", custom_processor),
            ("format_identification", staticmethod(custom_formatter)),
            ("__getattribute__", custom_getattribute),
            ("__getattr__", custom_getattr),
            ("__setattr__", custom_setattr),
        )
        constructor_code = GeneralManager.__dict__["__init__"].__code__

        for name, replacement in mutations:
            RelatedManager, HydratedCalculation = make_manager()
            interface_class = HydratedCalculation.Interface
            class_state = type.__getattribute__(interface_class, "__dict__")
            missing = object()
            original = class_state.get(name, missing)
            type.__setattr__(interface_class, name, replacement)
            try:
                with self.subTest(dispatch=name):
                    manager = HydratedCalculation("related-id")
            finally:
                if original is missing:
                    type.__delattr__(interface_class, name)
                else:
                    type.__setattr__(interface_class, name, original)

            self.assertNotIn("_resolved_input_values", vars(manager._interface))
            with count_profiled_calls(
                constructor_code,
                lambda value, related=RelatedManager: value.__class__ is related,
            ) as nested_constructors:
                resolved = manager.related
            self.assertEqual(nested_constructors.value, 1)
            self.assertEqual(manager.identification, {"related": {"id": "related-id"}})
            self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_custom_interface_handle_and_metaclass(self):
        """Custom lifecycle and metaclass dispatch never opt into seeding."""

        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class CustomHandleInterface(CalculationInterface):
            related = Input(RelatedManager)

            @classmethod
            def handle_interface(cls):
                return super().handle_interface()

        class HandleCalculation(GeneralManager):
            Interface = CustomHandleInterface

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(HandleCalculation)
        manager = HandleCalculation("related-id")
        self.assertNotIn("_resolved_input_values", vars(manager._interface))

        class CustomInterfaceMeta(type(CalculationInterface)):
            def __call__(cls, *args, **kwargs):
                return super().__call__(*args, **kwargs)

        class MetaclassInterface(CalculationInterface, metaclass=CustomInterfaceMeta):
            related = Input(RelatedManager)

        class MetaclassCalculation(GeneralManager):
            Interface = MetaclassInterface

        GeneralManagerMeta.ensure_attributes_initialized(MetaclassCalculation)
        manager = MetaclassCalculation("related-id")
        self.assertNotIn("_resolved_input_values", vars(manager._interface))

        constructor_code = GeneralManager.__dict__["__init__"].__code__
        with count_profiled_calls(
            constructor_code,
            lambda value: value.__class__ is RelatedManager,
        ) as nested_constructors:
            self.assertEqual(manager.related.identification, {"id": "related-id"})
        self.assertEqual(nested_constructors.value, 1)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fails_closed_for_custom_calculation_handlers_and_state(self):
        """Custom capability classes, methods, and instance state stay virtual."""

        def make_manager(case):
            class RelatedManager(GeneralManager):
                class Interface(CalculationInterface):
                    id = Input(str)

            if case == "class":

                class CustomRead(CalculationReadCapability):
                    pass

                class CustomLifecycle(CalculationLifecycleCapability):
                    pass

            elif case == "method":

                class CustomRead(CalculationReadCapability):
                    def get_attributes(self, interface_cls):
                        return super().get_attributes(interface_cls)

                class CustomLifecycle(CalculationLifecycleCapability):
                    def post_create(self, **kwargs):
                        return super().post_create(**kwargs)

            else:
                CustomRead = CalculationReadCapability
                CustomLifecycle = CalculationLifecycleCapability

            class CustomInterface(CalculationInterface):
                related = Input(RelatedManager)

            class HydratedCalculation(GeneralManager):
                Interface = CustomInterface

            GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
            GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
            handlers = HydratedCalculation.Interface._capability_handlers
            if case in {"class", "method"}:
                handlers["read"] = CustomRead()
                handlers["calculation_lifecycle"] = CustomLifecycle()
            else:
                vars(handlers["read"])["unexpected_state"] = object()
            return RelatedManager, HydratedCalculation

        constructor_code = GeneralManager.__dict__["__init__"].__code__
        for case in ("class", "method", "state"):
            RelatedManager, HydratedCalculation = make_manager(case)
            manager = HydratedCalculation("related-id")

            with self.subTest(case=case):
                self.assertNotIn("_resolved_input_values", vars(manager._interface))
                with count_profiled_calls(
                    constructor_code,
                    lambda value, related=RelatedManager: value.__class__ is related,
                ) as nested_constructors:
                    resolved = manager.related
                self.assertEqual(nested_constructors.value, 1)
                self.assertEqual(
                    manager.identification,
                    {"related": {"id": "related-id"}},
                )
                self.assertEqual(resolved.identification, {"id": "related-id"})

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_seed_fallbacks_keep_baseline_cast_for_accessor_descriptor_and_input(self):
        """Plan substitutions and uninitialized state never change public casts."""

        def make_manager(input_factory=None):
            class RelatedManager(GeneralManager):
                class Interface(CalculationInterface):
                    id = Input(str)

            input_field = (
                Input(RelatedManager)
                if input_factory is None
                else input_factory(RelatedManager)
            )

            class HydratedCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    related = input_field or Input(RelatedManager)

            GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
            GeneralManagerMeta.ensure_attributes_initialized(HydratedCalculation)
            return RelatedManager, HydratedCalculation

        constructor_code = GeneralManager.__dict__["__init__"].__code__

        # Replacing the accessor with a behavior-preserving wrapper invalidates
        # provenance but must leave the normal Input.cast path intact.
        RelatedManager, HydratedCalculation = make_manager()
        original_accessor = HydratedCalculation._attributes["related"]

        def replacement_accessor(interface):
            return original_accessor(interface)

        HydratedCalculation._attributes["related"] = replacement_accessor
        try:
            manager = HydratedCalculation("related-id")
        finally:
            HydratedCalculation._attributes["related"] = original_accessor
        self.assertNotIn("_resolved_input_values", vars(manager._interface))
        with count_profiled_calls(
            constructor_code,
            lambda value: value.__class__ is RelatedManager,
        ) as nested_constructors:
            resolved = manager.related
        self.assertEqual(nested_constructors.value, 1)
        self.assertEqual(resolved.identification, {"id": "related-id"})

        # Mutating the installed descriptor marker has the same fail-closed
        # result, while restoring it preserves ordinary descriptor behavior.
        RelatedManager, HydratedCalculation = make_manager()
        descriptor = inspect.getattr_static(HydratedCalculation, "related")
        installation = descriptor._gm_manager_attribute_descriptor_installation
        descriptor._gm_manager_attribute_descriptor_installation = None
        try:
            manager = HydratedCalculation("related-id")
        finally:
            descriptor._gm_manager_attribute_descriptor_installation = installation
        self.assertNotIn("_resolved_input_values", vars(manager._interface))
        with count_profiled_calls(
            constructor_code,
            lambda value: value.__class__ is RelatedManager,
        ) as nested_constructors:
            resolved = manager.related
        self.assertEqual(nested_constructors.value, 1)
        self.assertEqual(resolved.identification, {"id": "related-id"})

        # An Input subclass is valid public behavior but is deliberately outside
        # the canonical seed plan.
        class CustomInput(Input):
            pass

        RelatedManager, HydratedCalculation = make_manager(CustomInput)
        manager = HydratedCalculation("related-id")
        self.assertNotIn("_resolved_input_values", vars(manager._interface))
        with count_profiled_calls(
            constructor_code,
            lambda value: value.__class__ is RelatedManager,
        ) as nested_constructors:
            resolved = manager.related
        self.assertEqual(nested_constructors.value, 1)
        self.assertEqual(resolved.identification, {"id": "related-id"})

        # Additional per-instance Input state is also outside the exact plan.
        RelatedManager, HydratedCalculation = make_manager()
        input_state = vars(HydratedCalculation.Interface.input_fields["related"])
        input_state["unexpected_state"] = object()
        try:
            manager = HydratedCalculation("related-id")
        finally:
            input_state.pop("unexpected_state", None)
        self.assertNotIn("_resolved_input_values", vars(manager._interface))
        with count_profiled_calls(
            constructor_code,
            lambda value: value.__class__ is RelatedManager,
        ) as nested_constructors:
            resolved = manager.related
        self.assertEqual(nested_constructors.value, 1)
        self.assertEqual(resolved.identification, {"id": "related-id"})

        # Missing class attributes remain a lazy initialization case and do not
        # permit construction-time seeding.
        RelatedManager, HydratedCalculation = make_manager()
        type.__delattr__(HydratedCalculation, "_attributes")
        type.__delattr__(HydratedCalculation, "_gm_attributes_initialized")
        manager = HydratedCalculation("related-id")
        self.assertNotIn("_resolved_input_values", vars(manager._interface))
        with count_profiled_calls(
            constructor_code,
            lambda value: value.__class__ is RelatedManager,
        ) as nested_constructors:
            resolved = manager.related
        self.assertEqual(nested_constructors.value, 1)
        self.assertEqual(resolved.identification, {"id": "related-id"})

    def test_filter(self):
        """
        Tests that the filter method returns a CalculationBucket linked to DummyGeneralManager.
        """
        bucket = DummyCalculationInterface.filter(field1="test")
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)

    def test_exclude(self):
        """
        Tests that the exclude method returns a CalculationBucket linked to DummyGeneralManager.
        """
        bucket = DummyCalculationInterface.exclude(field1="test")
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)

    def test_all(self):
        """
        Tests that the all() method returns a CalculationBucket linked to DummyGeneralManager.
        """
        bucket = DummyCalculationInterface.all()
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)

    def test_pre_create(self):
        """
        Tests that the _pre_create class method initializes attributes and interface metadata correctly.

        Verifies that the returned attributes dictionary contains the provided field values, the correct interface type, and a reference to the interface class. Also checks that the initialized interface is a subclass of DummyCalculationInterface.
        """
        pre, _post = DummyCalculationInterface.handle_interface()
        attr, initialized_interface, _ = pre(
            "test",
            {"field1": "value1", "field2": 42},
            DummyCalculationInterface,
        )
        self.assertTrue(issubclass(initialized_interface, DummyCalculationInterface))
        self.assertEqual(attr.get("field1"), "value1")
        self.assertEqual(attr.get("field2"), 42)
        self.assertEqual(attr["_interface_type"], "calculation")
        self.assertIsNotNone(attr.get("Interface"))
        self.assertTrue(issubclass(attr["Interface"], DummyCalculationInterface))

    def test_interface_type(self):
        """
        Tests that the `_interface_type` attribute is set to "calculation" on both the class and instance.
        """
        self.assertEqual(DummyCalculationInterface._interface_type, "calculation")
        self.assertEqual(self.interface._interface_type, "calculation")

    def test_parent_class(self):
        """
        Tests that the `_parent_class` attribute of `DummyCalculationInterface` and its instance is set to `DummyGeneralManager`.
        """
        self.assertEqual(DummyCalculationInterface._parent_class, DummyGeneralManager)
        self.assertEqual(self.interface._parent_class, DummyGeneralManager)

    def test_get_field_type(self):
        """
        Tests that get_field_type returns the correct type for defined fields and raises KeyError for unknown fields.
        """
        field_type = DummyCalculationInterface.get_field_type("field1")
        self.assertEqual(field_type, str)

        field_type = DummyCalculationInterface.get_field_type("field2")
        self.assertEqual(field_type, int)

        with self.assertRaises(KeyError):
            DummyCalculationInterface.get_field_type("non_existent_field")

    def test_configured_capability_override(self):
        """
        Tests that configured capabilities declared on the interface replace default handlers.
        """
        handler = CustomCalculationInterface.get_capability_handler("query")
        self.assertIsNotNone(handler)
        self.assertIsInstance(handler, CustomQueryCapability)
        self.assertEqual(handler.label, "custom")


class LifecycleInterface(CalculationInterface):
    foo = Input(type=str)
    bar = Input(type=int)


class TestCalculationLifecycleCapability(TestCase):
    def setUp(self):
        self.capability = CalculationLifecycleCapability()

    def test_pre_create_collects_input_fields(self):
        attrs = {"__module__": __name__}

        with patch(
            "general_manager.interface.capabilities.calculation.lifecycle.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            updated_attrs, interface_cls, _ = self.capability.pre_create(
                name="GeneratedManager",
                attrs=attrs,
                interface=LifecycleInterface,
            )

        self.assertIn("Interface", updated_attrs)
        self.assertEqual(updated_attrs["_interface_type"], "calculation")
        generated_interface = updated_attrs["Interface"]
        self.assertTrue(issubclass(generated_interface, LifecycleInterface))
        self.assertEqual(
            set(generated_interface.input_fields.keys()),
            {"foo", "bar"},
        )
        self.assertEqual(interface_cls, generated_interface)

    def test_post_create_sets_parent_class(self):
        temp_interface = type(
            "TempInterface",
            (LifecycleInterface,),
            {},
        )
        manager_cls = type("TempManager", (), {})

        with patch(
            "general_manager.interface.capabilities.calculation.lifecycle.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            self.capability.post_create(
                new_class=manager_cls,
                interface_class=temp_interface,
                model=None,
            )

        self.assertIs(temp_interface._parent_class, manager_cls)  # type: ignore[attr-defined]
