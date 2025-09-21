# NOTE: Testing library/framework: pytest + pytest-django (function-style tests using fixtures).
# These tests validate general_manager.apps.GeneralmanagerConfig behavior across initialization,
# GraphQL setup, URL registration, permission checks, and read-only sync handling.

from __future__ import annotations

import os
import sys
import types
import importlib
import pytest
from typing import ClassVar

from django.conf import settings as dj_settings

# Helper: reload target module with optional stubs pre-inserted
def load_apps_module(_):
    if "general_manager.apps" in sys.modules:
        del sys.modules["general_manager.apps"]
    mod = importlib.import_module("general_manager.apps")
    importlib.reload(mod)
    return mod

@pytest.fixture
def dummy_gm_classes():
    # Minimal GeneralManager hierarchy and Interfaces
    class FakeGMBase:  # sentinel that stands in for GeneralManager base
        pass

    class InterfaceA:
        _model = object()
        @staticmethod
        def getAttributes():
            return {"foo": 1, "bar": 2}
        input_fields: ClassVar = {}
        @staticmethod
        def syncData():
            InterfaceA.synced = True

    class InterfaceB:
        _model = object()
        @staticmethod
        def getAttributes():
            return {"baz": 3}
        input_fields: ClassVar = {}
        @staticmethod
        def syncData():
            InterfaceB.synced = True

    class GM1(FakeGMBase):
        Interface = InterfaceA
        @classmethod
        def filter(cls, **kw):
            return ("GM1.filter", kw)

    class GM2(FakeGMBase):
        Interface = InterfaceB
        @classmethod
        def filter(cls, **kw):
            return ("GM2.filter", kw)

    return FakeGMBase, GM1, GM2, InterfaceA, InterfaceB

@pytest.fixture
def fake_input_cls():
    class Input:
        def __init__(self, type):
            self.type = type
    return Input

@pytest.fixture
def patch_general_manager_meta(monkeypatch, dummy_gm_classes, fake_input_cls):
    FakeGMBase, GM1, GM2, _, _ = dummy_gm_classes
    Input = fake_input_cls

    # Trigger linking: GM1 references GM2 via Input
    GM1.Interface.input_fields = {"linked": Input(type=GM2)}
    GM2.Interface.input_fields = {}

    gm_meta = types.SimpleNamespace(
        read_only_classes=[GM1, GM2],
        pending_attribute_initialization=[GM1, GM2],
        all_classes=[GM1, GM2],
        pending_graphql_interfaces=[GM1, GM2],
        createAtPropertiesForAttributes=lambda keys, cls: setattr(
            cls, "_created_for", (tuple(sorted(list(keys))), cls.__name__)
        ),
    )

    # Patch imports used inside apps.py
    monkeypatch.setitem(
        sys.modules,
        "general_manager.manager.generalManager",
        types.SimpleNamespace(GeneralManager=FakeGMBase),
    )
    monkeypatch.setitem(
        sys.modules,
        "general_manager.manager.meta",
        types.SimpleNamespace(GeneralManagerMeta=gm_meta),
    )
    monkeypatch.setitem(
        sys.modules,
        "general_manager.manager.input",
        types.SimpleNamespace(Input=Input),
    )
    return gm_meta, FakeGMBase, GM1, GM2

@pytest.fixture
def patch_graphql_stack(monkeypatch):
    # GraphQL core stub
    class GQL:
        _query_fields: ClassVar = {"ping": lambda: "pong"}
        _mutations: ClassVar = {}
        _query_class = None
        _mutation_class = None

        @staticmethod
        def createGraphqlInterface(cls):
            GQL._query_fields[cls.__name__.lower()] = lambda: cls.__name__

        @staticmethod
        def createGraphqlMutation(cls):
            class M:
                @staticmethod
                def Field():
                    return f"Field({cls.__name__})"
            GQL._mutations[cls.__name__.lower()] = M

    # property stub returns original function for easy validation
    def graphQlProperty(func):
        return func

    # graphene minimal stub
    class ObjectType:
        pass
    def Schema(**kw):
        return ("Schema", kw)

    # GraphQLView.as_view stub
    class _V:
        @staticmethod
        def as_view(**kw):
            return ("GraphQLView.as_view", kw)

    monkeypatch.setitem(sys.modules, "general_manager.api.graphql", types.SimpleNamespace(GraphQL=GQL))
    monkeypatch.setitem(sys.modules, "general_manager.api.property", types.SimpleNamespace(graphQlProperty=graphQlProperty))
    monkeypatch.setitem(sys.modules, "graphene", types.SimpleNamespace(ObjectType=ObjectType, Schema=Schema))
    monkeypatch.setitem(sys.modules, "graphene_django.views", types.SimpleNamespace(GraphQLView=_V))

def test_ready_calls_components(monkeypatch, settings, patch_general_manager_meta):
    gm_meta, *_ = patch_general_manager_meta
    mod = load_apps_module(monkeypatch)

    calls = {}
    monkeypatch.setattr(mod.GeneralmanagerConfig, "handleReadOnlyInterface", staticmethod(lambda arg: calls.setdefault("roi", arg)))
    monkeypatch.setattr(mod.GeneralmanagerConfig, "initializeGeneralManagerClasses", staticmethod(lambda a, b: calls.setdefault("init", (a, b))))
    monkeypatch.setattr(mod.GeneralmanagerConfig, "handleGraphQL", staticmethod(lambda arg: calls.setdefault("gql", arg)))

    settings.AUTOCREATE_GRAPHQL = False
    mod.GeneralmanagerConfig().ready()
    assert calls["roi"] == gm_meta.read_only_classes
    assert calls["init"] == (gm_meta.pending_attribute_initialization, gm_meta.all_classes)
    assert "gql" not in calls

    settings.AUTOCREATE_GRAPHQL = True
    calls.clear()
    mod.GeneralmanagerConfig().ready()
    assert calls["gql"] == gm_meta.pending_graphql_interfaces

def test_handleReadOnlyInterface_registers_checks_and_patches(monkeypatch, patch_general_manager_meta):
    gm_meta, *_ = patch_general_manager_meta

    # Provide ReadOnlyInterface stub BEFORE calling handleReadOnlyInterface
    called = []
    roi_mod = types.SimpleNamespace(
        ReadOnlyInterface=types.SimpleNamespace(
            ensureSchemaIsUpToDate=staticmethod(lambda manager_class, model: (called.append((manager_class, model)) or []))
        )
    )
    monkeypatch.setitem(sys.modules, "general_manager.interface.readOnlyInterface", roi_mod)

    mod = load_apps_module(monkeypatch)

    patched = {}
    monkeypatch.setattr(mod.GeneralmanagerConfig, "patchReadOnlyInterfaceSync", staticmethod(lambda ros: patched.setdefault("called_with", tuple(ros))))
    registered = []
    monkeypatch.setattr(mod, "register", lambda func, tag: registered.append((func, tag)))

    mod.GeneralmanagerConfig.handleReadOnlyInterface(gm_meta.read_only_classes)
    assert patched["called_with"] == tuple(gm_meta.read_only_classes)
    assert len(registered) == 2
    for func, tag in registered:
        assert tag == "general_manager"
        func(None)
    assert {m.__name__ for (m, _model) in called} == {"GM1", "GM2"}

def test_patchReadOnlyInterfaceSync_syncs_on_non_runserver_and_real_run(monkeypatch, patch_general_manager_meta):
    gm_meta, _, GM1, GM2 = patch_general_manager_meta

    # ReadOnlyInterface import stub (not used directly by code path, but keep for safety)
    monkeypatch.setitem(
        sys.modules,
        "general_manager.interface.readOnlyInterface",
        types.SimpleNamespace(ReadOnlyInterface=types.SimpleNamespace()),
    )

    # Prepare module and wrap BaseCommand.run_from_argv
    from django.core.management.base import BaseCommand
    mod = load_apps_module(monkeypatch)

    def fake_run(_, __):
        return "ok"
    monkeypatch.setattr(BaseCommand, "run_from_argv", fake_run, raising=False)

    # Apply patch (wraps BaseCommand.run_from_argv)
    mod.GeneralmanagerConfig.patchReadOnlyInterfaceSync(gm_meta.read_only_classes)

    # 1) Non-runserver -> should sync
    os.environ.pop("RUN_MAIN", None)
    GM1.Interface.synced = False
    GM2.Interface.synced = False
    assert BaseCommand.run_from_argv(BaseCommand(), ["manage.py", "migrate"]) == "ok"
    assert GM1.Interface.synced and GM2.Interface.synced

    # 2) runserver with RUN_MAIN \!= "true" -> no sync
    GM1.Interface.synced = False
    GM2.Interface.synced = False
    os.environ["RUN_MAIN"] = "false"
    BaseCommand.run_from_argv(BaseCommand(), ["manage.py", "runserver"])
    assert not GM1.Interface.synced and not GM2.Interface.synced

    # 3) runserver with RUN_MAIN == "true" -> sync
    os.environ["RUN_MAIN"] = "true"
    BaseCommand.run_from_argv(BaseCommand(), ["manage.py", "runserver"])
    assert GM1.Interface.synced and GM2.Interface.synced

def test_initializeGeneralManagerClasses_sets_attributes_links_and_checks(monkeypatch, patch_general_manager_meta):
    gm_meta, _, GM1, GM2 = patch_general_manager_meta
    mod = load_apps_module(monkeypatch)

    checked = []
    monkeypatch.setattr(mod.GeneralmanagerConfig, "checkPermissionClass", staticmethod(lambda cls: checked.append(cls)))

    mod.GeneralmanagerConfig.initializeGeneralManagerClasses(
        gm_meta.pending_attribute_initialization, gm_meta.all_classes
    )

    assert GM1._attributes == {"foo": 1, "bar": 2}
    assert GM2._attributes == {"baz": 3}
    assert GM1._created_for[0] == ("bar", "foo")
    assert GM2._created_for[0] == ("baz",)

    assert hasattr(GM2, "gm1_list")
    func = GM2.gm1_list
    assert func("X") == ("GM1.filter", {"linked": "X"})
    assert getattr(func, "__annotations__", {}).get("return") is GM1

    assert checked == [GM1, GM2]

def test_handleGraphQL_builds_schema_and_registers_url(monkeypatch, patch_general_manager_meta):
    gm_meta, *_ = patch_general_manager_meta
    mod = load_apps_module(monkeypatch)

    captured = {}
    monkeypatch.setattr(mod.GeneralmanagerConfig, "addGraphqlUrl", staticmethod(lambda schema: captured.setdefault("schema", schema)))

    # With mutations
    mod.GeneralmanagerConfig.handleGraphQL(gm_meta.pending_graphql_interfaces)
    assert captured["schema"][0] == "Schema"
    from general_manager.api.graphql import GraphQL as GQL
    assert isinstance(GQL._query_class, type)
    assert isinstance(GQL._mutation_class, type)

    # Without mutations
    GQL._mutations = {}
    captured.clear()
    mod.GeneralmanagerConfig.handleGraphQL([])
    assert captured["schema"][0] == "Schema"
    assert GQL._mutation_class is None

def test_addGraphqlUrl_appends_path_and_uses_default_url(monkeypatch, settings):
    # Build a dynamic urlconf module
    module_name = "tests.dynamic_urls_two"
    urlconf = types.SimpleNamespace(urlpatterns=[])
    monkeypatch.setitem(sys.modules, module_name, urlconf)
    settings.ROOT_URLCONF = module_name
    if hasattr(settings, "GRAPHQL_URL"):
        delattr(settings, "GRAPHQL_URL")  # use default "graphql"

    # Stub GraphQLView.as_view
    class _V:
        @staticmethod
        def as_view(**kw):
            return ("as_view", kw)
    monkeypatch.setitem(sys.modules, "graphene_django.views", types.SimpleNamespace(GraphQLView=_V))

    # Import target and stub its 'path' to avoid Django internals
    import general_manager.apps as apps_mod
    def stub_path(route, view):
        return ("path", route, view)
    monkeypatch.setattr(apps_mod, "path", stub_path, raising=True)

    schema_obj = object()
    apps_mod.GeneralmanagerConfig.addGraphqlUrl(schema_obj)

    assert len(urlconf.urlpatterns) == 1
    typ, route, view = urlconf.urlpatterns[0]
    assert typ == "path"
    assert route == "graphql"  # default route
    assert view[0] == "as_view"
    assert view[1]["schema"] is schema_obj
    assert view[1].get("graphiql") is True

def test_addGraphqlUrl_raises_without_root_urlconf(_, settings):
    import general_manager.apps as apps_mod
    if hasattr(settings, "ROOT_URLCONF"):
        delattr(settings, "ROOT_URLCONF")
    with pytest.raises(Exception) as ei:
        apps_mod.GeneralmanagerConfig.addGraphqlUrl(schema=object())
    assert "ROOT_URLCONF" in str(ei.value)

def test_checkPermissionClass_enforces_and_defaults(monkeypatch):
    import general_manager.apps as apps_mod

    class BasePermission:
        pass
    
    class ManagerBasedPermission(BasePermission):
        pass

    perm_mod = types.SimpleNamespace(
        BasePermission=BasePermission,
        ManagerBasedPermission=ManagerBasedPermission
    )
    monkeypatch.setitem(sys.modules, "general_manager.permission.basePermission", perm_mod)
    monkeypatch.setitem(sys.modules, "general_manager.permission.managerBasedPermission", perm_mod)

    class GM:
        pass
    
    apps_mod.GeneralmanagerConfig.checkPermissionClass(GM)
    assert GM.Permission is ManagerBasedPermission

    class CustomPerm(BasePermission):
        pass
    
    class GM2:
        Permission = CustomPerm
    
    apps_mod.GeneralmanagerConfig.checkPermissionClass(GM2)
    assert GM2.Permission is CustomPerm

    class NotPerm:
        pass
    
    class GM3:
        Permission = NotPerm
    
    with pytest.raises(TypeError):
        apps_mod.GeneralmanagerConfig.checkPermissionClass(GM3)