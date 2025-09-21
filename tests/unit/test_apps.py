import sys
import types
import builtins
import importlib
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typing import ClassVar, Any

# We will import the module under test as "apps_mod" by constructing a simulated package/module layout
# consistent with the import statements inside the source.
#
# The source imports these at module level:
#   - from general_manager.manager.generalManager import GeneralManager
#   - from general_manager.manager.meta import GeneralManagerMeta
#   - from general_manager.manager.input import Input
#   - from general_manager.api.property import graphQlProperty
#   - from general_manager.api.graphql import GraphQL
#   - from graphene_django.views import GraphQLView
#   - from django.urls import path
#   - from django.core.management.base import BaseCommand
#   - and uses django.conf.settings
#
# To keep the tests hermetic and not require real Django or Graphene, we stub the necessary modules.
#

@pytest.fixture(autouse=True)
def _isolate_sys_modules(_monkeypatch):
    # Keep a clean module registry for each test; snapshot and restore a subset.
    preserved = dict(sys.modules)
    try:
        yield
    finally:
        # Restore original modules to avoid cross-test pollution
        sys.modules.clear()
        sys.modules.update(preserved)


@pytest.fixture
def stub_django(_monkeypatch):
    # Create minimal django.* module tree
    django = types.ModuleType("django")

    conf = types.ModuleType("django.conf")
    conf.settings = SimpleNamespace(
        AUTOCREATE_GRAPHQL=False,
        ROOT_URLCONF=None,  # tests will set as needed
        GRAPHQL_URL="graphql",
    )
    urls = types.ModuleType("django.urls")

    # path() can be a simple factory returning a tuple to assert against
    def fake_path(route, view, *args, **kwargs):
        return ("path", route, view, args, kwargs)

    urls.path = fake_path

    core = types.ModuleType("django.core")
    checks = types.ModuleType("django.core.checks")

    # register returns a decorator-like handle in real Django; here we just record calls
    registered_checks = []

    def register(func, tag):
        registered_checks.append((func, tag))
        return func

    checks.register = register
    checks._registered_checks = registered_checks

    management = types.ModuleType("django.core.management")
    base = types.ModuleType("django.core.management.base")

    class BaseCommand:
        def run_from_argv(self, argv):
            # original behavior stub just returns a marker
            return ("original_run_from_argv", tuple(argv))

    base.BaseCommand = BaseCommand
    management.base = base

    django.conf = conf
    django.urls = urls
    django.core = core
    django.core.checks = checks
    django.core.management = management
    django.core.management.base = base

    sys.modules["django"] = django
    sys.modules["django.conf"] = conf
    sys.modules["django.urls"] = urls
    sys.modules["django.core"] = core
    sys.modules["django.core.checks"] = checks
    sys.modules["django.core.management"] = management
    sys.modules["django.core.management.base"] = base

    return SimpleNamespace(
        django=django,
        conf=conf,
        urls=urls,
        checks=checks,
        management=management,
        base=base,
        registered_checks=registered_checks,
    )


@pytest.fixture
def stub_graphene(_monkeypatch):
    # Minimal graphene stub
    graphene = types.ModuleType("graphene")

    class ObjectType:
        pass

    class Schema:
        def __init__(self, query=None, mutation=None):
            self.query = query
            self.mutation = mutation

    graphene.ObjectType = ObjectType
    graphene.Schema = Schema

    sys.modules["graphene"] = graphene
    return graphene


@pytest.fixture
def stub_graphene_django(_monkeypatch):
    # Minimal graphene_django.views.GraphQLView stub
    graphene_django = types.ModuleType("graphene_django")
    views = types.ModuleType("graphene_django.views")

    class GraphQLView:
        @classmethod
        def as_view(cls, graphiql=True, schema=None):
            # Return an easily assertable marker
            return ("GraphQLView.as_view", graphiql, schema)

    views.GraphQLView = GraphQLView

    graphene_django.views = views
    sys.modules["graphene_django"] = graphene_django
    sys.modules["graphene_django.views"] = views
    return views.GraphQLView


@pytest.fixture
def stub_general_manager(_monkeypatch):
    # general_manager.manager.generalManager.GeneralManager (base class marker)
    gm_pkg = types.ModuleType("general_manager")
    manager_pkg = types.ModuleType("general_manager.manager")
    api_pkg = types.ModuleType("general_manager.api")
    interface_pkg = types.ModuleType("general_manager.interface")
    permission_pkg = types.ModuleType("general_manager.permission")

    class GeneralManager:  # empty base marker
        pass

    # Meta with hooks used by initializeGeneralManagerClasses
    meta_mod = types.ModuleType("general_manager.manager.meta")

    class MetaStub:
        # class-level registries that ready() reads
        read_only_classes: ClassVar[list] = []
        pending_attribute_initialization: ClassVar[list] = []
        all_classes: ClassVar[list] = []
        pending_graphql_interfaces: ClassVar[list] = []

        # called by initializeGeneralManagerClasses
        @staticmethod
        def createAtPropertiesForAttributes(attr_names, gm_class):
            MetaStub._capfa_calls.append((tuple(attr_names), gm_class))

    MetaStub._capfa_calls = []
    meta_mod.GeneralManagerMeta = MetaStub

    # Input class type marker
    input_mod = types.ModuleType("general_manager.manager.input")

    class Input:
        def __init__(self, typ):
            self.type = typ

    input_mod.Input = Input

    # property.graphQlProperty stub
    api_property_mod = types.ModuleType("general_manager.api.property")

    def graphQlProperty(func):
        return ("graphQlProperty", func)

    api_property_mod.graphQlProperty = graphQlProperty

    # api.graphql.GraphQL stub (with mutable registries)
    api_graphql_mod = types.ModuleType("general_manager.api.graphql")

    class GraphQL:
        _query_fields: ClassVar[dict] = {}
        _mutations: ClassVar[dict] = {}
        _query_class: ClassVar[Any] = None
        _mutation_class: ClassVar[Any] = None

        @staticmethod
        def createGraphqlInterface(gm_class):
            GraphQL._query_fields[gm_class.__name__] = f"{gm_class.__name__}Field"

        @staticmethod
        def createGraphqlMutation(gm_class):
            GraphQL._mutations[f"mutate{gm_class.__name__}"] = type(
                f"Mutate{gm_class.__name__}",
                (),
                {"Field": staticmethod(lambda: f"MutationField:{gm_class.__name__}")},
            )

    api_graphql_mod.GraphQL = GraphQL

    # interface.readOnlyInterface.ReadOnlyInterface base
    read_only_mod = types.ModuleType("general_manager.interface.readOnlyInterface")

    class ReadOnlyInterface:
        _model = object()

        @classmethod
        def ensureSchemaIsUpToDate(cls, manager_class, model):
            return ("ensureSchemaIsUpToDate", manager_class, model)

        @classmethod
        def getAttributes(cls):
            return {"id": int}

        @classmethod
        def syncData(cls):
            ReadOnlyInterface._synced.append(cls)

    ReadOnlyInterface._synced = []
    read_only_mod.ReadOnlyInterface = ReadOnlyInterface

    # permission base and default
    base_perm_mod = types.ModuleType("general_manager.permission.basePermission")

    class BasePermission:
        pass

    base_perm_mod.BasePermission = BasePermission
    manager_based_perm_mod = types.ModuleType("general_manager.permission.managerBasedPermission")

    class ManagerBasedPermission(BasePermission):
        pass

    manager_based_perm_mod.ManagerBasedPermission = ManagerBasedPermission

    # Attach modules into sys.modules tree
    sys.modules["general_manager"] = gm_pkg
    sys.modules["general_manager.manager"] = manager_pkg
    sys.modules["general_manager.manager.generalManager"] = types.ModuleType("general_manager.manager.generalManager")
    sys.modules["general_manager.manager.generalManager"].GeneralManager = GeneralManager
    sys.modules["general_manager.manager.meta"] = meta_mod
    sys.modules["general_manager.manager.input"] = input_mod

    sys.modules["general_manager.api"] = api_pkg
    sys.modules["general_manager.api.property"] = api_property_mod
    sys.modules["general_manager.api.graphql"] = api_graphql_mod

    sys.modules["general_manager.interface"] = interface_pkg
    sys.modules["general_manager.interface.readOnlyInterface"] = read_only_mod

    sys.modules["general_manager.permission"] = permission_pkg
    sys.modules["general_manager.permission.basePermission"] = base_perm_mod
    sys.modules["general_manager.permission.managerBasedPermission"] = manager_based_perm_mod

    return SimpleNamespace(
        GeneralManager=GeneralManager,
        GeneralManagerMeta=meta_mod.GeneralManagerMeta,
        Input=input_mod.Input,
        graphQlProperty=api_property_mod.graphQlProperty,
        GraphQL=api_graphql_mod.GraphQL,
        ReadOnlyInterface=read_only_mod.ReadOnlyInterface,
        BasePermission=base_perm_mod.BasePermission,
        ManagerBasedPermission=manager_based_perm_mod.ManagerBasedPermission,
        MetaStub=meta_mod.GeneralManagerMeta,
    )


@pytest.fixture
def apps_mod(_stub_django, _stub_graphene, _stub_graphene_django, _stub_general_manager, _monkeypatch):
    # Build a synthetic module that contains the provided source code under a stable name.
    # We'll write the source into a module string and exec it into a fresh module object.
    module_name = "general_manager.apps_under_test"
    mod = types.ModuleType(module_name)
    code = r'''
from __future__ import annotations
from django.apps import AppConfig
import graphene
import os
from django.conf import settings
from django.urls import path
from graphene_django.views import GraphQLView
from importlib import import_module
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.api.property import graphQlProperty
from general_manager.api.graphql import GraphQL
from typing import TYPE_CHECKING, Type, Any, cast, ClassVar
from django.core.checks import register
import logging
from django.core.management.base import BaseCommand

if TYPE_CHECKING:
    from general_manager.interface.readOnlyInterface import ReadOnlyInterface

logger = logging.getLogger(__name__)

class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "general_manager"

    def ready(self):
        self.handleReadOnlyInterface(GeneralManagerMeta.read_only_classes)
        self.initializeGeneralManagerClasses(
            GeneralManagerMeta.pending_attribute_initialization,
            GeneralManagerMeta.all_classes,
        )
        if getattr(settings, "AUTOCREATE_GRAPHQL", False):
            self.handleGraphQL(GeneralManagerMeta.pending_graphql_interfaces)

    @staticmethod
    def handleReadOnlyInterface(read_only_classes: list[Type[GeneralManager]],):
        GeneralmanagerConfig.patchReadOnlyInterfaceSync(read_only_classes)
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface
        logger.debug("starting to register ReadOnlyInterface schema warnings...")
        for general_manager_class in read_only_classes:
            read_only_interface = cast(Type[ReadOnlyInterface], general_manager_class.Interface)
            register(
                lambda app_configs, model=read_only_interface._model, manager_class=general_manager_class, **kwargs: ReadOnlyInterface.ensureSchemaIsUpToDate(
                    manager_class, model
                ),
                "general_manager",
            )

    @staticmethod
    def patchReadOnlyInterfaceSync(general_manager_classes: list[Type[GeneralManager]],):
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface
        original_run_from_argv = BaseCommand.run_from_argv

        def run_from_argv_with_sync(self, argv):
            run_main = os.environ.get("RUN_MAIN") == "true"
            command = argv[1] if len(argv) > 1 else None
            if command != "runserver" or run_main:
                logger.debug("start syncing ReadOnlyInterface data...")
                for general_manager_class in general_manager_classes:
                    read_only_interface = cast(Type[ReadOnlyInterface], general_manager_class.Interface)
                    read_only_interface.syncData()
                logger.debug("finished syncing ReadOnlyInterface data.")
            return original_run_from_argv(self, argv)
        BaseCommand.run_from_argv = run_from_argv_with_sync

    @staticmethod
    def initializeGeneralManagerClasses(pending_attribute_initialization: list[Type[GeneralManager]], all_classes: list[Type[GeneralManager]],):
        logger.debug("Initializing GeneralManager classes...")

        logger.debug("starting to create attributes for GeneralManager classes...")
        for general_manager_class in pending_attribute_initialization:
            attributes = general_manager_class.Interface.getAttributes()
            setattr(general_manager_class, "_attributes", attributes)
            GeneralManagerMeta.createAtPropertiesForAttributes(attributes.keys(), general_manager_class)

        logger.debug("starting to connect inputs to other general manager classes...")
        for general_manager_class in all_classes:
            attributes = getattr(general_manager_class.Interface, "input_fields", {})
            for attribute_name, attribute in attributes.items():
                if isinstance(attribute, Input) and issubclass(attribute.type, GeneralManager):
                    connected_manager = attribute.type
                    func = lambda x, attribute_name=attribute_name: general_manager_class.filter(**{attribute_name: x})
                    func.__annotations__ = {"return": general_manager_class}
                    setattr(connected_manager, f"{general_manager_class.__name__.lower()}_list", graphQlProperty(func))
        for general_manager_class in all_classes:
            GeneralmanagerConfig.checkPermissionClass(general_manager_class)

    @staticmethod
    def handleGraphQL(pending_graphql_interfaces: list[Type[GeneralManager]],):
        logger.debug("Starting to create GraphQL interfaces and mutations...")
        for general_manager_class in pending_graphql_interfaces:
            GraphQL.createGraphqlInterface(general_manager_class)
            GraphQL.createGraphqlMutation(general_manager_class)

        query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
        GraphQL._query_class = query_class

        if GraphQL._mutations:
            mutation_class = type("Mutation", (graphene.ObjectType,), {name: mutation.Field() for name, mutation in GraphQL._mutations.items()},)
            GraphQL._mutation_class = mutation_class
            schema = graphene.Schema(query=GraphQL._query_class, mutation=mutation_class,)
        else:
            GraphQL._mutation_class = None
            schema = graphene.Schema(query=GraphQL._query_class)
        GeneralmanagerConfig.addGraphqlUrl(schema)

    @staticmethod
    def addGraphqlUrl(schema):
        logging.debug("Adding GraphQL URL to Django settings...")
        root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
        graph_ql_url = getattr(settings, "GRAPHQL_URL", "graphql")
        if not root_url_conf_path:
            raise Exception("ROOT_URLCONF not found in settings")
        urlconf = import_module(root_url_conf_path)
        urlconf.urlpatterns.append(
            path(
                graph_ql_url,
                GraphQLView.as_view(graphiql=True, schema=schema),
            )
        )

    @staticmethod
    def checkPermissionClass(general_manager_class: Type[GeneralManager]):
        from general_manager.permission.basePermission import BasePermission
        from general_manager.permission.managerBasedPermission import (ManagerBasedPermission,)
        if hasattr(general_manager_class, "Permission"):
            permission = general_manager_class.Permission
            if not issubclass(permission, BasePermission):
                raise TypeError(f"{permission.__name__} must be a subclass of BasePermission")
            general_manager_class.Permission = permission
        else:
            general_manager_class.Permission = ManagerBasedPermission
'''
    exec(compile(code, module_name + ".py", "exec"), mod.__dict__)  # noqa: S102
    sys.modules[module_name] = mod
    return mod

# --------------------
# Tests start here
# --------------------

def test_handleReadOnlyInterface_registers_checks_and_patches(stub_django, stub_general_manager, apps_mod):
    GM = stub_general_manager.GeneralManager
    ROI = stub_general_manager.ReadOnlyInterface

    class A(GM):
        Interface = ROI

    class B(GM):
        Interface = ROI

    # Spy on patch method
    with patch.object(apps_mod.GeneralmanagerConfig, "patchReadOnlyInterfaceSync") as patched:
        apps_mod.GeneralmanagerConfig.handleReadOnlyInterface([A, B])
    # Patching called once with classes
    patched.assert_called_once()
    assert len(stub_django.checks._registered_checks) == 2
    # Validate the registered check callable signature returns ensureSchema marker when invoked
    func0, _tag0 = stub_django.checks._registered_checks[0]
    res = func0(app_configs=None)
    assert res[0] == "ensureSchemaIsUpToDate"
    assert res[1] in (A, B)


def test_patchReadOnlyInterfaceSync_wraps_BaseCommand_and_syncs(stub_django, stub_general_manager, apps_mod):
    GM = stub_general_manager.GeneralManager
    ROI = stub_general_manager.ReadOnlyInterface

    class C(GM):
        Interface = ROI

    class D(GM):
        Interface = ROI

    # Ensure fresh sync list
    stub_general_manager.ReadOnlyInterface._synced.clear()

    apps_mod.GeneralmanagerConfig.patchReadOnlyInterfaceSync([C, D])

    # Create instance of BaseCommand and run various argv combinations
    cmd = stub_django.base.BaseCommand()
    # 1) Non-runserver command: must sync
    out = cmd.run_from_argv(["manage.py", "migrate"])
    assert ("original_run_from_argv", ("manage.py", "migrate")) == out
    assert set(stub_general_manager.ReadOnlyInterface._synced) == {ROI}

    # Reset and test runserver child process (RUN_MAIN != 'true'): should not sync
    stub_general_manager.ReadOnlyInterface._synced.clear()
    # ensure env not set to true
    import os as _os
    _os.environ.pop("RUN_MAIN", None)
    _os.environ["RUN_MAIN"] = "false"
    _ = cmd.run_from_argv(["manage.py", "runserver"])
    assert stub_general_manager.ReadOnlyInterface._synced == []

    # Now simulate main runserver process: RUN_MAIN == 'true' -> should sync
    _os.environ["RUN_MAIN"] = "true"
    _ = cmd.run_from_argv(["manage.py", "runserver"])
    assert stub_general_manager.ReadOnlyInterface._synced == [ROI]


def test_initializeGeneralManagerClasses_sets_attributes_connects_inputs_and_checks(stub_general_manager, apps_mod, _monkeypatch):
    GM = stub_general_manager.GeneralManager
    Meta = stub_general_manager.MetaStub
    Input = stub_general_manager.Input

    class GM1(GM):
        class Interface:
            @staticmethod
            def getAttributes():
                return {"a": 1, "b": 2}

            input_fields: ClassVar[dict] = {}

        @staticmethod
        def filter(**kwargs):
            return ("GM1.filter", kwargs)

    class GM2(GM):
        class Interface:
            @staticmethod
            def getAttributes():
                return {"x": 9}

            # input_fields references GM1 to connect
            input_fields: ClassVar[dict] = {"gm1": Input(GM1)}

        @staticmethod
        def filter(**kwargs):
            return ("GM2.filter", kwargs)

    # Spy on checkPermissionClass
    with patch.object(apps_mod.GeneralmanagerConfig, "checkPermissionClass") as check_perm:
        apps_mod.GeneralmanagerConfig.initializeGeneralManagerClasses(
            pending_attribute_initialization=[GM1],
            all_classes=[GM1, GM2],
        )
    # Attributes set
    assert getattr(GM1, "_attributes", {}) == {"a": 1, "b": 2}
    # createAtPropertiesForAttributes called with keys for GM1
    assert Meta._capfa_calls and Meta._capfa_calls[-1][1] is GM1
    # connection: GM2.Interface.input_fields -> property added on GM1 class
    assert hasattr(GM1, "gm2_list")
    marker, func = GM1.gm2_list
    assert marker == "graphQlProperty"
    # function annotation and behavior
    assert getattr(func, "__annotations__", {}).get("return") is GM2
    assert GM2.filter(gm1=123) == ("GM2.filter", {"gm1": 123})
    # checkPermissionClass invoked for each class
    check_perm.assert_any_call(GM1)
    check_perm.assert_any_call(GM2)


def test_handleGraphQL_builds_schema_and_calls_add_url(stub_general_manager, _stub_graphene, apps_mod):
    class G1(stub_general_manager.GeneralManager):
        pass

    class G2(stub_general_manager.GeneralManager):
        pass

    # Ensure clean GraphQL registries
    gql = apps_mod.GraphQL
    gql._query_fields.clear()
    gql._mutations.clear()
    with patch.object(apps_mod.GeneralmanagerConfig, "addGraphqlUrl") as add_url:
        apps_mod.GeneralmanagerConfig.handleGraphQL([G1, G2])
        # After call, schema should be created and addGraphqlUrl invoked once with a Schema instance
        assert add_url.call_count == 1
        args, _kwargs = add_url.call_args
        assert len(args) == 1
        schema = args[0]
        from graphene import Schema
        assert isinstance(schema, Schema)
        # Mutations present because createGraphqlMutation populated _mutations
        assert schema.mutation is gql._mutation_class
        assert schema.query is gql._query_class


def test_addGraphqlUrl_appends_urlpattern_with_default_and_custom_url(_stub_django, _stub_graphene_django, apps_mod, _monkeypatch):
    # Default GRAPHQL_URL
    # Create a fake URLConf module
    url_mod = types.ModuleType("tests.fake_urls")
    url_mod.urlpatterns = []
    sys.modules["tests.fake_urls"] = url_mod

    # Set settings
    from django.conf import settings
    settings.ROOT_URLCONF = "tests.fake_urls"
    if hasattr(settings, "GRAPHQL_URL"):
        delattr(settings, "GRAPHQL_URL")

    schema = ("schema",)
    apps_mod.GeneralmanagerConfig.addGraphqlUrl(schema)
    assert len(url_mod.urlpatterns) == 1
    kind, route, view, _args, _kwargs = url_mod.urlpatterns[0]
    assert kind == "path"
    assert route == "graphql"
    assert view[0] == "GraphQLView.as_view"
    assert view[2] == schema  # schema passed through

    # Custom GRAPHQL_URL
    url_mod.urlpatterns.clear()
    settings.GRAPHQL_URL = "api/graphql"
    apps_mod.GeneralmanagerConfig.addGraphqlUrl(schema)
    assert url_mod.urlpatterns[0][1] == "api/graphql"


def test_addGraphqlUrl_raises_without_root_urlconf(_stub_django, apps_mod):
    from django.conf import settings
    settings.ROOT_URLCONF = None
    with pytest.raises(Exception) as ei:
        apps_mod.GeneralmanagerConfig.addGraphqlUrl(("schema",))
    assert "ROOT_URLCONF not found in settings" in str(ei.value)


def test_checkPermissionClass_sets_default_and_validates_type(stub_general_manager, apps_mod):
    GM = stub_general_manager.GeneralManager
    BasePermission = stub_general_manager.BasePermission
    ManagerBasedPermission = stub_general_manager.ManagerBasedPermission

    class P(BasePermission):
        pass

    class NotPerm:
        pass

    class M1(GM):
        pass

    class M2(GM):
        Permission = P

    class M3(GM):
        Permission = NotPerm

    # No Permission -> default set
    apps_mod.GeneralmanagerConfig.checkPermissionClass(M1)
    assert M1.Permission is ManagerBasedPermission

    # Valid Permission subclass -> preserved
    apps_mod.GeneralmanagerConfig.checkPermissionClass(M2)
    assert M2.Permission is P

    # Invalid Permission -> raises TypeError with message
    with pytest.raises(TypeError) as ei:
        apps_mod.GeneralmanagerConfig.checkPermissionClass(M3)
    assert "must be a subclass of BasePermission" in str(ei.value)


def test_ready_calls_expected_hooks(_stub_django, stub_general_manager, apps_mod, _monkeypatch):
    Meta = stub_general_manager.MetaStub
    Meta.read_only_classes = ["ro1"]
    Meta.pending_attribute_initialization = ["pai1"]
    Meta.all_classes = ["all1"]
    Meta.pending_graphql_interfaces = ["gql1"]

    cfg = apps_mod.GeneralmanagerConfig("general_manager", apps_mod)
    with patch.object(apps_mod.GeneralmanagerConfig, "handleReadOnlyInterface") as hroi, \
         patch.object(apps_mod.GeneralmanagerConfig, "initializeGeneralManagerClasses") as initc, \
         patch.object(apps_mod.GeneralmanagerConfig, "handleGraphQL") as hgql:
        # First with AUTOCREATE_GRAPHQL False
        from django.conf import settings
        settings.AUTOCREATE_GRAPHQL = False
        cfg.ready()
        hroi.assert_called_once_with(Meta.read_only_classes)
        initc.assert_called_once_with(Meta.pending_attribute_initialization, Meta.all_classes)
        hgql.assert_not_called()

        # Now with AUTOCREATE_GRAPHQL True
        hroi.reset_mock()
        initc.reset_mock()
        hgql.reset_mock()
        settings.AUTOCREATE_GRAPHQL = True
        cfg.ready()
        hgql.assert_called_once_with(Meta.pending_graphql_interfaces)