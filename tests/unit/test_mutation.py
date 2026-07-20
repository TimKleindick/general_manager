import asyncio
import inspect
from typing import Optional, List, ClassVar
from unittest import mock

import graphene
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import TestCase
from django.contrib.auth.models import AnonymousUser, User

from general_manager.api.graphql_mutations import _normalize_mutation_kwargs_for_manager
from general_manager.api.graphql_errors import PublicGraphQLError
from general_manager.api.mutation import _sequence_argument, graph_ql_mutation
from general_manager.api.graphql import GraphQL
from general_manager.as_of import HistoricalMutationError, as_of
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.interface.base_interface import InterfaceBase
from general_manager.permission.base_permission import PermissionCheckError
from general_manager.permission.mutation_permission import MutationPermission
from graphql import GraphQLError


type test123 = str

type int_1 = int
type int_2 = int
type int_3 = int


class DummyInterface(InterfaceBase):
    input_fields: ClassVar[dict] = {}

    def __init__(self, *args, **kwargs):
        pass

    def get_data(self, search_date=None):
        pass

    @classmethod
    def get_attribute_types(cls):
        return {}

    @classmethod
    def get_attributes(cls):
        return {}

    @classmethod
    def filter(cls, **kwargs):
        raise NotImplementedError("This method should be implemented in a subclass")

    @classmethod
    def exclude(cls, **kwargs):
        raise NotImplementedError("This method should be implemented in a subclass")

    @classmethod
    def handle_interface(cls):
        def pre(_name, attrs, interface):
            return attrs, interface, None

        def post(new_class, interface_cls, model):
            pass

        return pre, post

    @classmethod
    def get_field_type(cls, _field_name: str):
        return str


class DummyGM(GeneralManager):
    def __init__(self, name: str):
        self.name = name

    class Interface(DummyInterface):
        pass


class SingleInputInterface(DummyInterface):
    input_fields: ClassVar[dict] = {"id": Input(int)}

    def __init__(self, *args, **kwargs):
        InterfaceBase.__init__(self, *args, **kwargs)


class SingleInputGM(GeneralManager):
    class Interface(SingleInputInterface):
        pass


# Required because the dummy handle_interface hook does not attach Interface.
SingleInputGM.Interface = SingleInputInterface


class ValidationInputInterface(DummyInterface):
    input_fields: ClassVar[dict] = {}

    @classmethod
    def get_attribute_types(cls):
        return {
            "project_phase_type": {
                "type": str,
                "is_required": False,
                "default": None,
                "is_derived": False,
                "is_editable": True,
            },
            "customer": {
                "type": SingleInputGM,
                "is_required": False,
                "default": None,
                "is_derived": False,
                "is_editable": True,
                "relation_kind": "direct",
            },
            "member_list": {
                "type": SingleInputGM,
                "is_required": False,
                "default": None,
                "is_derived": False,
                "is_editable": True,
            },
        }


class ValidationInputGM(GeneralManager):
    class Interface(ValidationInputInterface):
        pass

    @classmethod
    def create(cls, **kwargs):
        if "member_id_list" in kwargs:
            raise ValidationError({"member_id_list": ["Members are required."]})
        raise ValidationError(
            {
                "project_phase_type": ["This field cannot be null."],
                "customer_id": ["Customer is required."],
                NON_FIELD_ERRORS: ["Project dates overlap."],
            }
        )


ValidationInputGM.Interface = ValidationInputInterface


class ValidationMutationGM(GeneralManager):
    class Interface(ValidationInputInterface):
        pass

    def __init__(self, *args, **kwargs):
        _ = args, kwargs

    def update(self, **kwargs):
        _ = kwargs
        raise ValidationError({"customer_id": ["Customer is required."]})

    def delete(self, **kwargs):
        _ = kwargs
        raise ValidationError({"customer_id": ["Customer is required."]})


ValidationMutationGM.Interface = ValidationInputInterface


class MultiInputInterface(DummyInterface):
    input_fields: ClassVar[dict] = {
        "tenant": Input(str),
        "code": Input(int),
    }

    def __init__(self, *args, **kwargs):
        InterfaceBase.__init__(self, *args, **kwargs)


class MultiInputGM(GeneralManager):
    class Interface(MultiInputInterface):
        pass


# Required because the dummy handle_interface hook does not attach Interface.
MultiInputGM.Interface = MultiInputInterface


class RegionInputInterface(DummyInterface):
    input_fields: ClassVar[dict] = {
        "region": Input(str),
    }

    def __init__(self, *args, **kwargs):
        InterfaceBase.__init__(self, *args, **kwargs)


class HistoricalMutationOverrideGuardTests(TestCase):
    def test_sync_mutation_overrides_are_guarded_without_changing_binding(self) -> None:
        calls: list[tuple[str, object]] = []

        class CustomInterface(DummyInterface):
            @classmethod
            def create(cls, value: str) -> str:
                calls.append(("create", cls))
                return value

            def update(self, value: str) -> str:
                calls.append(("update", self))
                return value

            def delete(self) -> str:
                calls.append(("delete", self))
                return "deleted"

        interface = CustomInterface()

        self.assertEqual(CustomInterface.create("outside"), "outside")
        self.assertEqual(interface.update("outside"), "outside")
        self.assertEqual(interface.delete(), "deleted")
        self.assertEqual(
            [name for name, _target in calls], ["create", "update", "delete"]
        )
        self.assertIs(calls[0][1], CustomInterface)
        self.assertIs(calls[1][1], interface)
        self.assertEqual(CustomInterface.create.__name__, "create")
        self.assertTrue(hasattr(CustomInterface.create, "__wrapped__"))
        self.assertFalse(hasattr(CustomInterface.create.__wrapped__, "__wrapped__"))

        calls.clear()
        with as_of("2022-01-01"):
            with self.assertRaises(HistoricalMutationError):
                CustomInterface.create("blocked")
            with self.assertRaises(HistoricalMutationError):
                interface.update("blocked")
            with self.assertRaises(HistoricalMutationError):
                interface.delete()

        self.assertEqual(calls, [])

    def test_async_mutation_override_is_guarded_and_remains_async(self) -> None:
        calls: list[str] = []

        class AsyncInterface(DummyInterface):
            @classmethod
            async def create(cls, value: str) -> str:
                _ = cls
                calls.append(value)
                return value

        async def exercise() -> None:
            self.assertEqual(await AsyncInterface.create("outside"), "outside")
            with as_of("2022-01-01"):
                with self.assertRaises(HistoricalMutationError):
                    await AsyncInterface.create("blocked")

        self.assertTrue(inspect.iscoroutinefunction(AsyncInterface.create))
        asyncio.run(exercise())
        self.assertEqual(calls, ["outside"])


class MutationDecoratorTests(TestCase):
    def tearDown(self) -> None:
        GraphQL._mutations.clear()

    def test_missing_parameter_hint(self):
        with self.assertRaises(TypeError):

            @graph_ql_mutation()
            def bad(info, value) -> str:
                _ = info
                _ = value
                return "x"

    def test_missing_return_annotation(self):
        with self.assertRaises(TypeError):

            @graph_ql_mutation()
            def bad(info, value: int):
                _ = info
                _ = value
                return "x"

    def test_invalid_return_type(self):
        with self.assertRaises(TypeError):

            @graph_ql_mutation()
            def bad(info, value: int) -> List[str]:
                _ = info
                _ = value
                return []

    def test_optional_argument_defaults(self):
        @graph_ql_mutation()
        def opt(info, value: Optional[int] = None) -> int:
            _ = info
            return value or 0

        mutation = GraphQL._mutations["opt"]
        arg = mutation._meta.arguments["value"]
        self.assertFalse(arg.kwargs.get("required"))
        self.assertIsNone(arg.kwargs.get("default_value"))

    def test_general_manager_argument_uses_id(self):
        @graph_ql_mutation()
        def gm(info, item: SingleInputGM) -> str:
            _ = info
            _ = item
            return "ok"

        mutation = GraphQL._mutations["gm"]
        arg = mutation._meta.arguments["item"]
        self.assertIsInstance(arg, graphene.ID)

    def test_general_manager_argument_passes_manager_to_permission_and_resolver(self):
        class CapturePermission(MutationPermission):
            received_item = None

            @classmethod
            def check(cls, data: dict, user: object) -> None:
                cls.received_item = data["item"]

        @graph_ql_mutation(permission=CapturePermission)
        def gm(info, item: SingleInputGM) -> str:
            _ = info
            return str(item.identification["id"])

        mutation = GraphQL._mutations["gm"]
        info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        result = mutation.mutate(None, info, item="42")

        self.assertTrue(result.success)
        self.assertEqual(result.str, "42")
        self.assertIsInstance(CapturePermission.received_item, SingleInputGM)
        self.assertEqual(CapturePermission.received_item.identification, {"id": 42})

    def test_multi_input_general_manager_argument_uses_input_object(self):
        seen: dict[str, MultiInputGM] = {}

        @graph_ql_mutation()
        def gm(info, item: MultiInputGM) -> str:
            _ = info
            seen["item"] = item
            return f"{item.identification['tenant']}:{item.identification['code']}"

        mutation = GraphQL._mutations["gm"]
        arg = mutation._meta.arguments["item"]
        arg_type = arg.type.of_type if hasattr(arg.type, "of_type") else arg.type
        self.assertTrue(issubclass(arg_type, graphene.InputObjectType))
        self.assertIn("tenant", arg_type._meta.fields)
        self.assertIn("code", arg_type._meta.fields)

        info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        result = mutation.mutate(
            None,
            info,
            item={"tenant": "customer-a", "code": "7"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.str, "customer-a:7")
        self.assertEqual(seen["item"].identification["code"], 7)
        self.assertIsInstance(seen["item"].identification["code"], int)

    def test_manager_input_type_cache_uses_unique_manager_identifier(self):
        FirstSharedName = type(
            "SharedName",
            (GeneralManager,),
            {
                "__module__": "tests.unit.test_mutation.first",
                "Interface": MultiInputInterface,
            },
        )
        FirstSharedName.Interface = MultiInputInterface
        SecondSharedName = type(
            "SharedName",
            (GeneralManager,),
            {
                "__module__": "tests.unit.test_mutation.second",
                "Interface": RegionInputInterface,
            },
        )
        SecondSharedName.Interface = RegionInputInterface

        @graph_ql_mutation()
        def first(info, item: FirstSharedName) -> str:
            _ = info, item
            return "first"

        @graph_ql_mutation()
        def second(info, item: SecondSharedName) -> str:
            _ = info, item
            return "second"

        first_type = GraphQL._mutations["first"]._meta.arguments["item"].type.of_type
        second_type = GraphQL._mutations["second"]._meta.arguments["item"].type.of_type

        self.assertIsNot(first_type, second_type)
        self.assertIn("tenant", first_type._meta.fields)
        self.assertIn("code", first_type._meta.fields)
        self.assertIn("region", second_type._meta.fields)
        self.assertNotIn("tenant", second_type._meta.fields)

    def test_manager_argument_normalization_errors_are_graphql_errors(self):
        @graph_ql_mutation()
        def gm(info, item: SingleInputGM) -> str:
            _ = info
            return str(item.identification["id"])

        mutation = GraphQL._mutations["gm"]
        info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(None, info, item="not-an-int")

        self.assertEqual(ctx.exception.message, "An internal server error occurred.")
        self.assertEqual(ctx.exception.extensions["code"], "INTERNAL_SERVER_ERROR")
        self.assertNotIn("not-an-int", str(ctx.exception.formatted))

    def test_decorator_mutation_validation_errors_use_schema_argument_names(self):
        @graph_ql_mutation()
        def validate_project(info, project_phase_type: str) -> str:
            _ = info, project_phase_type
            raise ValidationError(
                {
                    "project_phase_type": ["This field cannot be null."],
                    NON_FIELD_ERRORS: ["Project dates overlap."],
                }
            )

        mutation = GraphQL._mutations["validateProject"]
        info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(None, info, project_phase_type="")

        self.assertEqual(ctx.exception.message, "Validation failed.")
        self.assertEqual(
            ctx.exception.extensions,
            {
                "code": "BAD_USER_INPUT",
                "fieldErrors": {
                    "projectPhaseType": ["This field cannot be null."],
                },
                "nonFieldErrors": ["Project dates overlap."],
            },
        )

    def test_generated_create_mutation_validation_errors_use_schema_field_names(self):
        mutation = GraphQL.generate_create_mutation_class(ValidationInputGM, {})
        info = type(
            "Info",
            (),
            {
                "context": type(
                    "Ctx",
                    (),
                    {"user": type("User", (), {"id": 42})()},
                )()
            },
        )()

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(
                None,
                info,
                project_phase_type=None,
                customer="7",
            )

        self.assertEqual(ctx.exception.message, "Validation failed.")
        self.assertEqual(
            ctx.exception.extensions,
            {
                "code": "BAD_USER_INPUT",
                "fieldErrors": {
                    "projectPhaseType": ["This field cannot be null."],
                    "customer": ["Customer is required."],
                },
                "nonFieldErrors": ["Project dates overlap."],
            },
        )

    def test_generated_create_mutation_validation_errors_use_schema_list_field_names(
        self,
    ):
        mutation = GraphQL.generate_create_mutation_class(ValidationInputGM, {})
        info = type(
            "Info",
            (),
            {
                "context": type(
                    "Ctx",
                    (),
                    {"user": type("User", (), {"id": 42})()},
                )()
            },
        )()

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(
                None,
                info,
                member_list=["7"],
            )

        self.assertEqual(ctx.exception.message, "Validation failed.")
        self.assertEqual(
            ctx.exception.extensions,
            {
                "code": "BAD_USER_INPUT",
                "fieldErrors": {
                    "memberList": ["Members are required."],
                },
                "nonFieldErrors": [],
            },
        )

    def test_generated_update_mutation_validation_errors_use_schema_field_names(self):
        mutation = GraphQL.generate_update_mutation_class(ValidationMutationGM, {})
        info = type(
            "Info",
            (),
            {
                "context": type(
                    "Ctx",
                    (),
                    {"user": type("User", (), {"id": 42})()},
                )()
            },
        )()

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(
                None,
                info,
                id="99",
                customer="7",
            )

        self.assertEqual(ctx.exception.message, "Validation failed.")
        self.assertEqual(
            ctx.exception.extensions,
            {
                "code": "BAD_USER_INPUT",
                "fieldErrors": {
                    "customer": ["Customer is required."],
                },
                "nonFieldErrors": [],
            },
        )

    def test_generated_delete_mutation_validation_errors_use_schema_field_names(self):
        mutation = GraphQL.generate_delete_mutation_class(ValidationMutationGM, {})
        info = type(
            "Info",
            (),
            {
                "context": type(
                    "Ctx",
                    (),
                    {"user": type("User", (), {"id": 42})()},
                )()
            },
        )()

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(
                None,
                info,
                id="99",
            )

        self.assertEqual(ctx.exception.message, "Validation failed.")
        self.assertEqual(
            ctx.exception.extensions,
            {
                "code": "BAD_USER_INPUT",
                "fieldErrors": {
                    "customer": ["Customer is required."],
                },
                "nonFieldErrors": [],
            },
        )

    def test_list_argument(self):
        @graph_ql_mutation()
        def many(info, items: List[int]) -> int:
            _ = info
            return sum(items)

        mutation = GraphQL._mutations["many"]
        arg = mutation._meta.arguments["items"]
        self.assertIsInstance(arg, graphene.List)
        self.assertEqual(arg.of_type, graphene.Int)

    def test_mutation_with_multiple_return_types(self):
        """
        Tests that a GraphQL mutation returning multiple values as a tuple correctly exposes each value as a separate field in the mutation response and that the mutation executes and returns expected results.
        """

        @graph_ql_mutation()
        def multi(info, value: int) -> tuple[bool, str]:
            _ = info
            if value > 0:
                return True, "Success"
            else:
                return False, "Failure"

        mutation = GraphQL._mutations["multi"]
        self.assertIn("success", mutation._meta.fields)
        self.assertIn("bool", mutation._meta.fields)
        self.assertIn("str", mutation._meta.fields)

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, value=1)
        self.assertTrue(res.success)
        self.assertEqual(res.bool, True)
        self.assertEqual(res.str, "Success")

    def test_tuple_return_length_mismatch_too_short_raises_graphql_error(self):
        @graph_ql_mutation()
        def too_short(info, value: int) -> tuple[bool, str]:
            _ = info, value
            return (True,)  # type: ignore[return-value]

        mutation = GraphQL._mutations["tooShort"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(None, Info, value=1)

        self.assertEqual(ctx.exception.message, "An internal server error occurred.")
        self.assertEqual(ctx.exception.extensions["code"], "INTERNAL_SERVER_ERROR")
        self.assertNotIn("expected 2", str(ctx.exception.formatted))
        self.assertNotIn("received 1", str(ctx.exception.formatted))

    def test_tuple_return_length_mismatch_too_long_raises_graphql_error(self):
        @graph_ql_mutation()
        def too_long(info, value: int) -> tuple[bool, str]:
            _ = info, value
            return True, "Success", "extra"  # type: ignore[return-value]

        mutation = GraphQL._mutations["tooLong"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        with self.assertRaises(GraphQLError) as ctx:
            mutation.mutate(None, Info, value=1)

        self.assertEqual(ctx.exception.message, "An internal server error occurred.")
        self.assertEqual(ctx.exception.extensions["code"], "INTERNAL_SERVER_ERROR")
        self.assertNotIn("expected 2", str(ctx.exception.formatted))
        self.assertNotIn("received 3", str(ctx.exception.formatted))

    def test_mutation_execution_and_auth(self):
        class addPermission(MutationPermission):
            __mutate__: ClassVar[List[str]] = ["isAuthenticated"]

        @graph_ql_mutation(permission=addPermission)
        def add(info, a: int, b: int) -> int:
            _ = info
            return a + b

        mutation = GraphQL._mutations["add"]

        InfoNoAuth = type("Info", (), {"context": type("Ctx", (), {"user": None})()})
        with self.assertRaises(GraphQLError):
            mutation.mutate(None, InfoNoAuth, a=1, b=2)

    def test_mutation_with_manager_return(self):
        @graph_ql_mutation()
        def create_item(info, name: str) -> DummyGM:
            _ = info
            return DummyGM(name=name)

        mutation = GraphQL._mutations["createItem"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, name="Test Item")
        self.assertTrue(res.success)
        self.assertIsInstance(res.dummyGM, DummyGM)
        self.assertEqual(res.dummyGM.name, "Test Item")

    def test_mutation_with_custom_types(self):
        @graph_ql_mutation()
        def custom_type(info, value: str) -> test123:
            _ = info
            return value

        mutation = GraphQL._mutations["customType"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, value="Hello")
        self.assertTrue(res.success)
        self.assertIsInstance(res.test123, str)
        self.assertEqual(res.test123, "Hello")

    # -------------------------------------------------------------------------
    # Additional tests (post-PR coverage)
    #
    # Framework note: Using django.test.TestCase (unittest) and Graphene.
    #
    # These tests focus on argument mapping, naming, permissions, runtime behavior,
    #
    # and tuple return handling, complementing the existing suite.
    # -------------------------------------------------------------------------

    def test_snake_case_to_camelcase_registration(self):
        @graph_ql_mutation()
        def my_mutation_case(info, value: int) -> int:
            _ = info
            return value

        # Function name with underscores should register in camelCase
        self.assertIn("myMutationCase", GraphQL._mutations)
        self.assertNotIn("my_mutation_case", GraphQL._mutations)

    def test_optional_list_argument_defaults_explicit(self):
        @graph_ql_mutation()
        def total(info, items: Optional[List[int]] = None) -> int:
            _ = info
            return sum(items or [])

        mutation = GraphQL._mutations["total"]
        arg = mutation._meta.arguments["items"]
        self.assertIsInstance(arg, graphene.List)
        self.assertEqual(arg.of_type, graphene.Int)
        self.assertFalse(arg.kwargs.get("required"))
        self.assertIsNone(arg.kwargs.get("default_value"))

    def test_general_manager_list_argument_uses_ids(self):
        @graph_ql_mutation()
        def bulk(info, items: List[DummyGM]) -> int:
            _ = info
            return len(items)

        mutation = GraphQL._mutations["bulk"]
        arg = mutation._meta.arguments["items"]
        self.assertIsInstance(arg, graphene.List)
        self.assertEqual(arg.of_type, graphene.ID)

    def test_general_manager_list_argument_normalizes_manager_items(self):
        seen: dict[str, list] = {}

        @graph_ql_mutation()
        def bulk_single(info, items: List[SingleInputGM]) -> int:
            _ = info
            seen["single"] = items
            return len(items)

        @graph_ql_mutation()
        def bulk_multi(info, items: List[MultiInputGM]) -> int:
            _ = info
            seen["multi"] = items
            return len(items)

        info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        single_result = GraphQL._mutations["bulkSingle"].mutate(
            None,
            info,
            items=["1", "2", "3"],
        )
        multi_result = GraphQL._mutations["bulkMulti"].mutate(
            None,
            info,
            items=[
                {"tenant": "a", "code": "1"},
                {"tenant": "b", "code": "2"},
            ],
        )

        self.assertEqual(single_result.int, 3)
        self.assertTrue(all(isinstance(item, SingleInputGM) for item in seen["single"]))
        self.assertEqual(
            [item.identification["id"] for item in seen["single"]],
            [1, 2, 3],
        )
        self.assertTrue(all(isinstance(item, MultiInputGM) for item in seen["multi"]))
        self.assertEqual(
            [item.identification for item in seen["multi"]],
            [{"tenant": "a", "code": 1}, {"tenant": "b", "code": 2}],
        )
        self.assertEqual(multi_result.int, 2)

    def test_permission_allows_authenticated(self):
        class addPermission(MutationPermission):
            __mutate__: ClassVar[List[str]] = ["isAuthenticated"]

        @graph_ql_mutation(permission=addPermission)
        def add_nums(info, a: int, b: int) -> int:
            _ = info
            return a + b

        mutation = GraphQL._mutations["addNums"]

        # Simulate an authenticated user (matches typical Django pattern)
        AuthUser = User()
        InfoAuth = type("Info", (), {"context": type("Ctx", (), {"user": AuthUser})()})

        res = mutation.mutate(None, InfoAuth, a=1, b=2)
        self.assertTrue(res.success)
        # For primitive int return types, the field is expected to be named "int"
        self.assertTrue(
            hasattr(res, "int"),
            "Expected 'int' field on mutation result for int return type",
        )
        self.assertEqual(res.int, 3)

    def test_missing_required_argument_raises(self):
        @graph_ql_mutation()
        def required(info, value: int) -> int:
            """
            Return the provided integer value unchanged.

            Parameters:
                info: GraphQL resolver info object (ignored by this function).
                value (int): The integer to return.

            Returns:
                int: The same integer passed in via `value`.
            """
            _ = info
            return value

        mutation = GraphQL._mutations["required"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        # Not providing the required 'value' argument should raise a GraphQLError
        with self.assertRaises(GraphQLError):
            mutation.mutate(None, Info)

    def test_list_argument_runtime_empty(self):
        @graph_ql_mutation()
        def total_list(info, items: List[int]) -> int:
            _ = info
            return sum(items)

        mutation = GraphQL._mutations["totalList"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, items=[])
        self.assertTrue(res.success)
        self.assertTrue(hasattr(res, "int"))
        self.assertEqual(res.int, 0)

    def test_optional_general_manager_argument_defaults(self):
        @graph_ql_mutation()
        def maybe_gm(info, item: Optional[DummyGM] = None) -> str:
            _ = info
            _ = item
            return "ok"

        mutation = GraphQL._mutations["maybeGm"]
        arg = mutation._meta.arguments["item"]
        self.assertIsInstance(arg, graphene.ID)
        self.assertFalse(arg.kwargs.get("required"))
        self.assertIsNone(arg.kwargs.get("default_value"))

    def test_invalid_argument_type_raises(self):
        # Using an unsupported argument type (e.g., dict) should error at decoration time
        with self.assertRaises(TypeError):

            @graph_ql_mutation()
            def bad_arg(info, payload: dict) -> int:
                _ = info
                _ = payload
                return 0

    def test_mutation_with_three_return_types(self):
        """
        Ensure tuple with three primitive return types exposes each as a field and executes correctly.
        """

        @graph_ql_mutation()
        def multi3(info, value: int) -> tuple[int, bool, str]:
            _ = info
            return value, value > 0, ("ok" if value > 0 else "no")

        mutation = GraphQL._mutations["multi3"]
        self.assertIn("int", mutation._meta.fields)
        self.assertIn("bool", mutation._meta.fields)
        self.assertIn("str", mutation._meta.fields)

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, value=0)
        self.assertEqual(res.int, 0)
        self.assertFalse(res.bool)
        self.assertEqual(res.str, "no")

    def test_missing_parameter_type_hint_error(self):
        """Test that MissingParameterTypeHintError is raised for parameters without type hints."""
        from general_manager.api.mutation import MissingParameterTypeHintError

        with self.assertRaises(MissingParameterTypeHintError) as ctx:

            @graph_ql_mutation()
            def bad_mutation(info, param_without_hint):  # Missing type hint
                return True

        self.assertIn("param_without_hint", str(ctx.exception))
        self.assertIn("Missing type hint", str(ctx.exception))

    def test_missing_mutation_return_annotation_error(self):
        """Test that MissingMutationReturnAnnotationError is raised for mutations without return annotation."""
        from general_manager.api.mutation import MissingMutationReturnAnnotationError

        with self.assertRaises(MissingMutationReturnAnnotationError) as ctx:

            @graph_ql_mutation()
            def bad_mutation(info, value: int):  # Missing return annotation
                return value

        self.assertIn("missing return annotation", str(ctx.exception))

    def test_mutation_with_optional_parameters(self):
        """Test mutations with Optional parameters are handled correctly."""

        @graph_ql_mutation()
        def optional_param_mutation(
            info, required: int, optional: int | None = None
        ) -> bool:
            _ = info
            return required > 0 and (optional is None or optional > 0)

        mutation = GraphQL._mutations["optionalParamMutation"]

        # Check that optional parameter is not required
        args_class = mutation.Arguments
        self.assertFalse(args_class.optional.kwargs.get("required", False))

    def test_mutation_with_list_parameters(self):
        """Test mutations with List parameters."""

        @graph_ql_mutation()
        def list_param_mutation(info, values: list[int]) -> int:
            _ = info
            return sum(values)

        mutation = GraphQL._mutations["listParamMutation"]

        # Check that list parameter exists
        args_class = mutation.Arguments
        self.assertTrue(hasattr(args_class, "values"))

    def test_mutation_with_default_values(self):
        """Test mutations with default parameter values."""

        @graph_ql_mutation()
        def default_value_mutation(info, multiplier: int = 2) -> int:
            _ = info
            return multiplier * 10

        mutation = GraphQL._mutations["defaultValueMutation"]

        # Check that parameter has default
        args_class = mutation.Arguments
        self.assertTrue(args_class.multiplier.kwargs.get("default_value") == 2)

    def test_mutation_error_handling(self):
        """Test that mutations properly handle and report errors."""

        @graph_ql_mutation()
        def error_mutation(info, should_fail: bool) -> str:
            _ = info
            if should_fail:
                raise PublicGraphQLError(  # noqa: TRY003
                    "Expected error",
                    code="EXPECTED_ERROR",
                )
            return "success"

        mutation = GraphQL._mutations["errorMutation"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        # Should fail
        with self.assertRaises(GraphQLError) as caught:
            mutation.mutate(None, Info, should_fail=True)
        self.assertEqual(caught.exception.message, "Expected error")
        self.assertEqual(caught.exception.extensions, {"code": "EXPECTED_ERROR"})

        # Should succeed when no error
        result = mutation.mutate(None, Info, should_fail=False)
        self.assertTrue(result.success)
        self.assertEqual(result.str, "success")

    def test_mutation_unexpected_exception_is_sanitized(self):
        """Decorator-generated mutations hide arbitrary internal exceptions."""
        error_id = "0123456789abcdef0123456789abcdef"
        private_message = "filesystem path=/secret"

        @graph_ql_mutation()
        def unexpected_exception_mutation(info) -> str:
            _ = info
            raise OSError(private_message)

        mutation = GraphQL._mutations["unexpectedExceptionMutation"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        with (
            mock.patch("general_manager.api.graphql_errors.uuid4") as uuid4_mock,
            self.assertRaises(GraphQLError) as caught,
        ):
            uuid4_mock.return_value.hex = error_id
            mutation.mutate(None, Info)

        self.assertEqual(caught.exception.message, "An internal server error occurred.")
        self.assertEqual(
            caught.exception.extensions,
            {"code": "INTERNAL_SERVER_ERROR", "errorId": error_id},
        )
        self.assertNotIn(private_message, str(caught.exception.formatted))

    def test_mutation_exception_rendering_failure_is_sanitized_by_schema(self):
        """Graphene execution cannot expose errors raised while rendering errors."""
        error_id = "0123456789abcdef0123456789abcdef"
        primary_secret = "primary secret"  # noqa: S105
        secondary_secret = "secondary secret"  # noqa: S105

        class UnrenderableError(Exception):
            def __str__(self) -> str:
                raise OSError(secondary_secret)

        @graph_ql_mutation()
        def exception_rendering_mutation(info) -> str:
            _ = info
            raise UnrenderableError(primary_secret)

        mutation = GraphQL._mutations["exceptionRenderingMutation"]

        class Query(graphene.ObjectType):
            ready = graphene.Boolean(default_value=True)

        mutation_root = type(
            "Mutation",
            (graphene.ObjectType,),
            {"exceptionRenderingMutation": mutation.Field()},
        )
        schema = graphene.Schema(query=Query, mutation=mutation_root)

        with mock.patch("general_manager.api.graphql_errors.uuid4") as uuid4_mock:
            uuid4_mock.return_value.hex = error_id
            result = schema.execute(
                "mutation { exceptionRenderingMutation { success str } }",
                context_value=object(),
            )

        self.assertEqual(len(result.errors or []), 1)
        error = result.errors[0]
        self.assertEqual(error.message, "An internal server error occurred.")
        self.assertEqual(
            error.extensions,
            {"code": "INTERNAL_SERVER_ERROR", "errorId": error_id},
        )
        self.assertNotIn(primary_secret, str(error.formatted))
        self.assertNotIn(secondary_secret, str(error.formatted))

    def test_mutation_with_permission_class(self):
        """Test mutations with custom permission classes."""
        from general_manager.permission.mutation_permission import MutationPermission

        class CustomPermission(MutationPermission):
            @classmethod
            def check(cls, data: dict, user: object) -> None:
                if data.get("value", 0) < 0:
                    raise PermissionError("Value must be non-negative")  # noqa: TRY003

        @graph_ql_mutation(permission=CustomPermission)
        def protected_mutation(info, value: int) -> int:
            _ = info
            return value * 2

        mutation = GraphQL._mutations["protectedMutation"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        # Should fail with negative value
        with self.assertRaises(GraphQLError):
            mutation.mutate(None, Info, value=-5)

        # Should succeed with positive value
        result = mutation.mutate(None, Info, value=5)
        self.assertTrue(result.success)
        self.assertEqual(result.int, 10)

    def test_mutation_with_tuple_unpacking_duplicate_names_raises(self):
        """Enforce that duplicate output field names trigger an error."""
        from general_manager.api.mutation import DuplicateMutationOutputNameError

        with self.assertRaises(DuplicateMutationOutputNameError):

            @graph_ql_mutation()
            def tuple_mutation(info, a: int, b: int) -> tuple[int, int, int]:
                _ = info
                return a + b, a - b, a * b

        self.assertNotIn("tupleMutation", GraphQL._mutations)

    def test_mutation_with_tuple_unpacking_with_custom_names(self):
        """Test that tuple returns are properly unpacked into mutation fields."""

        @graph_ql_mutation()
        def tuple_mutation(info, a: int, b: int) -> tuple[int_1, int_2, int_3]:
            _ = info
            return a + b, a - b, a * b

        mutation = GraphQL._mutations["tupleMutation"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        result = mutation.mutate(None, Info, a=10, b=5)
        self.assertTrue(result.success)
        self.assertEqual(result.int_1, 15)  # First tuple element
        self.assertEqual(result.int_2, 5)  # Second tuple element
        self.assertEqual(result.int_3, 50)  # Third tuple element

    def test_mutation_info_parameter_skipping(self):
        """Test that 'info' parameter is correctly skipped in Arguments."""

        @graph_ql_mutation()
        def info_mutation(info, value: int) -> int:
            # info should be passed but not in Arguments
            return value

        mutation = GraphQL._mutations["infoMutation"]

        # Arguments should not include 'info'
        args_class = mutation.Arguments
        self.assertFalse(hasattr(args_class, "info"))
        self.assertTrue(hasattr(args_class, "value"))

    def test_mutation_with_manager_type_parameter(self):
        """Test mutations that accept GeneralManager types as parameters."""

        @graph_ql_mutation()
        def manager_mutation(info, item: DummyGM) -> str:
            _ = info
            return item.name

        mutation = GraphQL._mutations["managerMutation"]
        arg = mutation._meta.arguments["item"]
        self.assertIsInstance(arg, graphene.ID)

    def test_mutation_graphql_type_resolution(self):
        """Test that mutations properly resolve Python types to GraphQL types."""

        @graph_ql_mutation()
        def type_resolution_mutation(
            info,
            int_val: int,
            float_val: float,
            str_val: str,
            bool_val: bool,
        ) -> bool:
            _ = info, int_val, float_val, str_val
            return bool_val

        mutation = GraphQL._mutations["typeResolutionMutation"]

        # All parameters should be properly typed
        args_class = mutation.Arguments
        self.assertTrue(hasattr(args_class, "int_val"))
        self.assertTrue(hasattr(args_class, "float_val"))
        self.assertTrue(hasattr(args_class, "str_val"))
        self.assertTrue(hasattr(args_class, "bool_val"))


def test_mutation_permission_empty_lists_allow() -> None:
    """Empty mutation permissions should behave like allow-all."""

    class AllowEmptyPermission(MutationPermission):
        __mutate__: ClassVar[List[str]] = []

    assert AllowEmptyPermission.check({"value": 1}, AnonymousUser()) is None


def test_mutation_permission_global_gate_allows_fields_without_specific_gate() -> None:
    """Fields without a field-specific list should use the global gate alone."""

    class GlobalOnlyPermission(MutationPermission):
        __mutate__: ClassVar[List[str]] = ["public"]

    assert GlobalOnlyPermission.check({"value": 1}, AnonymousUser()) is None


def test_mutation_permission_empty_payload_checks_global_gate() -> None:
    """Empty payloads still need the class-level mutation gate."""

    class DenyEmptyPayloadPermission(MutationPermission):
        __mutate__: ClassVar[List[str]] = ["isAdmin"]

    try:
        DenyEmptyPayloadPermission.check({}, AnonymousUser())
    except PermissionCheckError as exc:
        assert "Mutation permission denied for attribute '__mutate__'" in str(exc)
    else:
        raise AssertionError


def test_sequence_argument_treats_text_as_scalar() -> None:
    assert _sequence_argument("abc") == ("abc",)
    assert _sequence_argument(b"abc") == (b"abc",)
    assert _sequence_argument(bytearray(b"abc")) == (bytearray(b"abc"),)


def test_normalize_mutation_kwargs_maps_canonical_relation_inputs() -> None:
    class Owner(GeneralManager):
        pass

    class Member(GeneralManager):
        pass

    class Project:
        class Interface:
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {
                    "owner": {
                        "type": Owner,
                        "is_derived": False,
                    },
                    "owner_id": {
                        "type": int,
                        "is_derived": False,
                    },
                    "member_list": {
                        "type": Member,
                        "is_derived": False,
                    },
                }

    normalized = _normalize_mutation_kwargs_for_manager(
        Project,
        {"owner": "1", "member_list": ["2"]},
    )

    assert normalized == {"owner_id": "1", "member_id_list": ["2"]}


def test_mutation_permission_missing_mutate_denies() -> None:
    """Omitting __mutate__ should deny the default mutation gate."""

    class MissingMutatePermission(MutationPermission):
        pass

    try:
        MissingMutatePermission.check({"value": 1}, AnonymousUser())
    except PermissionCheckError as exc:
        assert "Mutation permission denied for attribute 'value'" in str(exc)
    else:
        raise AssertionError


def test_mutation_permission_invalid_mutate_shape_denies() -> None:
    """Invalid __mutate__ declarations should deny the global gate."""

    class StringMutatePermission(MutationPermission):
        __mutate__: ClassVar[object] = "public"

    class MixedMutatePermission(MutationPermission):
        __mutate__: ClassVar[list[object]] = ["public", object()]

    for permission_cls in (StringMutatePermission, MixedMutatePermission):
        try:
            permission_cls.check({"value": 1}, AnonymousUser())
        except PermissionCheckError as exc:
            assert "Mutation permission denied for attribute 'value'" in str(exc)
        else:
            raise AssertionError


def test_mutation_permission_ignores_non_list_public_attributes() -> None:
    """Only list[str] class attributes should be treated as field permissions."""

    class MixedPermission(MutationPermission):
        __mutate__: ClassVar[List[str]] = []
        field: ClassVar[List[str]] = ["public"]
        helper_constant: ClassVar[str] = "not-a-permission-list"
        mixed_list: ClassVar[list[object]] = ["public", object()]
        tuple_permissions: ClassVar[tuple[str, ...]] = ("public",)

    permission = MixedPermission({"field": "value"}, User())

    assert "field" in permission._MutationPermission__attribute_permissions
    assert (
        "helper_constant" not in permission._MutationPermission__attribute_permissions
    )
    assert "mixed_list" not in permission._MutationPermission__attribute_permissions
    assert (
        "tuple_permissions" not in permission._MutationPermission__attribute_permissions
    )


def test_mutation_permission_inherits_mutate_but_not_field_permissions() -> None:
    """Global __mutate__ is inherited, but field permissions are concrete only."""

    class ParentPermission(MutationPermission):
        __mutate__: ClassVar[List[str]] = []
        field: ClassVar[List[str]] = ["public"]

    class ChildPermission(ParentPermission):
        pass

    permission = ChildPermission({"field": "value"}, User())

    assert permission.check_permission("field")
    assert "field" not in permission._MutationPermission__attribute_permissions
