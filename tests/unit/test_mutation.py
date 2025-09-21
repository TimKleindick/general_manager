from typing import Optional, List, ClassVar

import graphene
from django.test import TestCase

from general_manager.api.mutation import graphQlMutation
from general_manager.api.graphql import GraphQL
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.permission.mutationPermission import MutationPermission


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

class MutationDecoratorAdditionalTests(TestCase):
    def tearDown(self) -> None:
        # Keep registry clean between tests
        GraphQL._mutations.clear()

    def test_required_argument_flag_for_non_optional(self):
        @graphQlMutation()
        def req(info, value: int) -> int:
            _ = info
            return value

        mutation = GraphQL._mutations["req"]
        arg = mutation._meta.arguments["value"]
        # For non-Optional args, required should be True or default to True if unset
        self.assertIn("required", getattr(arg, "kwargs", {}))
        self.assertTrue(arg.kwargs.get("required"))

    def test_optional_with_default_value_propagates_default(self):

        @graphQlMutation()
        def opt_with_default(info, value: Optional[int] = 5) -> int:
            _ = info
            return value

        mutation = GraphQL._mutations["optWithDefault"]
        arg = mutation._meta.arguments["value"]
        self.assertFalse(arg.kwargs.get("required"))
        self.assertEqual(arg.kwargs.get("default_value"), 5)

    def test_list_argument_empty_and_singleton(self):

        @graphQlMutation()
        def total(info, items: List[int]) -> int:
            _ = info
            return sum(items)

        mutation = GraphQL._mutations["total"]
        arg = mutation._meta.arguments["items"]
        # Should be a graphene.List of graphene.Int, as established by existing tests
        self.assertIsInstance(arg, graphene.List)
        self.assertEqual(arg.of_type, graphene.Int)

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res_empty = mutation.mutate(None, Info, items=[])
        self.assertTrue(res_empty.success)
        self.assertEqual(res_empty.int, 0)

        res_single = mutation.mutate(None, Info, items=[7])
        self.assertTrue(res_single.success)
        self.assertEqual(res_single.int, 7)

    def test_successful_permissioned_mutation_with_authenticated_user(self):
        class addPermission(MutationPermission):
            __mutate__: ClassVar[List[str]] = ["isAuthenticated"]


        @graphQlMutation(permission=addPermission)
        def add_ok(info, a: int, b: int) -> int:
            _ = info
            return a + b

        mutation = GraphQL._mutations["addOk"]
        InfoAuth = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        result = mutation.mutate(None, InfoAuth, a=3, b=4)
        self.assertTrue(result.success)
        self.assertEqual(result.int, 7)

    def test_primitive_argument_type_mapping(self):

        @graphQlMutation()
        def prim(info, a: int, b: str, c: bool) -> bool:
            _ = (info, a, b)
            return c

        mutation = GraphQL._mutations["prim"]

        a_arg = mutation._meta.arguments["a"]
        b_arg = mutation._meta.arguments["b"]
        c_arg = mutation._meta.arguments["c"]

        # Validate graphene types used for primitives
        self.assertIs(a_arg, graphene.Int)
        self.assertIs(b_arg, graphene.String)
        self.assertIs(c_arg, graphene.Boolean)

    def test_tuple_return_negative_branch(self):

        @graphQlMutation()
        def verdict(info, n: int) -> tuple[bool, str]:
            _ = info
            return (n % 2 == 0), ("even" if n % 2 == 0 else "odd")

        mutation = GraphQL._mutations["verdict"]
        self.assertIn("success", mutation._meta.fields)
        self.assertIn("bool", mutation._meta.fields)
        self.assertIn("str", mutation._meta.fields)

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, n=3)
        self.assertTrue(res.success)
        self.assertEqual(res.bool, False)
        self.assertEqual(res.str, "odd")

    def test_mutation_name_camel_case_registration(self):

        @graphQlMutation()
        def create_user_account(info, username: str) -> str:
            _ = info
            return username

        # Expect function name to be converted to camelCase by the decorator/registry
        self.assertIn("createUserAccount", GraphQL._mutations)
        mutation = GraphQL._mutations["createUserAccount"]

        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, username="alice")
        self.assertTrue(res.success)
        self.assertEqual(res.str, "alice")

    def test_invalid_return_type_raises_for_set(self):
        with self.assertRaises(TypeError):
            @graphQlMutation()
            def bad_ret(info, value: int) -> set[int]:
                _ = info
                return {value}

    def test_error_when_missing_info_parameter(self):
        # The decorator should enforce an 'info' first parameter; if not, it should raise TypeError
        with self.assertRaises(TypeError):
            @graphQlMutation()
            def missing_info(x: int) -> int:
                return x

    def test_general_manager_argument_rejects_non_id_value(self):
        @graphQlMutation()
        def gm2(info, item: DummyGM) -> str:
            _ = info
            _ = item
            return "ok"

        mutation = GraphQL._mutations["gm2"]
        arg = mutation._meta.arguments["item"]
        self.assertIsInstance(arg, graphene.ID)

        # Passing a non-ID-like object should raise a TypeError during input processing if validated
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        with self.assertRaises((TypeError, ValueError, AssertionError)):
            mutation.mutate(None, Info, item=object())

    def test_custom_type_alias_roundtrip_multiple_values(self):

        @graphQlMutation()
        def echo_pair(info, left: str, right: str) -> tuple[test123, test123]:
            _ = info
            return left, right

        mutation = GraphQL._mutations["echoPair"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, left="L", right="R")
        self.assertTrue(res.success)
        self.assertEqual(res.str, "L")   # first element mapped to 'str'
        self.assertEqual(res.str_2, "R") # second element should disambiguate with suffix