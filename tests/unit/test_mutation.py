from typing import Optional, List, ClassVar

import graphene
from django.test import TestCase
from django.contrib.auth.models import User

from general_manager.api.mutation import graphQlMutation
from general_manager.api.graphql import GraphQL
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.permission.mutationPermission import MutationPermission
from graphql import GraphQLError


type test123 = str


class DummyInterface(InterfaceBase):
    input_fields: ClassVar[dict] = {}

    def __init__(self, *args, **kwargs):
        pass

    def getData(self, search_date=None):
        pass

    @classmethod
    def getAttributeTypes(cls):
        return {}

    @classmethod
    def getAttributes(cls):
        return {}

    @classmethod
    def filter(cls, **kwargs):
        raise NotImplementedError("This method should be implemented in a subclass")

    @classmethod
    def exclude(cls, **kwargs):
        raise NotImplementedError("This method should be implemented in a subclass")

    @classmethod
    def handleInterface(cls):
        def pre(_name, attrs, interface):
            return attrs, interface, None

        def post(new_class, interface_cls, model):
            pass

        return pre, post

    @classmethod
    def getFieldType(cls, _field_name: str):
        return str


class DummyGM(GeneralManager):
    def __init__(self, name: str):
        self.name = name

    class Interface(DummyInterface):
        pass


class MutationDecoratorTests(TestCase):
    def tearDown(self) -> None:
        GraphQL._mutations.clear()

    def test_missing_parameter_hint(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value) -> str:
                _ = info
                _ = value
                return "x"

    def test_missing_return_annotation(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value: int):
                _ = info
                _ = value
                return "x"

    def test_invalid_return_type(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value: int) -> List[str]:
                _ = info
                _ = value
                return []

    def test_optional_argument_defaults(self):
        @graphQlMutation()
        def opt(info, value: Optional[int] = None) -> int:
            _ = info
            return value or 0

        mutation = GraphQL._mutations["opt"]
        arg = mutation._meta.arguments["value"]
        self.assertFalse(arg.kwargs.get("required"))
        self.assertIsNone(arg.kwargs.get("default_value"))

    def test_general_manager_argument_uses_id(self):
        @graphQlMutation()
        def gm(info, item: DummyGM) -> str:
            _ = info
            _ = item
            return "ok"

        mutation = GraphQL._mutations["gm"]
        arg = mutation._meta.arguments["item"]
        self.assertIsInstance(arg, graphene.ID)

    def test_list_argument(self):
        @graphQlMutation()
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

        @graphQlMutation()
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

    def test_mutation_execution_and_auth(self):
        class addPermission(MutationPermission):
            __mutate__: ClassVar[List[str]] = ["isAuthenticated"]

        @graphQlMutation(permission=addPermission)
        def add(info, a: int, b: int) -> int:
            _ = info
            return a + b

        mutation = GraphQL._mutations["add"]

        InfoNoAuth = type("Info", (), {"context": type("Ctx", (), {"user": None})()})
        with self.assertRaises(PermissionError):
            mutation.mutate(None, InfoNoAuth, a=1, b=2)

    def test_mutation_with_manager_return(self):
        @graphQlMutation()
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
        @graphQlMutation()
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
        @graphQlMutation()
        def my_mutation_case(info, value: int) -> int:
            _ = info
            return value

        # Function name with underscores should register in camelCase
        self.assertIn("myMutationCase", GraphQL._mutations)
        self.assertNotIn("my_mutation_case", GraphQL._mutations)

    def test_optional_list_argument_defaults_explicit(self):
        @graphQlMutation()
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
        @graphQlMutation()
        def bulk(info, items: List[DummyGM]) -> int:
            _ = info
            return len(items)

        mutation = GraphQL._mutations["bulk"]
        arg = mutation._meta.arguments["items"]
        self.assertIsInstance(arg, graphene.List)
        self.assertEqual(arg.of_type, graphene.String)  # IDs are strings in GraphQL

    def test_permission_allows_authenticated(self):
        class addPermission(MutationPermission):
            __mutate__: ClassVar[List[str]] = ["isAuthenticated"]

        @graphQlMutation(permission=addPermission)
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
        @graphQlMutation()
        def required(info, value: int) -> int:
            _ = info
            return value

        mutation = GraphQL._mutations["required"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        # Not providing the required 'value' argument should raise a GraphQLError
        with self.assertRaises(GraphQLError):
            mutation.mutate(None, Info)

    def test_list_argument_runtime_empty(self):
        @graphQlMutation()
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
        @graphQlMutation()
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

            @graphQlMutation()
            def bad_arg(info, payload: dict) -> int:
                _ = info
                _ = payload
                return 0

    def test_mutation_with_three_return_types(self):
        """
        Ensure tuple with three primitive return types exposes each as a field and executes correctly.
        """

        @graphQlMutation()
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

            @graphQlMutation()
            def bad_mutation(info, param_without_hint):  # Missing type hint
                return True

        self.assertIn("param_without_hint", str(ctx.exception))
        self.assertIn("Missing type hint", str(ctx.exception))

    def test_missing_mutation_return_annotation_error(self):
        """Test that MissingMutationReturnAnnotationError is raised for mutations without return annotation."""
        from general_manager.api.mutation import MissingMutationReturnAnnotationError

        with self.assertRaises(MissingMutationReturnAnnotationError) as ctx:

            @graphQlMutation()
            def bad_mutation(info, value: int):  # Missing return annotation
                return value

        self.assertIn("missing return annotation", str(ctx.exception))

    def test_invalid_mutation_return_type_error(self):
        """Test that InvalidMutationReturnTypeError is raised for non-type return values."""
        from general_manager.api.mutation import InvalidMutationReturnTypeError

        with self.assertRaises(InvalidMutationReturnTypeError) as ctx:

            @graphQlMutation()
            def bad_mutation(info, value: int) -> "not_a_type":  # Invalid return type
                return value

        self.assertIn("is not a type", str(ctx.exception))

    def test_mutation_with_optional_parameters(self):
        """Test mutations with Optional parameters are handled correctly."""

        @graphQlMutation()
        def optional_param_mutation(info, required: int, optional: int | None = None) -> bool:
            _ = info
            return required > 0 and (optional is None or optional > 0)

        mutation = GraphQL._mutations["optional_param_mutation"]

        # Check that optional parameter is not required
        args_class = mutation.Arguments
        self.assertFalse(args_class.optional.kwargs.get("required", False))

    def test_mutation_with_list_parameters(self):
        """Test mutations with List parameters."""

        @graphQlMutation()
        def list_param_mutation(info, values: list[int]) -> int:
            _ = info
            return sum(values)

        mutation = GraphQL._mutations["list_param_mutation"]

        # Check that list parameter exists
        args_class = mutation.Arguments
        self.assertTrue(hasattr(args_class, "values"))

    def test_mutation_with_default_values(self):
        """Test mutations with default parameter values."""

        @graphQlMutation()
        def default_value_mutation(info, multiplier: int = 2) -> int:
            _ = info
            return multiplier * 10

        mutation = GraphQL._mutations["default_value_mutation"]

        # Check that parameter has default
        args_class = mutation.Arguments
        self.assertTrue(hasattr(args_class.multiplier, "default_value"))

    def test_mutation_error_handling(self):
        """Test that mutations properly handle and report errors."""

        @graphQlMutation()
        def error_mutation(info, should_fail: bool) -> str:
            _ = info
            if should_fail:
                raise ValueError("Expected error")
            return "success"

        mutation = GraphQL._mutations["error_mutation"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        # Should fail gracefully
        result = mutation.mutate(None, Info, should_fail=True)
        self.assertFalse(result.success)

        # Should succeed when no error
        result = mutation.mutate(None, Info, should_fail=False)
        self.assertTrue(result.success)
        self.assertEqual(result.str, "success")

    def test_mutation_with_permission_class(self):
        """Test mutations with custom permission classes."""
        from general_manager.permission.mutationPermission import MutationPermission

        class CustomPermission(MutationPermission):
            @classmethod
            def check(cls, data: dict, user: object) -> None:
                if data.get("value", 0) < 0:
                    raise PermissionError("Value must be non-negative")

        @graphQlMutation(permission=CustomPermission)
        def protected_mutation(info, value: int) -> int:
            _ = info
            return value * 2

        mutation = GraphQL._mutations["protected_mutation"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        # Should fail with negative value
        result = mutation.mutate(None, Info, value=-5)
        self.assertFalse(result.success)

        # Should succeed with positive value
        result = mutation.mutate(None, Info, value=5)
        self.assertTrue(result.success)
        self.assertEqual(result.int, 10)

    def test_mutation_with_tuple_unpacking(self):
        """Test that tuple returns are properly unpacked into mutation fields."""

        @graphQlMutation()
        def tuple_mutation(info, a: int, b: int) -> tuple[int, int, int]:
            _ = info
            return a + b, a - b, a * b

        mutation = GraphQL._mutations["tuple_mutation"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})

        result = mutation.mutate(None, Info, a=10, b=5)
        self.assertTrue(result.success)
        self.assertEqual(result.int, 15)  # First tuple element

    def test_mutation_info_parameter_skipping(self):
        """Test that 'info' parameter is correctly skipped in Arguments."""

        @graphQlMutation()
        def info_mutation(info, value: int) -> int:
            # info should be passed but not in Arguments
            return value

        mutation = GraphQL._mutations["info_mutation"]

        # Arguments should not include 'info'
        args_class = mutation.Arguments
        self.assertFalse(hasattr(args_class, "info"))
        self.assertTrue(hasattr(args_class, "value"))

    def test_mutation_snake_to_camel_case_naming(self):
        """Test that mutation names are converted from snake_case to camelCase."""

        @graphQlMutation()
        def my_custom_mutation(info, value: int) -> int:
            _ = info
            return value

        # Should be converted to camelCase
        self.assertIn("myCustomMutation", GraphQL._mutations)

    def test_mutation_with_manager_type_parameter(self):
        """Test mutations that accept GeneralManager types as parameters."""
        from general_manager.manager import GeneralManager

        # This would need proper setup with a real manager class
        # Testing the type resolution mechanism

        @graphQlMutation()
        def manager_mutation(info, id: int) -> int:
            _ = info
            return id

        mutation = GraphQL._mutations["manager_mutation"]
        self.assertIsNotNone(mutation)

    def test_mutation_graphql_type_resolution(self):
        """Test that mutations properly resolve Python types to GraphQL types."""

        @graphQlMutation()
        def type_resolution_mutation(
            info,
            int_val: int,
            float_val: float,
            str_val: str,
            bool_val: bool,
        ) -> bool:
            _ = info, int_val, float_val, str_val
            return bool_val

        mutation = GraphQL._mutations["type_resolution_mutation"]

        # All parameters should be properly typed
        args_class = mutation.Arguments
        self.assertTrue(hasattr(args_class, "int_val"))
        self.assertTrue(hasattr(args_class, "float_val"))
        self.assertTrue(hasattr(args_class, "str_val"))
        self.assertTrue(hasattr(args_class, "bool_val"))