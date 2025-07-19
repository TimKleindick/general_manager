from typing import Optional, List

import graphene
from django.test import TestCase

from general_manager.api.mutation import graphQlMutation
from general_manager.api.graphql import GraphQL
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.permission.mutationPermission import MutationPermission


type test123 = str


class DummyInterface(InterfaceBase):
    input_fields = {}

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
        def pre(name, attrs, interface):
            return attrs, interface, None

        def post(new_class, interface_cls, model):
            pass

        return pre, post

    @classmethod
    def getFieldType(cls, field_name: str):
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
                return "x"

    def test_missing_return_annotation(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value: int):
                return "x"

    def test_invalid_return_type(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value: int) -> List[str]:
                return []

    def test_optional_argument_defaults(self):
        @graphQlMutation()
        def opt(info, value: Optional[int] = None) -> int:
            return value or 0

        mutation = GraphQL._mutations["opt"]
        arg = mutation._meta.arguments["value"]
        self.assertFalse(arg.kwargs.get("required"))
        self.assertIsNone(arg.kwargs.get("default_value"))

    def test_general_manager_argument_uses_id(self):
        @graphQlMutation()
        def gm(info, item: DummyGM) -> str:
            return "ok"

        mutation = GraphQL._mutations["gm"]
        arg = mutation._meta.arguments["item"]
        self.assertIsInstance(arg, graphene.ID)

    def test_list_argument(self):
        @graphQlMutation()
        def many(info, items: List[int]) -> int:
            return sum(items)

        mutation = GraphQL._mutations["many"]
        arg = mutation._meta.arguments["items"]
        self.assertIsInstance(arg, graphene.List)
        self.assertEqual(arg.of_type, graphene.Int)

    def test_mutation_with_multiple_return_types(self):
        @graphQlMutation()
        def multi(info, value: int) -> tuple[bool, str]:
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
            __mutate__ = ["isAuthenticated"]

        @graphQlMutation(addPermission)
        def add(info, a: int, b: int) -> int:
            return a + b

        mutation = GraphQL._mutations["add"]

        InfoNoAuth = type("Info", (), {"context": type("Ctx", (), {"user": None})()})
        with self.assertRaises(PermissionError):
            mutation.mutate(None, InfoNoAuth, a=1, b=2)

    def test_mutation_with_manager_return(self):
        @graphQlMutation()
        def create_item(info, name: str) -> DummyGM:
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
            return value

        mutation = GraphQL._mutations["customType"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, value="Hello")
        self.assertTrue(res.success)
        self.assertIsInstance(res.test123, str)
        self.assertEqual(res.test123, "Hello")
