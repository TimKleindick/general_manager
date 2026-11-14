"""Calculation query plans share passive metadata, never permission decisions."""

from __future__ import annotations

import sys
from calendar import monthrange
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import date
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from graphql import GraphQLError

from general_manager.api import graphql_query_permissions as query_permissions
from general_manager.api import graphql_resolvers
from general_manager.interface import CalculationInterface
from general_manager.interface.capabilities.calculation.lifecycle import (
    CalculationReadCapability,
)
from general_manager.manager import GeneralManager, Input
from general_manager.manager.meta import AttributeEvaluationError
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.manager_based_permission import (
    additional_local_read_permission,
)
from general_manager.permission.permission_checks import permission_functions
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@contextmanager
def calculation_metadata_calls():
    """Count real metadata entries without replacing their attested executables."""
    codes = {
        CalculationReadCapability.get_attribute_types.__code__: "types",
        CalculationReadCapability.get_attributes.__code__: "accessors",
        additional_local_read_permission.__code__: "accounting",
    }
    counts = Counter()
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code in codes:
            counts[codes[frame.f_code]] += 1
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(observe)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


class TestGraphQLCalculationQueryStructure(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class QueryProofSubject(GeneralManager):
            class Interface(CalculationInterface):
                code = Input(int, possible_values=range(10))
                exact = Input(int, possible_values=range(10))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class QueryProofCalculation(GeneralManager):
            class Interface(CalculationInterface):
                axis = Input(int, possible_values=range(10))
                day = Input(date, possible_values=[date(2025, 1, 1)])
                subject = Input(QueryProofSubject)
                subject_id = Input(int, possible_values=range(10))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.Subject = QueryProofSubject
        cls.Calculation = QueryProofCalculation
        cls.general_manager_classes = [QueryProofSubject, QueryProofCalculation]
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.info = SimpleNamespace(context=SimpleNamespace(user=AnonymousUser()))
        self.subject = self.Subject(code=7, exact=2)
        self.record = self.make_record()

    def make_record(self, axis=1):
        return self.Calculation(
            axis=axis,
            day=date(2025, 1, 1),
            subject=self.subject,
            subject_id=7,
        )

    def authorize(self, candidates, paths, *, prepared=True, **kwargs):
        query_permissions.authorize_query_fields(
            candidates,
            self.info,
            paths,
            budget_additional_permissions_only=True,
            prepare_native_structure=prepared,
            **kwargs,
        )

    @contextmanager
    def permission_callback(self, callback):
        with (
            patch.dict(
                permission_functions,
                {
                    "calculationProof": {
                        "permission_method": callback,
                        "permission_filter": lambda *_: None,
                    }
                },
            ),
            patch.object(self.Calculation.Permission, "__read__", ["calculationProof"]),
        ):
            yield

    def test_scalar_and_terminal_manager_metadata_work_is_bounded(self):
        """Amortizing schema work must retain every real field callback in order."""
        callbacks = []
        fields = []
        original = graphql_resolvers.check_read_permission

        def callback(instance, _user, _config):
            callbacks.append(instance)
            return True

        def checked(instance, info, field, **kwargs):
            fields.append(field)
            return original(instance, info, field, **kwargs)

        observations = []
        with (
            self.permission_callback(callback),
            patch.object(graphql_resolvers, "check_read_permission", checked),
        ):
            for size in (2, 64):
                with calculation_metadata_calls() as counts:
                    self.authorize(
                        [self.record] * size,
                        ["subject", "day__lte", "axis", "day__gte"],
                    )
                observations.append(counts)

        assert fields == ["axis", "day", "day", "subject"] * 66
        assert len(callbacks) == 264
        assert all(instance is self.record for instance in callbacks)
        for kind in ("types", "accessors", "accounting"):
            assert observations[0][kind] > 0
            assert observations[1][kind] <= observations[0][kind], observations

    def test_third_permission_callback_denial_and_exception_remain_live(self):
        """A populated structure must never reuse a previous actor/object decision."""
        for prepared in (False, True):
            for raises in (False, True):
                calls = []

                def callback(instance, _user, _config, *, calls=calls, raises=raises):
                    calls.append(instance)
                    if len(calls) == 3 and raises:
                        message = "third calculation callback"
                        raise RuntimeError(message)
                    return len(calls) < 3

                with (
                    self.subTest(prepared=prepared, raises=raises),
                    self.permission_callback(callback),
                    self.assertRaisesMessage(
                        RuntimeError if raises else GraphQLError,
                        "third calculation callback"
                        if raises
                        else "query field 'axis'",
                    ),
                ):
                    self.authorize([self.record] * 64, ["axis"], prepared=prepared)
                assert len(calls) == 3
                assert all(instance is self.record for instance in calls)

    def test_passing_additional_rule_keeps_each_budget_charge(self):
        """A passing stricter rule still consumes budget before its field callback."""
        self.info.context.user = get_user_model().objects.create_user(
            username="calculation-query-proof"
        )
        original = graphql_resolvers.check_read_permission
        for prepared in (False, True):
            fields = []
            budget = query_permissions.QueryFieldAuthorizationBudget(limit=3)

            def checked(instance, info, field, *, fields=fields, **kwargs):
                fields.append(field)
                return original(instance, info, field, **kwargs)

            with (
                self.subTest(prepared=prepared),
                patch.object(
                    self.Calculation.Permission,
                    "axis",
                    {"read": ["isAuthenticated"]},
                    create=True,
                ),
                patch.object(graphql_resolvers, "check_read_permission", checked),
                self.assertRaisesMessage(GraphQLError, "candidate limit"),
            ):
                self.authorize(
                    [self.record, self.record],
                    ["axis", "axis__gte"],
                    prepared=prepared,
                    budget=budget,
                )
            assert fields == ["axis"] * 3
            assert budget.decisions == 4

    def test_public_field_has_no_additional_budget_charge(self):
        for prepared in (False, True):
            budget = query_permissions.QueryFieldAuthorizationBudget(limit=0)
            self.authorize(
                [self.record] * 64,
                ["axis", "day__gte", "subject"],
                prepared=prepared,
                budget=budget,
            )
            assert budget.decisions == 0

    def test_input_subclass_metadata_hook_keeps_error_order(self):
        """An Input subclass must not have its executable metadata frozen or probed."""
        original_input = self.Calculation.Interface.input_fields["axis"]
        for key in ("type", "required"):
            for prepared in (False, True):
                events = []

                class DynamicInput(Input):
                    def __getattribute__(item, name, *, key=key, events=events):
                        if name == key:
                            events.append("metadata")
                            if events.count("metadata") == 3:
                                message = "third input metadata access"
                                raise RuntimeError(message)
                        return super().__getattribute__(name)

                dynamic = object.__new__(DynamicInput)
                vars(dynamic).update(vars(original_input))

                def callback(_instance, _user, _config, *, events=events):
                    events.append("permission")
                    return True

                with (
                    self.subTest(key=key, prepared=prepared),
                    patch.object(
                        self.Calculation.Interface,
                        "input_fields",
                        {**self.Calculation.Interface.input_fields, "axis": dynamic},
                    ),
                    self.permission_callback(callback),
                    self.assertRaisesMessage(
                        RuntimeError, "third input metadata access"
                    ),
                ):
                    self.authorize([self.record] * 64, ["axis"], prepared=prepared)
                assert events == [
                    "metadata",
                    "permission",
                    "metadata",
                    "permission",
                    "metadata",
                ]

    def test_canonical_input_data_descriptor_keeps_error_order(self):
        """Even exact Input instances can acquire executable class descriptors."""
        inputs = self.Calculation.Interface.input_fields
        watched = inputs["axis"]
        for key in ("type", "required"):
            for prepared in (False, True):
                events = []

                def metadata(item, *, key=key, events=events):
                    if item is watched:
                        events.append("metadata")
                        if events.count("metadata") == 3:
                            message = "third canonical metadata access"
                            raise RuntimeError(message)
                    return vars(item)[key]

                def callback(_instance, _user, _config, *, events=events):
                    events.append("permission")
                    return True

                with (
                    self.subTest(key=key, prepared=prepared),
                    patch.object(Input, key, property(metadata), create=True),
                    self.permission_callback(callback),
                    self.assertRaisesMessage(
                        RuntimeError, "third canonical metadata access"
                    ),
                ):
                    self.authorize([self.record] * 64, ["axis"], prepared=prepared)
                assert events == [
                    "metadata",
                    "permission",
                    "metadata",
                    "permission",
                    "metadata",
                ]

    def test_custom_input_mapping_keeps_error_order(self):
        """A mapping subclass's items provider must keep its per-visit behavior."""
        for prepared in (False, True):
            events = []

            class DynamicInputs(dict):
                def items(mapping, *, events=events):
                    events.append("metadata")
                    if events.count("metadata") == 3:
                        message = "third mapping access"
                        raise RuntimeError(message)
                    return super().items()

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            with (
                self.subTest(prepared=prepared),
                patch.object(
                    self.Calculation.Interface,
                    "input_fields",
                    DynamicInputs(self.Calculation.Interface.input_fields),
                ),
                self.permission_callback(callback),
                self.assertRaisesMessage(RuntimeError, "third mapping access"),
            ):
                self.authorize([self.record] * 64, ["axis"], prepared=prepared)
            assert events == [
                "metadata",
                "permission",
                "metadata",
                "permission",
                "metadata",
            ]

    def test_custom_read_capability_keeps_error_order(self):
        """A read capability override must never be consumed by an eligibility probe."""
        for prepared in (False, True):
            events = []

            class DynamicRead(CalculationReadCapability):
                def get_attribute_types(handler, interface, *, events=events):
                    events.append("metadata")
                    if events.count("metadata") == 3:
                        message = "third read capability access"
                        raise RuntimeError(message)
                    return super().get_attribute_types(interface)

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            with (
                self.subTest(prepared=prepared),
                patch.dict(
                    self.Calculation.Interface._capability_handlers,
                    {"read": DynamicRead()},
                ),
                self.permission_callback(callback),
                self.assertRaisesMessage(RuntimeError, "third read capability access"),
            ):
                self.authorize([self.record] * 64, ["axis"], prepared=prepared)
            assert events == [
                "metadata",
                "permission",
                "metadata",
                "permission",
                "metadata",
            ]

    def test_interface_metadata_override_keeps_error_order(self):
        original = self.Calculation.Interface.get_attribute_types
        for prepared in (False, True):
            events = []

            def metadata(_interface, *, events=events):
                events.append("metadata")
                if events.count("metadata") == 3:
                    message = "third interface metadata access"
                    raise RuntimeError(message)
                return original()

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            with (
                self.subTest(prepared=prepared),
                patch.object(
                    self.Calculation.Interface,
                    "get_attribute_types",
                    classmethod(metadata),
                ),
                self.permission_callback(callback),
                self.assertRaisesMessage(
                    RuntimeError, "third interface metadata access"
                ),
            ):
                self.authorize([self.record] * 64, ["axis"], prepared=prepared)
            assert events == [
                "metadata",
                "permission",
                "metadata",
                "permission",
                "metadata",
            ]

    def test_custom_identification_keeps_later_access_error(self):
        identification = self.record.identification
        for prepared in (False, True):
            events = []

            def identify(_instance, *, events=events):
                events.append("identification")
                if events.count("identification") == 3:
                    message = "third identification access"
                    raise RuntimeError(message)
                return identification

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            with (
                self.subTest(prepared=prepared),
                patch.object(self.Calculation, "identification", property(identify)),
                self.permission_callback(callback),
                self.assertRaisesMessage(RuntimeError, "third identification access"),
            ):
                self.authorize([self.record] * 64, ["axis"], prepared=prepared)
            assert events == [
                "identification",
                "permission",
                "identification",
                "permission",
                "identification",
            ]

    def test_unrelated_namespace_reflection_keeps_later_error(self):
        """GraphQL property discovery must retain executable isinstance lookups."""
        observations = []
        for prepared in (False, True):
            events = []

            class ReflectionValue:
                @property
                def __class__(value, events=events):
                    events.append("reflection")
                    if events.count("reflection") == 3:
                        message = "third namespace reflection"
                        raise RuntimeError(message)
                    return type(value)

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            error = None
            with (
                patch.object(
                    self.Calculation,
                    "unrelated_metadata",
                    ReflectionValue(),
                    create=True,
                ),
                self.permission_callback(callback),
            ):
                try:
                    self.authorize([self.record] * 64, ["axis"], prepared=prepared)
                except RuntimeError as exception:
                    error = str(exception)
            observations.append((error, events))

        assert observations[0] == (
            "third namespace reflection",
            ["reflection", "permission", "reflection", "permission", "reflection"],
        )
        assert observations[1] == observations[0]

    def test_unrelated_namespace_metaclass_mro_is_not_read(self):
        """A passive namespace probe must bypass executable metaclass descriptors."""
        observations = []
        for prepared in (False, True):
            events = []

            class DynamicMeta(type):
                @property
                def __mro__(cls, events=events):
                    events.append("mro")
                    message = "unrelated namespace MRO read"
                    raise RuntimeError(message)

            class NamespaceValue(metaclass=DynamicMeta):
                pass

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            error = None
            with (
                patch.object(
                    self.Calculation,
                    "unrelated_metadata",
                    NamespaceValue(),
                    create=True,
                ),
                self.permission_callback(callback),
            ):
                try:
                    self.authorize(
                        [self.record, self.record], ["axis"], prepared=prepared
                    )
                except RuntimeError as exception:
                    error = str(exception)
            observations.append((error, events))

        assert observations[0] == (None, ["permission", "permission"])
        assert observations[1] == observations[0]

    def test_empty_candidates_do_not_read_metadata(self):
        def metadata(_interface):
            message = "empty candidate metadata"
            raise RuntimeError(message)

        with patch.object(
            self.Calculation.Interface, "get_attribute_types", classmethod(metadata)
        ):
            for prepared in (False, True):
                self.authorize(iter(()), ["axis", "zz_missing"], prepared=prepared)

    def test_earlier_denial_precedes_later_unknown_operand(self):
        for prepared in (False, True):
            with (
                self.subTest(prepared=prepared),
                patch.object(
                    self.Calculation.Permission,
                    "axis",
                    {"read": ["isAdmin"]},
                    create=True,
                ),
                self.assertRaisesMessage(GraphQLError, "query field 'axis'"),
            ):
                self.authorize(
                    [self.record] * 64, ["zz_missing", "axis"], prepared=prepared
                )

    def test_new_call_observes_input_names_and_permission_declarations(self):
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                self.authorize([self.record] * 64, ["axis"], prepared=prepared)
                with patch.object(
                    self.Calculation.Permission,
                    "axis",
                    {"read": ["isAdmin"]},
                    create=True,
                ):
                    with self.assertRaisesMessage(GraphQLError, "query field 'axis'"):
                        self.authorize([self.record] * 64, ["axis"], prepared=prepared)
                with patch.object(
                    self.Calculation.Interface,
                    "input_fields",
                    {
                        **self.Calculation.Interface.input_fields,
                        "temporary": Input(int),
                    },
                ):
                    self.authorize([self.record] * 64, ["temporary"], prepared=prepared)
                with self.assertRaisesMessage(GraphQLError, "query field 'temporary'"):
                    self.authorize([self.record] * 64, ["temporary"], prepared=prepared)

    def test_new_call_observes_changed_input_type_and_relation_gate(self):
        axis = self.Calculation.Interface.input_fields["axis"]
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                self.authorize([self.record] * 64, ["axis__exact"], prepared=prepared)
                with (
                    patch.object(
                        self.Calculation,
                        "axis",
                        property(lambda _instance: self.subject),
                    ),
                    patch.object(axis, "type", self.Subject),
                    patch.object(
                        self.Subject.Permission, "can_read_instance", return_value=False
                    ),
                    self.assertRaises(query_permissions.QueryRelationPermissionError),
                ):
                    self.authorize(
                        [self.record] * 64, ["axis__exact"], prepared=prepared
                    )

    def test_instance_policy_override_is_not_replaced_by_class_plan(self):
        class DenyPermission(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["isAdmin"]

        other = self.make_record(axis=2)
        other.Permission = DenyPermission
        for prepared in (False, True):
            with (
                self.subTest(prepared=prepared),
                self.assertRaisesMessage(GraphQLError, "query field 'axis'"),
            ):
                self.authorize([self.record, other], ["axis"], prepared=prepared)

    def test_instance_interface_override_keeps_its_metadata_provider(self):
        original = self.Calculation.Interface
        for prepared in (False, True):
            events = []
            other = self.make_record(axis=2)

            def metadata(*, events=events):
                events.append("metadata")
                if events.count("metadata") == 3:
                    message = "third instance metadata access"
                    raise RuntimeError(message)
                return original.get_attribute_types()

            def callback(_instance, _user, _config, *, events=events):
                events.append("permission")
                return True

            other.Interface = SimpleNamespace(
                get_attribute_types=metadata,
                get_attributes=original.get_attributes,
                get_graph_ql_properties=original.get_graph_ql_properties,
                input_fields=original.input_fields,
            )
            with (
                self.subTest(prepared=prepared),
                self.permission_callback(callback),
                self.assertRaisesMessage(
                    RuntimeError, "third instance metadata access"
                ),
            ):
                self.authorize(
                    [self.record, other, other, other], ["axis"], prepared=prepared
                )
            assert events == [
                "permission",
                "metadata",
                "permission",
                "metadata",
                "permission",
                "metadata",
            ]

    def test_delegated_passing_field_rule_keeps_resolved_budget(self):
        class DelegatedPermission(OverrideManagerPermission):
            __based_on__: ClassVar[str] = "subject"

        self.info.context.user = get_user_model().objects.create_user(
            username="calculation-delegated-query-proof"
        )
        for prepared in (False, True):
            budget = query_permissions.QueryFieldAuthorizationBudget(limit=2)
            with (
                self.subTest(prepared=prepared),
                patch.object(self.Calculation, "Permission", DelegatedPermission),
                patch.object(
                    self.Subject.Permission,
                    "axis",
                    {"read": ["isAuthenticated"]},
                    create=True,
                ),
                self.assertRaisesMessage(GraphQLError, "candidate limit"),
            ):
                self.authorize(
                    [self.record] * 64, ["axis"], prepared=prepared, budget=budget
                )
            assert budget.decisions == 3

    def test_terminal_manager_input_does_not_read_its_target(self):
        def forbidden(*_args, **_kwargs):
            message = "terminal input target was read"
            raise RuntimeError(message)

        with (
            patch.object(self.Calculation, "subject", property(forbidden)),
            patch.object(
                self.Subject.Interface, "get_attribute_types", classmethod(forbidden)
            ),
            patch.object(self.Subject.Permission, "can_read_instance", forbidden),
        ):
            for prepared in (False, True):
                self.authorize([self.record] * 64, ["subject"], prepared=prepared)

    def test_calculation_identity_spelling_keeps_both_field_checks(self):
        original = graphql_resolvers.check_read_permission
        observations = []
        for prepared in (False, True):
            fields = []

            def checked(instance, info, field, *, fields=fields, **kwargs):
                fields.append(field)
                return original(instance, info, field, **kwargs)

            with patch.object(graphql_resolvers, "check_read_permission", checked):
                self.authorize([self.record], ["subject_id"], prepared=prepared)
            observations.append(fields)
            assert len(fields) == 2
            assert set(fields) == {"subject", "subject_id"}
            with (
                patch.object(
                    self.Calculation.Permission,
                    "subject",
                    {"read": ["isAdmin"]},
                    create=True,
                ),
                self.assertRaisesMessage(GraphQLError, "query field 'subject_id'"),
            ):
                self.authorize([self.record], ["subject_id"], prepared=prepared)
        assert observations[0] == observations[1]

    def test_terminal_manager_does_not_inherit_identity_spelling_rule(self):
        with patch.object(
            self.Calculation.Permission,
            "subject_id",
            {"read": ["isAdmin"]},
            create=True,
        ):
            for prepared in (False, True):
                budget = query_permissions.QueryFieldAuthorizationBudget(limit=0)
                self.authorize(
                    [self.record] * 64,
                    ["subject"],
                    prepared=prepared,
                    budget=budget,
                )
                assert budget.decisions == 0

    def test_nested_manager_and_native_paths_keep_related_row_gate(self):
        with patch.object(
            self.Subject.Permission, "can_read_instance", return_value=False
        ):
            for prepared in (False, True):
                for native in (False, True):
                    for path in ("subject__code", "subject__exact"):
                        with (
                            self.subTest(prepared=prepared, native=native, path=path),
                            self.assertRaises(
                                query_permissions.QueryRelationPermissionError
                            ),
                        ):
                            self.authorize(
                                [self.record] * 64,
                                [path],
                                prepared=prepared,
                                native_paths=native,
                            )

    def test_flat_authorization_does_not_invoke_input_providers(self):
        def forbidden(*_args, **_kwargs):
            message = "query metadata invoked an input provider"
            raise RuntimeError(message)

        axis = self.Calculation.Interface.input_fields["axis"]
        with (
            patch.object(axis, "possible_values", forbidden),
            patch.object(axis, "normalizer", forbidden),
            patch.object(axis, "validator", forbidden),
        ):
            for prepared in (False, True):
                self.authorize(
                    [self.record] * 64,
                    ["axis", "axis__gte", "subject"],
                    prepared=prepared,
                )

    def test_permission_callback_input_provider_error_keeps_order(self):
        axis = self.Calculation.Interface.input_fields["axis"]
        for prepared in (False, True):
            rows = [self.make_record(axis=value) for value in (1, 2, 3)]
            events = []

            def domain(*, events=events):
                events.append("domain")
                return range(10)

            def normalize(value, *, domain, events=events):
                events.append("normalizer")
                assert value in domain
                if value == 3:
                    message = "third normalized input"
                    raise RuntimeError(message)
                return value

            def callback(instance, _user, _config, *, events=events):
                events.append("permission")
                return instance.axis > 0

            with (
                self.subTest(prepared=prepared),
                patch.object(axis, "possible_values", domain),
                patch.object(axis, "normalizer", normalize),
                self.permission_callback(callback),
                self.assertRaisesMessage(
                    AttributeEvaluationError, "third normalized input"
                ),
            ):
                self.authorize(rows, ["axis"], prepared=prepared)
            # AttributeEvaluationError is an AttributeError: the existing late
            # descriptor fallback retries the failing third getter once.
            assert events == ["permission", "domain", "normalizer"] * 3 + [
                "domain",
                "normalizer",
            ]


class TestGraphQLCalculationLeapYearScope(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class LeapProofSubject(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int, possible_values=range(1, 4))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class LeapProofCalendar(GeneralManager):
            class Interface(CalculationInterface):
                subject = Input(
                    LeapProofSubject, possible_values=lambda: LeapProofSubject.all()
                )
                day = Input.date_range(start=date(2024, 1, 1), end=date(2024, 12, 31))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.Subject = LeapProofSubject
        cls.Calendar = LeapProofCalendar
        cls.general_manager_classes = [LeapProofSubject, LeapProofCalendar]
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.info = SimpleNamespace(context=SimpleNamespace(user=AnonymousUser()))
        self.resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Calendar.all(), self.Calendar
        )

    @staticmethod
    def identity(instance):
        identification = instance.identification
        return identification["subject"]["id"], identification["day"]

    @staticmethod
    def expected_identities(month=None):
        months = (month,) if month is not None else range(1, 13)
        return {
            (subject_id, date(2024, period, day))
            for subject_id in (1, 2, 3)
            for period in months
            for day in range(1, monthrange(2024, period)[1] + 1)
        }

    @contextmanager
    def authorized_scopes(self, *, prepared=True):
        """Observe the contributor owner's exact row-visible requested instances."""
        from general_manager.api import graphql_contributors

        scopes = []
        original = graphql_contributors.filter_contributors

        def authorize(candidates, info, paths, **kwargs):
            instances = tuple(candidates)
            scopes.append([self.identity(instance) for instance in instances])
            return original(candidates.with_instances(instances), info, paths, **kwargs)

        with (
            patch.object(graphql_contributors, "filter_contributors", authorize),
            nullcontext()
            if prepared
            else patch.object(
                query_permissions._NativeQueryStructures,
                "for_instance",
                return_value=None,
            ),
        ):
            yield scopes

    def resolve_period(self, start, end):
        return self.resolver(
            None,
            self.info,
            filter={"day__gte": start, "day__lte": end},
            order_by=[
                {"field": "subject__id", "direction": "ASC"},
                {"field": "day", "direction": "ASC"},
            ],
        )

    def test_full_leap_year_matches_all_twelve_monthly_scopes(self):
        """Three subjects retain every full-period identity, including February 29."""
        expected = self.expected_identities()
        assert len(expected) == 1098
        assert {(subject, date(2024, 2, 29)) for subject in (1, 2, 3)} <= expected
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                with self.authorized_scopes(prepared=prepared) as scopes:
                    full = self.resolve_period(date(2024, 1, 1), date(2024, 12, 31))
                full_identities = [self.identity(item) for item in full["items"]]
                assert len(scopes) == 1
                assert len(scopes[0]) == len(full_identities) == 1098
                assert set(scopes[0]) == set(full_identities) == expected
                assert full["pageInfo"] == {
                    "total_count": 1098,
                    "page_size": None,
                    "current_page": 1,
                    "total_pages": 1,
                }

                monthly_identities = []
                for month in range(1, 13):
                    with self.authorized_scopes(prepared=prepared) as scopes:
                        result = self.resolve_period(
                            date(2024, month, 1),
                            date(2024, month, monthrange(2024, month)[1]),
                        )
                    expected_month = self.expected_identities(month)
                    identities = [self.identity(item) for item in result["items"]]
                    assert len(scopes) == 1
                    assert len(scopes[0]) == len(identities) == len(expected_month)
                    assert set(scopes[0]) == set(identities) == expected_month
                    assert result["pageInfo"]["total_count"] == len(expected_month)
                    assert result["pageInfo"]["page_size"] is None
                    monthly_identities.extend(identities)

                assert len(monthly_identities) == 1098
                assert set(monthly_identities) == set(full_identities)

    def test_graphql_omitted_and_null_pagination_return_the_full_leap_year(self):
        document = """
            query {
                leapProofCalendarList(
                    PAGINATION
                    orderBy: [{field: subject__id}, {field: day}]
                ) {
                    items { subject { id } day }
                    pageInfo { totalCount pageSize currentPage totalPages }
                }
            }
        """
        expected = self.expected_identities()
        responses = []
        for controls in ("", "page: null, pageSize: null,"):
            with self.subTest(controls=controls):
                with self.authorized_scopes() as scopes:
                    response = self.query(document.replace("PAGINATION", controls))
                self.assertResponseNoErrors(response)
                result = response.json()["data"]["leapProofCalendarList"]
                identities = [
                    (int(item["subject"]["id"]), date.fromisoformat(item["day"]))
                    for item in result["items"]
                ]
                assert len(scopes) == 1
                assert len(scopes[0]) == len(identities) == 1098
                assert set(scopes[0]) == set(identities) == expected
                assert result["pageInfo"] == {
                    "totalCount": 1098,
                    "pageSize": None,
                    "currentPage": 1,
                    "totalPages": 1,
                }
                responses.append(result)
        assert responses[0] == responses[1]

    def test_direct_resolver_excludes_late_denial_and_preserves_callback_error(self):
        """An off-page denial changes total; an off-page callback error still fails."""
        denied_identity = (3, date(2024, 12, 31))
        for raises in (False, True):
            observations = []
            for prepared in (False, True):
                visits = []

                def callback(instance, _user, _config, *, visits=visits, raises=raises):
                    identity = self.identity(instance)
                    visits.append(identity)
                    if identity == denied_identity:
                        if raises:
                            message = "final leap-year identity denied"
                            raise RuntimeError(message)
                        return False
                    return True

                with (
                    self.subTest(prepared=prepared, raises=raises),
                    patch.dict(
                        permission_functions,
                        {
                            "leapCalendarProof": {
                                "permission_method": callback,
                                "permission_filter": lambda *_: None,
                            }
                        },
                    ),
                    patch.object(
                        self.Calendar.Permission,
                        "day",
                        {"read": ["leapCalendarProof"]},
                        create=True,
                    ),
                    self.authorized_scopes(prepared=prepared),
                    self.assertRaisesMessage(
                        RuntimeError, "final leap-year identity denied"
                    )
                    if raises
                    else nullcontext() as caught,
                ):
                    result = self.resolver(
                        None,
                        self.info,
                        filter={
                            "day__gte": date(2024, 1, 1),
                            "day__lte": date(2024, 12, 31),
                        },
                        order_by=[{"field": "day", "direction": "ASC"}],
                        page=1,
                        page_size=1,
                    )
                assert visits[-1] == denied_identity
                if raises:
                    observations.append(
                        (type(caught.exception), str(caught.exception), visits)
                    )
                else:
                    assert result["pageInfo"]["total_count"] == 1097
                    assert len(result["items"]) == 1
                    assert self.identity(result["items"][0]) != denied_identity
                    observations.append((result["pageInfo"], visits))
            assert observations[0] == observations[1]
