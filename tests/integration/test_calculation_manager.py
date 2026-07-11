# type: ignore

from typing import ClassVar
from unittest.mock import patch
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import CASCADE, CharField, ForeignKey, IntegerField, Value
from django.db.models.query import QuerySet
from django.db.models.sql.query import Query
from django.db.models.sql.where import WhereNode
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.crypto import get_random_string
from general_manager.bucket.calculation_bucket import (
    CalculationBucket,
    _DatabaseEnumerationEvidence,
    _database_enumeration_evidence,
    _database_source_signature,
)
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.measurement import MeasurementField, Measurement
from general_manager.manager.input import Input
from general_manager.interface.base_interface import InvalidInputValueError
from general_manager.api.property import graph_ql_property
from general_manager.cache.run_context import CalculationRunContext


class CustomMutationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Prepare test manager classes used across tests.

        Defines two inner manager classes on the test class:
        - Employee: a database-backed manager with `name` and `salary` fields (salary measured in EUR).
        - TaxCalculation: a manager that references an Employee and exposes a sortable GraphQL property `calculated_tax` that computes 20% of the referenced employee's salary.

        After definition, assigns `Employee`, `TaxCalculation`, and `general_manager_classes` to the test class for use in test methods.
        """

        class Employee(GeneralManager):
            id: int
            name: str
            salary: Measurement
            salary_rate_calls: ClassVar[int] = 0

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                salary = MeasurementField(base_unit="EUR")

            @graph_ql_property()
            def salary_rate(self) -> float:
                type(self).salary_rate_calls += 1
                return float(self.salary.quantity.magnitude / 100)

        class TaxCalculation(GeneralManager):
            employee: Employee

            class Interface(CalculationInterface):
                employee = Input(Employee, possible_values=lambda: Employee.all())

            @graph_ql_property(sortable=True)
            def calculated_tax(self) -> Measurement:
                """
                Calculates 20% of the associated employee's salary as tax.

                Returns:
                    Measurement: The calculated tax amount based on the employee's salary.
                """
                return self.employee.salary * 0.2

        class Bonus(GeneralManager):
            employee: Employee
            amount: int

            class Interface(DatabaseInterface):
                employee = ForeignKey(
                    "general_manager.Employee",
                    on_delete=CASCADE,
                )
                amount = IntegerField()

        class BonusCalculation(GeneralManager):
            employee: Employee

            class Interface(CalculationInterface):
                employee = Input(Employee, possible_values=lambda: Employee.all())

            @graph_ql_property
            def total_bonus(self) -> int:
                return sum(bonus.amount for bonus in self.employee.bonus_list)

        class RequestScopedCalculation(GeneralManager):
            employee: Employee
            computed_calls: ClassVar[int] = 0

            class Interface(CalculationInterface):
                employee = Input(Employee, possible_values=lambda: Employee.all())

            @graph_ql_property
            def computed_value(self) -> int:
                type(self).computed_calls += 1
                return int(self.employee.salary.quantity.magnitude)

        cls.Employee = Employee
        cls.TaxCalculation = TaxCalculation
        cls.Bonus = Bonus
        cls.BonusCalculation = BonusCalculation
        cls.RequestScopedCalculation = RequestScopedCalculation

        cls.general_manager_classes = [
            Employee,
            TaxCalculation,
            Bonus,
            BonusCalculation,
            RequestScopedCalculation,
        ]

    def setUp(self):
        """
        Set up a test user and the GraphQL query used by tests.

        Creates a test user and logs them in, then assigns:
        - self.user: the created user instance
        - self.mutation: GraphQL query string for retrieving a TaxCalculation's `calculatedTax` (value and unit)
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)
        self.mutation = """
        query($employeeId: ID!) {
            taxCalculation(employeeId: $employeeId) {
                calculatedTax {
                    value
                    unit
                }
            }
        }
        """

    def test_calculated_tax_tax(self):
        """
        Tests the tax calculation GraphQL mutation for an employee.

        Creates an employee with a specified salary, executes the tax calculation mutation, and verifies that the calculatedTaxd tax value and unit in the response are correct.
        """
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        variables = {"employeeId": employee.id}
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["taxCalculation"]
        self.assertEqual(data["calculatedTax"]["value"], 600)
        self.assertEqual(data["calculatedTax"]["unit"], "EUR")

    def test_group_by_manager_input(self):
        first_employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )
        second_employee = self.Employee.create(
            name="Bob",
            salary=Measurement(4000, "EUR"),
            creator_id=self.user.id,
        )

        grouped = self.TaxCalculation.all().group_by("employee")

        self.assertEqual(grouped.count(), 2)
        self.assertEqual(
            {group.employee.identification["id"] for group in grouped},
            {
                first_employee.identification["id"],
                second_employee.identification["id"],
            },
        )
        self.assertTrue(all(group._data.count() == 1 for group in grouped))
        self.assertEqual(
            grouped,
            self.TaxCalculation.all().group_by("employee"),
        )

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_static_database_input_batches_membership_with_exact_dependencies(self):
        for name in ("Alice", "Bob", "Carol"):
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
        source = self.Employee.all()

        class StaticEmployeeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        with (
            DependencyTracker() as dependencies,
            CaptureQueriesContext(connection) as queries,
        ):
            managers = list(CalculationBucket(StaticEmployeeCalculation))

        self.assertEqual(len(managers), 3)
        self.assertEqual(len(queries), 2)
        self.assertIn((self.Employee.__name__, "all", ""), dependencies)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_static_database_input_mutation_substitution_and_exposure_fall_back(self):
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]
        source = self.Employee.all()

        class GuardedEmployeeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        mutated = CalculationBucket(GuardedEmployeeCalculation)
        mutated_combinations = mutated._materialize_combinations(expose=False)
        mutated_combinations[0]["employee"].identification["id"] = 987_654_321
        with self.assertRaises(InvalidInputValueError):
            list(mutated)

        substituted = CalculationBucket(GuardedEmployeeCalculation)
        substituted._materialize_combinations(expose=False)
        GuardedEmployeeCalculation.Interface.input_fields[
            "employee"
        ].possible_values = self.Employee.filter(id=-1)
        with self.assertRaises(InvalidInputValueError):
            list(substituted)

        GuardedEmployeeCalculation.Interface.input_fields[
            "employee"
        ].possible_values = source
        exposed = CalculationBucket(GuardedEmployeeCalculation)
        self.assertEqual(len(exposed.generate_combinations()), 2)
        self.Employee.Interface._model.objects.filter(
            pk=employees[-1].identification["id"]
        ).delete()
        with self.assertRaises(InvalidInputValueError):
            list(exposed)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_database_dependency_metadata_mutation_revokes_batch_trust(self):
        for name in ("Alice", "Bob"):
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
        mutations = {
            "filters": lambda source: source.filters.__setitem__(
                "name", ["changed-after-enumeration"]
            ),
            "excludes": lambda source: source.excludes.__setitem__(
                "name", ["changed-after-enumeration"]
            ),
            "sort_keys": lambda source: setattr(source, "_sort_keys", ("name",)),
            "sort_reverse": lambda source: setattr(source, "_sort_reverse", True),
            "cacheability": lambda source: setattr(
                source, "_run_scoped_cacheable", False
            ),
            "trusted_signature": lambda source: setattr(
                source, "_trusted_query_signature", ("changed",)
            ),
            "query_signature_cache": lambda source: setattr(
                source, "_query_signature_cache", None
            ),
            "search_date": lambda source: setattr(
                source, "_search_date", date(2024, 1, 1)
            ),
        }

        for mutation_name, mutate in mutations.items():
            with self.subTest(mutation_name=mutation_name):
                source = self.Employee.all()

                class MetadataMutationCalculation(GeneralManager):
                    class Interface(CalculationInterface):
                        employee = Input(self.Employee, possible_values=source)

                bucket = CalculationBucket(MetadataMutationCalculation)
                self.assertEqual(len(bucket._materialize_combinations(expose=False)), 2)
                mutate(source)

                with (
                    DependencyTracker() as dependencies,
                    CaptureQueriesContext(connection) as queries,
                ):
                    managers = list(bucket)

                self.assertEqual(len(managers), 2)
                self.assertEqual(len(queries), 2)
                if mutation_name == "filters":
                    self.assertIn(
                        (
                            self.Employee.__name__,
                            "filter",
                            serialize_dependency_identifier(
                                {"name": "changed-after-enumeration"}
                            ),
                        ),
                        dependencies,
                    )

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_hostile_database_state_is_ineligible_without_attribute_hooks(self):
        employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )

        hostile_error = AssertionError("hostile __class__ hook ran")

        class HostileStateValue:
            hook_calls = 0

            def __getattribute__(own, name):
                if name == "__class__":
                    type(own).hook_calls += 1
                    raise hostile_error
                return object.__getattribute__(own, name)

        source = self.Employee.all()

        class HostileStateCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        bucket = CalculationBucket(HostileStateCalculation)
        combinations = bucket._materialize_combinations(expose=False)
        input_field = HostileStateCalculation.Interface.input_fields["employee"]
        evidence = bucket._lookup_combination_evidence(combinations[0])["employee"]
        self.assertIsInstance(evidence, _DatabaseEnumerationEvidence)
        database_evidence = evidence
        database_evidence.authorized_tokens = frozenset(
            {database_evidence.primary_key_token}
        )
        source.filters["hostile"] = [HostileStateValue()]

        self.assertFalse(database_evidence.authorizes(input_field, employee, {}))
        self.assertEqual(HostileStateValue.hook_calls, 0)

        creation_source = self.Employee.all()
        creation_source.excludes["hostile"] = [HostileStateValue()]
        creation_input = Input(self.Employee, possible_values=creation_source)
        self.assertIsNone(
            _database_enumeration_evidence(
                creation_input,
                creation_source,
                employee,
            )
        )
        self.assertEqual(HostileStateValue.hook_calls, 0)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_static_filtered_database_source_keeps_two_query_batch_path(self):
        for name in ("Alice", "Bob"):
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
        source = self.Employee.filter(name="Alice")

        class FilteredSourceCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        with CaptureQueriesContext(connection) as queries:
            managers = list(CalculationBucket(FilteredSourceCalculation))

        self.assertEqual(len(managers), 1)
        self.assertEqual(len(queries), 2)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_database_batch_dependency_replay_is_exact_per_next_and_nested(self):
        for name in ("Alice", "Bob"):
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
        source = self.Employee.all()

        class DependencyReplayCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        bucket = CalculationBucket(DependencyReplayCalculation)
        bucket._materialize_combinations(expose=False)
        iterator = iter(bucket)
        with DependencyTracker() as outer_dependencies:
            with DependencyTracker() as first_dependencies:
                next(iterator)
        with DependencyTracker() as second_dependencies:
            next(iterator)
        iterator.close()

        expected_source_dependencies = {(self.Employee.__name__, "all", "")}
        for dependencies in (
            outer_dependencies,
            first_dependencies,
            second_dependencies,
        ):
            source_dependencies = {
                dependency
                for dependency in dependencies
                if dependency[0] == self.Employee.__name__
            }
            self.assertEqual(source_dependencies, expected_source_dependencies)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_database_source_mutation_between_yields_rechecks_live_signature(self):
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]
        mutations = {
            "filters": lambda source: source.filters.__setitem__(
                "name", ["changed-between-yields"]
            ),
            "excludes": lambda source: source.excludes.__setitem__(
                "name", ["changed-between-yields"]
            ),
            "query": lambda source: setattr(
                source,
                "_data",
                source._data.filter(pk=employees[-1].identification["id"]),
            ),
            "search_date": lambda source: setattr(
                source, "_search_date", date(2024, 1, 1)
            ),
        }

        for mutation_name, mutate in mutations.items():
            with self.subTest(mutation_name=mutation_name):
                source = self.Employee.all().sort("name")

                class BetweenYieldMutationCalculation(GeneralManager):
                    class Interface(CalculationInterface):
                        employee = Input(self.Employee, possible_values=source)

                bucket = CalculationBucket(BetweenYieldMutationCalculation)
                bucket._materialize_combinations(expose=False)
                iterator = iter(bucket)
                next(iterator)
                mutate(source)

                with (
                    DependencyTracker() as dependencies,
                    CaptureQueriesContext(connection) as queries,
                ):
                    second = next(iterator)
                iterator.close()

                self.assertEqual(
                    second.identification["employee"]["id"],
                    employees[-1].identification["id"],
                )
                self.assertEqual(len(queries), 1)
                source_dependencies = {
                    dependency
                    for dependency in dependencies
                    if dependency[0] == self.Employee.__name__
                }
                self.assertTrue(source_dependencies)
                self.assertFalse(
                    any(
                        dependency[1] == "identification"
                        for dependency in source_dependencies
                    )
                )

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_in_place_database_query_mutation_between_yields_falls_back(self):
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]

        def mutate_rhs(source):
            source._data.query.where.children[0].rhs = [
                employees[-1].identification["id"]
            ]

        def mutate_limit(source):
            source._data.query.set_limits(high=1)

        def mutate_distinct(source):
            source._data.query.distinct = True

        def mutate_annotations(source):
            source._data.query.annotations["marker"] = Value(1)

        mutations = {
            "rhs": (mutate_rhs, None, 1),
            "limits": (mutate_limit, TypeError, 0),
            "distinct": (mutate_distinct, None, 1),
            "annotations": (mutate_annotations, None, 1),
        }
        employee_ids = [employee.identification["id"] for employee in employees]

        for mutation_name, (
            mutate,
            expected_error,
            expected_queries,
        ) in mutations.items():
            with self.subTest(mutation_name=mutation_name):
                source = self.Employee.filter(id__in=employee_ids).sort("name")

                class InPlaceQueryMutationCalculation(GeneralManager):
                    class Interface(CalculationInterface):
                        employee = Input(self.Employee, possible_values=source)

                bucket = CalculationBucket(InPlaceQueryMutationCalculation)
                bucket._materialize_combinations(expose=False)
                iterator = iter(bucket)
                next(iterator)
                mutate(source)

                with CaptureQueriesContext(connection) as queries:
                    if expected_error is not None:
                        with self.assertRaises(expected_error):
                            next(iterator)
                    else:
                        second = next(iterator)
                        self.assertEqual(
                            second.identification["employee"]["id"],
                            employees[-1].identification["id"],
                        )
                iterator.close()
                self.assertEqual(len(queries), expected_queries)

    def test_custom_queryset_query_and_where_are_rejected_without_hooks(self):
        source = self.Employee.all()
        canonical_queryset = source._data
        canonical_query = canonical_queryset.query

        hostile_error = AssertionError("hostile Django state hook ran")

        class HostileMixin:
            hook_calls = 0
            active = False

            def __getattribute__(own, name):
                if type(own).active and name not in {"active", "hook_calls"}:
                    type(own).hook_calls += 1
                    raise hostile_error
                return object.__getattribute__(own, name)

        class HostileQuerySet(HostileMixin, QuerySet):
            pass

        class HostileQuery(HostileMixin, Query):
            pass

        class HostileWhere(HostileMixin, WhereNode):
            pass

        hostile_queryset = HostileQuerySet(
            model=canonical_queryset.model,
            query=canonical_query.clone(),
            using=canonical_queryset.db,
            hints={},
        )
        hostile_query = HostileQuery(canonical_query.model)
        hostile_where = HostileWhere()

        cases = (
            ("queryset", hostile_queryset, HostileQuerySet),
            ("query", canonical_queryset._clone(), HostileQuery),
            ("where", canonical_queryset._clone(), HostileWhere),
        )
        cases[1][1]._query = hostile_query
        cases[2][1]._query.where = hostile_where
        for case_name, queryset, hostile_type in cases:
            with self.subTest(case_name=case_name):
                hostile_type.active = True
                source._data = queryset
                self.assertIsNone(_database_source_signature(source))
                self.assertEqual(hostile_type.hook_calls, 0)
                hostile_type.active = False

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_database_row_authorization_is_a_whole_pass_key_snapshot(self):
        """Rows proven at pass start stay authorized without per-yield existence SQL."""
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]
        source = self.Employee.all().sort("name")

        class WholePassSnapshotCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        bucket = CalculationBucket(WholePassSnapshotCalculation)
        bucket._materialize_combinations(expose=False)
        iterator = iter(bucket)
        next(iterator)
        self.Employee.Interface._model.objects.filter(
            pk=employees[-1].identification["id"]
        ).delete()

        with CaptureQueriesContext(connection) as queries:
            second = next(iterator)
        iterator.close()

        self.assertEqual(
            second.identification["employee"]["id"],
            employees[-1].identification["id"],
        )
        self.assertEqual(len(queries), 0)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_custom_calculation_construction_paths_do_not_use_database_trust(self):
        for name in ("Alice", "Bob"):
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )

        class CustomInput(Input):
            pass

        def custom_manager_class(source):
            class CustomManagerCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    employee = Input(self.Employee, possible_values=source)

                def __init__(own, *args, **kwargs):
                    super().__init__(*args, **kwargs)

            return CustomManagerCalculation

        def custom_interface_class(source):
            class CustomInterfaceCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    employee = Input(self.Employee, possible_values=source)

                    def _process_input_field(own, *args, **kwargs):
                        return super()._process_input_field(*args, **kwargs)

            return CustomInterfaceCalculation

        def custom_input_class(source):
            class CustomInputCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    employee = CustomInput(self.Employee, possible_values=source)

            return CustomInputCalculation

        def custom_tracking_class(source):
            class CustomTrackingCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    employee = Input(self.Employee, possible_values=source)

                @classmethod
                def _track_identification_dependency_active(own, identification):
                    return super()._track_identification_dependency_active(
                        identification
                    )

            return CustomTrackingCalculation

        factories = (
            custom_manager_class,
            custom_interface_class,
            custom_input_class,
            custom_tracking_class,
        )
        for factory in factories:
            with self.subTest(factory=factory.__name__):
                source = self.Employee.all()
                manager_class = factory(source)
                with CaptureQueriesContext(connection) as queries:
                    managers = list(CalculationBucket(manager_class))
                self.assertEqual(len(managers), 2)
                self.assertEqual(len(queries), 3)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_database_candidate_identity_mutations_use_public_validation(self):
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]

        def prepared_bucket():
            source = self.Employee.all()

            class CandidateMutationCalculation(GeneralManager):
                class Interface(CalculationInterface):
                    employee = Input(self.Employee, possible_values=source)

            bucket = CalculationBucket(CandidateMutationCalculation)
            combinations = bucket._materialize_combinations(expose=False)
            return bucket, combinations

        valid_mutations = {
            "replaced_identification": lambda combinations: object.__setattr__(
                combinations[0]["employee"],
                "_GeneralManager__id",
                {"id": employees[0].identification["id"]},
            ),
            "changed_id": lambda combinations: combinations[0][
                "employee"
            ].identification.__setitem__("id", employees[1].identification["id"]),
            "substituted_manager": lambda combinations: combinations[0].__setitem__(
                "employee", employees[1]
            ),
        }
        for mutation_name, mutate in valid_mutations.items():
            with self.subTest(mutation_name=mutation_name):
                bucket, combinations = prepared_bucket()
                mutate(combinations)
                with CaptureQueriesContext(connection) as queries:
                    managers = list(bucket)
                self.assertEqual(len(managers), 2)
                self.assertEqual(len(queries), 2)

        bucket, combinations = prepared_bucket()
        del combinations[0]["employee"].identification["id"]
        with self.assertRaises(InvalidInputValueError):
            list(bucket)

        bucket, combinations = prepared_bucket()
        combinations[0]["employee"] = object()
        with self.assertRaises(TypeError):
            list(bucket)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_callable_and_validator_database_inputs_keep_live_fallbacks(self):
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]
        source = self.Employee.all()
        callback_calls = 0

        def possible_values():
            nonlocal callback_calls
            callback_calls += 1
            return source

        class CallbackEmployeeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=possible_values)

        self.assertEqual(len(list(CalculationBucket(CallbackEmployeeCalculation))), 2)
        self.assertEqual(callback_calls, 3)

        validator_calls = 0

        def validator(_value):
            nonlocal validator_calls
            validator_calls += 1
            if validator_calls == 1:
                self.Employee.Interface._model.objects.filter(
                    pk=employees[-1].identification["id"]
                ).delete()
            return True

        class ValidatedEmployeeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(
                    self.Employee,
                    possible_values=source,
                    validator=validator,
                )

        with self.assertRaises(InvalidInputValueError):
            list(CalculationBucket(ValidatedEmployeeCalculation))
        self.assertEqual(validator_calls, 2)

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_dependent_callable_database_providers_disable_whole_pass_trust(self):
        employees = [
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
            for name in ("Alice", "Bob")
        ]
        employee_ids = [employee.identification["id"] for employee in employees]

        for provider_kind, dependency_mode in (
            ("fresh", "stable"),
            ("fresh", "changing"),
            ("same", "stable"),
            ("same", "changing"),
            ("mutating", "stable"),
            ("mutating", "changing"),
            ("raising", "stable"),
            ("raising", "changing"),
        ):
            with self.subTest(
                provider_kind=provider_kind,
                dependency_mode=dependency_mode,
            ):
                source = self.Employee.all()
                calls = []
                returned_sources = []
                mutation_events = []
                provider_error = RuntimeError("database provider failed")
                changing = dependency_mode == "changing"
                generation_calls = 2 if changing else 1
                region_values = ["EU", "US"] if changing else ["EU"]
                segment_values = ["all"] if changing else ["retail", "enterprise"]

                def possible_employees(
                    region,
                    *,
                    _calls=calls,
                    _provider_kind=provider_kind,
                    _source=source,
                    _provider_error=provider_error,
                    _returned_sources=returned_sources,
                    _mutation_events=mutation_events,
                    _generation_calls=generation_calls,
                ):
                    _calls.append(region)
                    if _provider_kind == "raising" and len(_calls) == 2:
                        raise _provider_error
                    if _provider_kind == "mutating" and len(_calls) > _generation_calls:
                        result = _source.none()
                        _mutation_events.append((region, "empty"))
                    elif _provider_kind == "fresh":
                        result = self.Employee.all()
                    else:
                        result = _source
                    _returned_sources.append(result)
                    return result

                class DependentDatabaseCalculation(GeneralManager):
                    class Interface(CalculationInterface):
                        region = Input(str, possible_values=region_values)
                        segment = Input(str, possible_values=segment_values)
                        employee = Input(
                            self.Employee,
                            possible_values=possible_employees,
                            depends_on=["region"],
                        )

                bucket = CalculationBucket(DependentDatabaseCalculation)
                if provider_kind == "raising" and changing:
                    with self.assertRaisesRegex(
                        RuntimeError, "database provider failed"
                    ):
                        bucket._materialize_combinations(expose=False)
                    self.assertEqual(calls, ["EU", "US"])
                    self.assertEqual(returned_sources, [source])
                    self.assertIsNone(bucket._data)
                else:
                    combinations = bucket._materialize_combinations(expose=False)
                    self.assertEqual(len(combinations), 4)
                    self.assertTrue(bucket._combination_evidence)
                    self.assertTrue(
                        all(
                            "employee" not in evidence
                            for _combination, evidence in bucket._combination_evidence.values()
                        )
                    )
                    if provider_kind == "mutating":
                        with self.assertRaises(InvalidInputValueError):
                            list(bucket)
                        expected_calls = (
                            ["EU", "US", "EU"] if changing else ["EU", "EU"]
                        )
                        self.assertEqual(calls, expected_calls)
                        self.assertTrue(
                            all(result is source for result in returned_sources[:-1])
                        )
                        self.assertIsNot(returned_sources[-1], source)
                        self.assertEqual(mutation_events, [("EU", "empty")])
                    elif provider_kind == "raising":
                        with self.assertRaisesRegex(
                            RuntimeError, "database provider failed"
                        ):
                            list(bucket)
                        self.assertEqual(calls, ["EU", "EU"])
                        self.assertEqual(returned_sources, [source])
                        self.assertEqual(
                            combinations,
                            [
                                {
                                    "region": "EU",
                                    "segment": segment,
                                    "employee": employee,
                                }
                                for segment in ("retail", "enterprise")
                                for employee in employees
                            ],
                        )
                    else:
                        managers = list(bucket)
                        self.assertEqual(len(managers), 4)
                        expected_calls = (
                            ["EU", "US", "EU", "EU", "US", "US"]
                            if changing
                            else ["EU"] * 5
                        )
                        self.assertEqual(calls, expected_calls)
                        expected_results = (
                            [
                                (region, "all", employee_id)
                                for region in ("EU", "US")
                                for employee_id in employee_ids
                            ]
                            if changing
                            else [
                                ("EU", segment, employee_id)
                                for segment in ("retail", "enterprise")
                                for employee_id in employee_ids
                            ]
                        )
                        self.assertEqual(
                            [
                                (
                                    manager.identification["region"],
                                    manager.identification["segment"],
                                    manager.identification["employee"]["id"],
                                )
                                for manager in managers
                            ],
                            expected_results,
                        )
                        if provider_kind == "fresh":
                            self.assertEqual(
                                len({id(result) for result in returned_sources}),
                                len(returned_sources),
                            )
                        else:
                            self.assertTrue(
                                all(result is source for result in returned_sources)
                            )
                self.assertEqual(bucket._combination_evidence, {})

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_database_preview_falls_back_and_preparation_errors_clear_evidence(self):
        for name in ("Alice", "Bob"):
            self.Employee.create(
                name=name,
                salary=Measurement(3000, "EUR"),
                creator_id=self.user.id,
            )
        source = self.Employee.all()

        class PreviewEmployeeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(self.Employee, possible_values=source)

        with patch.object(
            source,
            "_contains_all_primary_keys",
            wraps=source._contains_all_primary_keys,
        ) as batch_contains:
            str(CalculationBucket(PreviewEmployeeCalculation))
        batch_contains.assert_not_called()

        bucket = CalculationBucket(PreviewEmployeeCalculation)
        bucket._materialize_combinations(expose=False)
        batch_error = RuntimeError("batch membership failed")

        def fail_batch_query(_execute, _sql, _params, _many, _context):
            raise batch_error

        with connection.execute_wrapper(fail_batch_query):
            with self.assertRaisesRegex(RuntimeError, "batch membership failed"):
                next(iter(bucket))
        self.assertEqual(bucket._combination_evidence, {})

    def test_manager_inputs_are_cached_only_within_the_calculation_instance(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        calculation = self.TaxCalculation(employee=employee)

        cached_employee = calculation.employee

        employee.update(
            salary=Measurement(4000, "EUR"),
            creator_id=self.user.id,
            ignore_permission=True,
        )

        with DependencyTracker() as dependencies:
            repeated_employee = calculation.employee

        self.assertIs(repeated_employee, cached_employee)
        self.assertEqual(
            dependencies,
            {
                (
                    self.Employee.__name__,
                    "identification",
                    serialize_dependency_identifier(cached_employee.identification),
                )
            },
        )

        later_calculation = self.TaxCalculation(employee=employee)
        self.assertEqual(later_calculation.employee.salary, Measurement(4000, "EUR"))
        self.assertIsNot(later_calculation.employee, cached_employee)

    def test_entry_based_graphql_property_refreshes_after_update(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        query = """
        query($id: ID!) {
            employee(id: $id) {
                salaryRate
            }
        }
        """

        response = self.query(query, variables={"id": employee.id})
        self.assertResponseNoErrors(response)
        self.assertEqual(response.json()["data"]["employee"]["salaryRate"], 30)

        employee.update(
            salary=Measurement(4000, "EUR"),
            creator_id=self.user.id,
            ignore_permission=True,
        )

        response = self.query(query, variables={"id": employee.id})
        self.assertResponseNoErrors(response)
        self.assertEqual(response.json()["data"]["employee"]["salaryRate"], 40)

    def test_database_graphql_property_defaults_to_run_scope(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        query = """
        query($id: ID!) {
            employeeA: employee(id: $id) {
                salaryRate
            }
            employeeB: employee(id: $id) {
                salaryRate
            }
        }
        """

        self.Employee.salary_rate_calls = 0

        response = self.query(query, variables={"id": employee.id})
        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        self.assertEqual(data["employeeA"]["salaryRate"], 30)
        self.assertEqual(data["employeeB"]["salaryRate"], 30)
        self.assertEqual(self.Employee.salary_rate_calls, 1)

        response = self.query(query, variables={"id": employee.id})
        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        self.assertEqual(data["employeeA"]["salaryRate"], 30)
        self.assertEqual(data["employeeB"]["salaryRate"], 30)
        self.assertEqual(self.Employee.salary_rate_calls, 2)

    def test_calculation_graphql_property_refreshes_after_entry_update(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        response = self.query(self.mutation, variables={"employeeId": employee.id})
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["taxCalculation"]
        self.assertEqual(data["calculatedTax"]["value"], 600)

        employee.update(
            salary=Measurement(4000, "EUR"),
            creator_id=self.user.id,
            ignore_permission=True,
        )

        response = self.query(self.mutation, variables={"employeeId": employee.id})
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["taxCalculation"]
        self.assertEqual(data["calculatedTax"]["value"], 800)

    def test_calculation_graphql_property_defaults_to_run_scope(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        calls = 0

        class RunScopedCalculation(GeneralManager):
            employee: self.Employee

            class Interface(CalculationInterface):
                employee = Input(
                    self.Employee, possible_values=lambda: self.Employee.all()
                )

            @graph_ql_property
            def computed_value(self) -> int:
                nonlocal calls
                calls += 1
                return int(self.employee.salary.quantity.magnitude)

        with CalculationRunContext():
            first = RunScopedCalculation(employee=employee)
            second = RunScopedCalculation(employee=employee)
            self.assertEqual(first.computed_value, 3000)
            self.assertEqual(second.computed_value, 3000)

        self.assertEqual(calls, 1)

        third = RunScopedCalculation(employee=employee)
        fourth = RunScopedCalculation(employee=employee)
        self.assertEqual(third.computed_value, 3000)
        self.assertEqual(fourth.computed_value, 3000)
        self.assertEqual(calls, 3)

    def test_calculation_graphql_property_can_opt_into_dependency_cache(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        calls = 0

        class DependencyCachedCalculation(GeneralManager):
            employee: self.Employee

            class Interface(CalculationInterface):
                employee = Input(
                    self.Employee, possible_values=lambda: self.Employee.all()
                )

            @graph_ql_property(cache="dependency")
            def computed_value(self) -> int:
                nonlocal calls
                calls += 1
                return int(self.employee.salary.quantity.magnitude)

        first = DependencyCachedCalculation(employee=employee)
        second = DependencyCachedCalculation(employee=employee)
        self.assertEqual(first.computed_value, 3000)
        self.assertEqual(second.computed_value, 3000)
        self.assertEqual(calls, 1)

        employee.update(
            salary=Measurement(4000, "EUR"),
            creator_id=self.user.id,
            ignore_permission=True,
        )

        refreshed = DependencyCachedCalculation(employee=employee)
        self.assertEqual(refreshed.computed_value, 4000)
        self.assertEqual(calls, 2)

    def test_calculation_graphql_property_can_disable_caching(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        calls = 0

        class UncachedCalculation(GeneralManager):
            employee: self.Employee

            class Interface(CalculationInterface):
                employee = Input(
                    self.Employee, possible_values=lambda: self.Employee.all()
                )

            @graph_ql_property(cache="none")
            def computed_value(self) -> int:
                nonlocal calls
                calls += 1
                return int(self.employee.salary.quantity.magnitude)

        with CalculationRunContext():
            first = UncachedCalculation(employee=employee)
            second = UncachedCalculation(employee=employee)
            self.assertEqual(first.computed_value, 3000)
            self.assertEqual(second.computed_value, 3000)

        self.assertEqual(calls, 2)

    def test_graphql_request_shares_run_cache_across_repeated_calculation_fields(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.RequestScopedCalculation.computed_calls = 0

        query = """
        query($employeeId: ID!) {
            first: requestScopedCalculation(employeeId: $employeeId) {
                computedValue
            }
            second: requestScopedCalculation(employeeId: $employeeId) {
                computedValue
            }
        }
        """

        response = self.query(query, variables={"employeeId": employee.id})

        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        self.assertEqual(data["first"]["computedValue"], 3000)
        self.assertEqual(data["second"]["computedValue"], 3000)
        self.assertEqual(self.RequestScopedCalculation.computed_calls, 1)

    def test_calculation_property_can_traverse_database_reverse_relation(self):
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Bonus.create(
            employee=employee,
            amount=100,
            creator_id=self.user.id,
            ignore_permission=True,
        )
        self.Bonus.create(
            employee=employee,
            amount=250,
            creator_id=self.user.id,
            ignore_permission=True,
        )

        calculation = self.BonusCalculation(employee=employee)

        self.assertEqual(calculation.total_bonus, 350)

    def test_sort_by_calculation_property(self):
        """
        Verify TaxCalculation entries can be ordered by the `calculated_tax` property.

        Asserts the initial retrieval order is by employee name, then checks that sorting by
        `calculated_tax` yields employees ordered by their salary-derived tax (ascending),
        and that sorting with `reverse=True` yields the reverse order.
        """
        self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket = self.TaxCalculation.all()
        self.assertEqual(len(tax_calculation_bucket), 3)
        self.assertEqual(tax_calculation_bucket[0].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket[1].employee.name, "Bob")
        self.assertEqual(tax_calculation_bucket[2].employee.name, "Tim")

        tax_calculation_bucket_sorted = tax_calculation_bucket.sort("calculated_tax")
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Bob")

        tax_calculation_bucket_sorted = tax_calculation_bucket.sort(
            "calculated_tax", reverse=True
        )
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Bob")

    def test_sort_by_calculation_property_and_name(self):
        """
        Verifies that TaxCalculation entries are ordered by `calculated_tax` and then by `employee.name`, both in ascending order.

        Creates employees with different salaries, sorts the TaxCalculation bucket by the tuple ("calculated_tax", "employee.name") ascending, and asserts the resulting employee name order is: "Tim", "Alice", "Tina", "Bob".
        """
        self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tina", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket_sorted = self.TaxCalculation.all().sort(
            ("calculated_tax", "employee.name"), reverse=False
        )
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Tina")
        self.assertEqual(tax_calculation_bucket_sorted[3].employee.name, "Bob")

    def test_filter_by_calculation_property(self):
        """
        Verifies that TaxCalculation entries can be filtered by employee name prefix and by calculated_tax, and that combined filters produce the expected subsets and ordering.

        Checks:
        - Filtering by employee name prefix "T" yields two entries ordered by employee name: Tim, Tina.
        - Further filtering that subset by calculated_tax == 3000 EUR * 0.2 yields a single entry (Tina).
        - Filtering all TaxCalculation entries by calculated_tax == 3000 EUR * 0.2 yields two entries ordered: Alice, Tina.
        - Applying the calculated_tax filter to the name-prefixed subset produces the same result as filtering the subset directly.
        - The filtered-by-both bucket is not equal to the original name-prefixed bucket.
        """
        self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tina", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket_filtered1 = self.TaxCalculation.filter(
            employee__name__startswith="T"
        )
        self.assertEqual(len(tax_calculation_bucket_filtered1), 2)
        self.assertEqual(tax_calculation_bucket_filtered1[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_filtered1[1].employee.name, "Tina")

        tim = tax_calculation_bucket_filtered1[0].employee
        tax_calculation_by_id = self.TaxCalculation.filter(employee_id=tim.id)
        self.assertEqual(len(tax_calculation_by_id), 1)
        self.assertEqual(tax_calculation_by_id[0].employee.name, "Tim")

        tax_calculation_bucket_filtered2 = tax_calculation_bucket_filtered1.filter(
            calculated_tax=Measurement(3000, "EUR") * 0.2
        )
        self.assertEqual(len(tax_calculation_bucket_filtered2), 1)
        self.assertEqual(tax_calculation_bucket_filtered2[0].employee.name, "Tina")

        tax_calculation_bucket_filtered3 = self.TaxCalculation.filter(
            calculated_tax=Measurement(3000, "EUR") * 0.2
        )
        self.assertEqual(len(tax_calculation_bucket_filtered3), 2)
        self.assertEqual(tax_calculation_bucket_filtered3[0].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_filtered3[1].employee.name, "Tina")

        tax_calculation_bucket_filtered4 = tax_calculation_bucket_filtered1.filter(
            calculated_tax=Measurement(3000, "EUR") * 0.2
        )

        self.assertEqual(
            tax_calculation_bucket_filtered4, tax_calculation_bucket_filtered2
        )

        self.assertNotEqual(
            tax_calculation_bucket_filtered4, tax_calculation_bucket_filtered1
        )
