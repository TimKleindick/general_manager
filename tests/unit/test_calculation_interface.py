import gc
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
)
from general_manager.interface.capabilities.calculation.lifecycle import (
    _is_canonical_calculation_input_accessor,
)
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.manager.input import Input
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.cache.cache_tracker import DependencyTracker


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

        def make_replacement():
            first = None
            second = None

            def replacement(interface, field_name):
                if first is second:
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
        recursive_cell = next(
            cell for cell in resolver.__closure__ if cell.cell_contents is resolver
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
