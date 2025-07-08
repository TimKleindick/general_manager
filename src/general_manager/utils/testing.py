from graphene_django.utils.testing import GraphQLTransactionTestCase
from general_manager.apps import GeneralmanagerConfig
from importlib import import_module
from django.db import connection
from django.conf import settings
from typing import cast
from django.db import models
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.graphql import GraphQL
from django.apps import apps as global_apps
from contextlib import suppress


from unittest.mock import ANY
from general_manager.cache.cacheDecorator import _SENTINEL


from django.test import override_settings
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache

_original_get_app = global_apps.get_containing_app_config


def createFallbackGetApp(fallback_app: str):
    """
    Creates a fallback function for getting the app config, which returns the specified fallback app if the original lookup fails.

    Parameters:
        fallback_app (str): The name of the app to return if the original lookup fails.

    Returns:
        function: A function that attempts to get the app config for a given object name, falling back to the specified app if not found.
    """

    def _fallback_get_app(object_name: str):
        cfg = _original_get_app(object_name)
        if cfg is not None:
            return cfg
        try:
            return global_apps.get_app_config(fallback_app)
        except LookupError:
            return None

    return _fallback_get_app


def _default_graphql_url_clear():
    """
    Removes the first URL pattern for the GraphQL view from the project's root URL configuration.

    This function searches the root URL patterns for a pattern whose callback is a `GraphQLView` and removes it, effectively clearing the default GraphQL endpoint from the URL configuration.
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

    def __new__(mcs, name, bases, attrs):
        """
        Creates a new test case class with a customized setUpClass method for GeneralManager and GraphQL integration tests.

        The generated setUpClass ensures the test environment is properly initialized by resetting GraphQL registries, applying any user-defined setup, clearing default GraphQL URL patterns, creating missing database tables for specified GeneralManager models and their history, initializing GeneralManager and GraphQL configurations, and invoking the base GraphQLTransactionTestCase setup.
        """
        user_setup = attrs.get("setUpClass")
        fallback_app = attrs.get("fallback_app", "general_manager")
        # MERKE dir das echte GraphQLTransactionTestCase.setUpClass
        base_setup = GraphQLTransactionTestCase.setUpClass

        def wrapped_setUpClass(cls):
            """
            Performs setup for a test case class by resetting GraphQL internals, configuring fallback app lookup, clearing default GraphQL URL patterns, ensuring database tables exist for specified GeneralManager models and their history, initializing GeneralManager and GraphQL configurations, and invoking the base test case setup.

            Skips database table creation for any GeneralManager class lacking an `Interface` or `_model` attribute.
            """
            GraphQL._query_class = None
            GraphQL._mutation_class = None
            GraphQL._mutations = {}
            GraphQL._query_fields = {}
            GraphQL.graphql_type_registry = {}
            GraphQL.graphql_filter_type_registry = {}

            if fallback_app is not None:
                global_apps.get_containing_app_config = createFallbackGetApp(
                    fallback_app
                )

            # 1) user-defined setUpClass (if any)
            if user_setup:
                user_setup.__func__(cls)
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
                        type[models.Model], manager_class.Interface._model  # type: ignore
                    )
                    if model_class._meta.db_table not in existing:
                        editor.create_model(model_class)
                        editor.create_model(model_class.history.model)  # type: ignore
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
    def __init__(self, *args, **kwargs):
        """
        Initialize the LoggingCache and set up an empty list to record cache operations.
        """
        super().__init__(*args, **kwargs)
        self.ops = []

    def get(self, key, default=None, version=None):
        """
        Retrieve a value from the cache and log whether it was a cache hit or miss.

        Parameters:
            key (str): The cache key to retrieve.
            default: The value to return if the key is not found.
            version: Optional cache version.

        Returns:
            The cached value if found; otherwise, the default value.
        """
        val = super().get(key, default)
        self.ops.append(("get", key, val is not _SENTINEL))
        return val

    def set(self, key, value, timeout=None, version=None):
        """
        Store a value in the cache and log the set operation.

        Parameters:
            key (str): The cache key to set.
            value (Any): The value to store in the cache.
            timeout (Optional[int]): The cache timeout in seconds.
            version (Optional[int]): The cache version (unused).
        """
        super().set(key, value, timeout)
        self.ops.append(("set", key))


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-cache",
        }
    }
)
class GeneralManagerTransactionTestCase(
    GraphQLTransactionTestCase, metaclass=GMTestCaseMeta
):
    general_manager_classes: list[type[GeneralManager]] = []
    read_only_classes: list[type[GeneralManager]] = []
    fallback_app: str | None = "general_manager"

    def setUp(self) -> None:
        """
        Prepares the test environment by replacing the default cache with a LoggingCache and resetting the cache operations log.
        """
        super().setUp()
        setattr(caches._connections, "default", LoggingCache("test-cache", {}))  # type: ignore
        self.__resetCacheCounter()

    @classmethod
    def tearDownClass(cls) -> None:
        """Clean up dynamic managers and restore patched globals."""
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
        global_apps.get_containing_app_config = _original_get_app

        super().tearDownClass()

    #
    def assertCacheMiss(self):
        """
        Assert that a cache miss occurred, followed by a cache set operation.

        Checks that the cache's `get` method was called and did not find a value, and that the `set` method was subsequently called to store a value. Resets the cache operation log after the assertion.
        """
        ops = getattr(caches["default"], "ops")
        self.assertIn(
            ("get", ANY, False),
            ops,
            "Cache.get should have been called and found nothing",
        )
        self.assertIn(("set", ANY), ops, "Cache.set should have stored the value")
        self.__resetCacheCounter()

    def assertCacheHit(self):
        """
        Assert that a cache get operation resulted in a cache hit and no cache set operation occurred.

        Raises an assertion error if the cache did not return a value for a get operation or if a set operation was performed. Resets the cache operation log after the check.
        """
        ops = getattr(caches["default"], "ops")
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

    def __resetCacheCounter(self):
        """
        Clear the log of cache operations recorded by the LoggingCache instance.
        """
        caches["default"].ops = []  # type: ignore
