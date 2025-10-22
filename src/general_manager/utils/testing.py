"""Test utilities for GeneralManager GraphQL integrations."""

from contextlib import suppress
from importlib import import_module
from typing import Any, Callable, ClassVar, cast

from django.apps import AppConfig, apps as global_apps
from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.db import connection, models
from django.test import override_settings
from graphene_django.utils.testing import GraphQLTransactionTestCase  # type: ignore[import]
from unittest.mock import ANY

from general_manager.api.graphql import GraphQL
from general_manager.apps import GeneralmanagerConfig
from general_manager.cache.cacheDecorator import _SENTINEL
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta

_original_get_app: Callable[[str], AppConfig | None] = (
    global_apps.get_containing_app_config
)


def createFallbackGetApp(fallback_app: str) -> Callable[[str], AppConfig | None]:
    """
    Create an app-config lookup that falls back to a specific Django app.

    Parameters:
        fallback_app (str): App label used when the default lookup cannot resolve the object.

    Returns:
        Callable[[str], Any]: Function returning either the resolved configuration or the fallback app configuration when available.
    """

    def _fallback_get_app(object_name: str) -> AppConfig | None:
        cfg = _original_get_app(object_name)
        if cfg is not None:
            return cfg
        try:
            return global_apps.get_app_config(fallback_app)
        except LookupError:
            return None

    return _fallback_get_app


def _default_graphql_url_clear() -> None:
    """
    Remove the default GraphQL URL pattern from Django's root URL configuration.

    The lookup searches for the first URL pattern whose view class is `GraphQLView` and removes it from the URL list.

    Returns:
        None
    """
    urlconf = import_module(settings.ROOT_URLCONF)
    for pattern in urlconf.urlpatterns:
        if (
            hasattr(pattern, "callback")
            and hasattr(pattern.callback, "view_class")
            and pattern.callback.view_class.__name__ == "GraphQLView"
        ):
            urlconf.urlpatterns.remove(pattern)
            break


class GMTestCaseMeta(type):
    """
    Metaclass that wraps setUpClass: first calls user-defined setup,
    then performs GM environment initialization, then super().setUpClass().
    """

    def __new__(
        mcs: type["GMTestCaseMeta"],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, object],
    ) -> type:
        """
        Create a new test case class that injects GeneralManager-specific initialization into `setUpClass`.

        The constructed class replaces or wraps any user-defined `setUpClass` with logic that resets GraphQL and manager registries, configures an optional fallback app lookup, ensures database tables for managed models exist, initializes GeneralManager and GraphQL registrations, and then calls the standard GraphQL test setup.

        Parameters:
            mcs (type[GMTestCaseMeta]): Metaclass constructing the new class.
            name (str): Name of the class to create.
            bases (tuple[type, ...]): Base classes for the new class.
            attrs (dict[str, object]): Class namespace; may contain a user-defined `setUpClass` and `fallback_app`.

        Returns:
            type: The newly created test case class whose `setUpClass` has been augmented for GeneralManager testing.
        """
        user_setup = attrs.get("setUpClass")
        fallback_app = cast(str | None, attrs.get("fallback_app", "general_manager"))
        # MERKE dir das echte GraphQLTransactionTestCase.setUpClass
        base_setup = GraphQLTransactionTestCase.setUpClass

        def wrapped_setUpClass(
            cls: type["GeneralManagerTransactionTestCase"],
        ) -> None:
            """
            Prepare the test environment for GeneralManager GraphQL tests.

            Resets GraphQL and manager registries, optionally overrides Django's app-config lookup to use a fallback app, clears the default GraphQL URL pattern, creates missing database tables for models referenced by the test class's `general_manager_classes`, initializes GeneralManager classes and read-only interfaces, registers GraphQL types/fields, and then invokes the base class `setUpClass`.
            """
            GraphQL._query_class = None
            GraphQL._mutation_class = None
            GraphQL._subscription_class = None
            GraphQL._mutations = {}
            GraphQL._query_fields = {}
            GraphQL._subscription_fields = {}
            GraphQL.graphql_type_registry = {}
            GraphQL.graphql_filter_type_registry = {}
            GraphQL._subscription_payload_registry = {}
            GraphQL._page_type_registry = {}
            GraphQL.manager_registry = {}
            GraphQL._schema = None

            if fallback_app is not None:
                handler = createFallbackGetApp(fallback_app)
                global_apps.get_containing_app_config = cast(  # type: ignore[assignment]
                    Callable[[str], AppConfig | None], handler
                )

            # 1) user-defined setUpClass (if any)
            if user_setup:
                if isinstance(user_setup, classmethod):
                    user_setup.__func__(cls)
                else:
                    cast(
                        Callable[[type["GeneralManagerTransactionTestCase"]], None],
                        user_setup,
                    )(cls)
            # 2) clear URL patterns
            _default_graphql_url_clear()
            # 3) register models & create tables
            existing = connection.introspection.table_names()
            with connection.schema_editor() as editor:
                for manager_class in cls.general_manager_classes:
                    if not hasattr(manager_class, "Interface") or not hasattr(
                        manager_class.Interface, "_model"
                    ):
                        continue
                    model_class = cast(
                        type[models.Model],
                        manager_class.Interface._model,  # type: ignore
                    )
                    if model_class._meta.db_table not in existing:
                        editor.create_model(model_class)
                        history_model = getattr(model_class, "history", None)
                        if history_model:
                            editor.create_model(history_model.model)  # type: ignore[attr-defined]
            # 4) GM & GraphQL initialization
            GeneralmanagerConfig.initializeGeneralManagerClasses(
                cls.general_manager_classes, cls.general_manager_classes
            )
            GeneralmanagerConfig.handleReadOnlyInterface(cls.read_only_classes)
            GeneralmanagerConfig.handleGraphQL(cls.general_manager_classes)
            # 5) GraphQLTransactionTestCase.setUpClass
            base_setup.__func__(cls)

        attrs["setUpClass"] = classmethod(wrapped_setUpClass)
        return super().__new__(mcs, name, bases, attrs)


class LoggingCache(LocMemCache):
    """An in-memory cache backend that records its get and set operations."""

    def __init__(self, location: str, params: dict[str, Any]) -> None:
        """Initialise the cache backend and the operation log store."""
        super().__init__(location, params)
        self.ops: list[tuple[str, object, bool] | tuple[str, object]] = []

    def get(
        self,
        key: str,
        default: object = None,
        version: int | None = None,
    ) -> object:
        """
        Retrieve a value from the cache and record whether it was a hit or miss.

        Parameters:
            key (str): Cache key identifying the stored value.
            default (Any): Fallback returned when the key is absent.
            version (int | None): Optional cache version used for the lookup.

        Returns:
            Any: Cached value when present; otherwise, the provided default.
        """
        val = super().get(key, default)
        self.ops.append(("get", key, val is not _SENTINEL))
        return val

    def set(
        self,
        key: str,
        value: object,
        timeout: float | None = None,
        version: int | None = None,
    ) -> None:
        """
        Store a value in the cache and record the set operation in the cache's operation log.

        Parameters:
            key (str): Cache key under which to store the value.
            value (object): Value to store.
            timeout (float | None): Expiration time in seconds, or None for no explicit timeout.
            version (int | None): Optional cache version identifier.
        """
        timeout = int(timeout) if timeout is not None else timeout
        super().set(key, value, timeout=timeout, version=version)
        self.ops.append(("set", key))


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-cache",
        }
    },
    CHANNEL_LAYERS={
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    },
)
class GeneralManagerTransactionTestCase(
    GraphQLTransactionTestCase, metaclass=GMTestCaseMeta
):
    GRAPHQL_URL = "/graphql/"
    general_manager_classes: ClassVar[list[type[GeneralManager]]] = []
    read_only_classes: ClassVar[list[type[GeneralManager]]] = []
    fallback_app: str | None = "general_manager"

    def setUp(self) -> None:
        """
        Install a logging cache backend and reset its operation log for the test.

        Replaces Django's default cache connection with a LoggingCache instance and clears any prior cache operation records so tests start with a fresh cache operation log.
        """
        super().setUp()
        caches._connections.default = LoggingCache("test-cache", {})  # type: ignore[attr-defined]
        self.__resetCacheCounter()

    @classmethod
    def tearDownClass(cls) -> None:
        """
        Tear down test-class state by removing dynamically created models and restoring patched global state.

        Performs these actions: removes the GraphQL URL pattern added during setup; drops database tables for any models created for the test (including associated history models); unregisters those models from Django's app registry and clears the app cache; removes the test's GeneralManager classes from metaclass registries; restores the original app-config lookup function; and finally calls the superclass teardown.
        """
        # remove GraphQL URL pattern added during setUpClass
        _default_graphql_url_clear()

        # drop generated tables and unregister models from Django's app registry
        existing = connection.introspection.table_names()
        with connection.schema_editor() as editor:
            for manager_class in cls.general_manager_classes:
                interface = getattr(manager_class, "Interface", None)
                model = getattr(interface, "_model", None)
                if not model:
                    continue
                model = cast(type[models.Model], model)
                if model._meta.db_table in existing:
                    editor.delete_model(model)
                history_model = getattr(model, "history", None)
                if history_model and history_model.model._meta.db_table in existing:
                    editor.delete_model(history_model.model)

                app_label = model._meta.app_label
                model_key = model.__name__.lower()
                global_apps.all_models[app_label].pop(model_key, None)
                app_config = global_apps.get_app_config(app_label)
                with suppress(LookupError):
                    app_config.models.pop(model_key, None)
                if history_model:
                    hist_key = history_model.model.__name__.lower()
                    global_apps.all_models[app_label].pop(hist_key, None)
                    with suppress(LookupError):
                        app_config.models.pop(hist_key, None)

        global_apps.clear_cache()

        # remove classes from metaclass registries
        GeneralManagerMeta.all_classes = [
            gm
            for gm in GeneralManagerMeta.all_classes
            if gm not in cls.general_manager_classes
        ]
        GeneralManagerMeta.pending_graphql_interfaces = [
            gm
            for gm in GeneralManagerMeta.pending_graphql_interfaces
            if gm not in cls.general_manager_classes
        ]
        GeneralManagerMeta.pending_attribute_initialization = [
            gm
            for gm in GeneralManagerMeta.pending_attribute_initialization
            if gm not in cls.general_manager_classes
        ]

        # reset fallback app lookup
        global_apps.get_containing_app_config = cast(  # type: ignore[assignment]
            Callable[[str], AppConfig | None], _original_get_app
        )

        super().tearDownClass()

    #
    def assertCacheMiss(self) -> None:
        """
        Assert that a cache retrieval missed and was followed by a write.

        The expectation is a `get` operation returning no value and a subsequent `set` operation storing the computed result. The cache operation log is cleared afterwards.

        Returns:
            None
        """
        cache_backend = cast(LoggingCache, caches["default"])
        ops = cache_backend.ops
        self.assertIn(
            ("get", ANY, False),
            ops,
            "Cache.get should have been called and found nothing",
        )
        self.assertIn(("set", ANY), ops, "Cache.set should have stored the value")
        self.__resetCacheCounter()

    def assertCacheHit(self) -> None:
        """
        Assert that a cache lookup succeeded without triggering a write.

        The expectation is a `get` operation that returns a cached value and no recorded `set` operation. The cache operation log is cleared afterwards.

        Returns:
            None
        """
        cache_backend = cast(LoggingCache, caches["default"])
        ops = cache_backend.ops
        self.assertIn(
            ("get", ANY, True),
            ops,
            "Cache.get should have been called and found something",
        )

        self.assertNotIn(
            ("set", ANY),
            ops,
            "Cache.set should not have stored anything",
        )
        self.__resetCacheCounter()

    def __resetCacheCounter(self) -> None:
        """
        Clear the log of cache operations recorded by the LoggingCache instance.

        Returns:
            None
        """
        cast(LoggingCache, caches["default"]).ops = []
