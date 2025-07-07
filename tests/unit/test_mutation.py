import inspect
from typing import Optional, List

import graphene
from django.test import TestCase

from general_manager.api.mutation import graphQlMutation
from general_manager.api.graphql import GraphQL
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.baseInterface import InterfaceBase


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
        pass

    @classmethod
    def exclude(cls, **kwargs):
        pass

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
    class Interface(DummyInterface):
        pass


class MutationDecoratorTests(TestCase):
    def tearDown(self) -> None:
        GraphQL._mutations.clear()

    def test_missing_parameter_hint(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value) -> str:  # type: ignore
                return "x"

    def test_missing_return_annotation(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value: int):  # type: ignore
                return "x"

    def test_invalid_return_type(self):
        with self.assertRaises(TypeError):

            @graphQlMutation()
            def bad(info, value: int) -> List[str]:  # type: ignore
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

    def test_mutation_execution_and_auth(self):
        @graphQlMutation(auth_required=True)
        def add(info, a: int, b: int) -> int:
            return a + b

        mutation = GraphQL._mutations["add"]
        Info = type("Info", (), {"context": type("Ctx", (), {"user": object()})()})
        res = mutation.mutate(None, Info, a=1, b=2)
        self.assertTrue(res.success)
        self.assertEqual(res.int, 3)

        InfoNoAuth = type("Info", (), {"context": type("Ctx", (), {"user": None})()})
        res = mutation.mutate(None, InfoNoAuth, a=1, b=2)
        self.assertFalse(res.success)
        self.assertEqual(res.errors[0], "Authentication required")
