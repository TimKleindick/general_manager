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

from simple_history.models import HistoricalChanges

from general_manager.api.graphql import GraphQL
from general_manager.apps import GeneralmanagerConfig
from general_manager.cache.cache_decorator import _SENTINEL
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta

_original_get_app: Callable[[str], AppConfig | None] = (
    global_apps.get_containing_app_config
)


def create_fallback_get_app(fallback_app: str) -> Callable[[str], AppConfig | None]:
    """
    Create an app-config lookup that falls back to a specific Django app.

    Parameters:
        fallback_app (str): App label used when the default lookup cannot resolve the object.

    Returns:
        Callable[[str], AppConfig | None]: Function returning either the resolved configuration or the fallback app configuration when available.
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
    Remove the first root URLconf pattern whose view class is named "GraphQLView".

    Searches the project's ROOT_URLCONF urlpatterns and removes the first pattern whose callback exposes a `view_class` attribute with the name "GraphQLView". This is used to reset GraphQL URL configuration between tests.
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


def _get_historical_changes_related_models(
    history_model_class: type[models.Model],
) -> list[type[models.Model]]:
    """
    Finds models subclassing `HistoricalChanges` that are related to the given history model via ManyToOne relations.

    Parameters:
        history_model_class (type[models.Model]): Django model class for a history model to inspect.

    Returns:
        list[type[models.Model]]: List of related model classes that subclass `HistoricalChanges` and are connected to `history_model_class` by a `ManyToOneRel`.
    """
    related_models: list[type[models.Model]] = []
    for rel in history_model_class._meta.get_fields():
        if not isinstance(rel, models.ManyToOneRel):
            continue
        related_model = getattr(rel, "related_model", None)
        if not isinstance(related_model, type):
            continue
        if not issubclass(related_model, HistoricalChanges):
            continue
        related_models.append(cast(type[models.Model], related_model))
    return related_models


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
            Prepare the class-level test environment for GeneralManager GraphQL tests.

            Resets GraphQL and manager registries, optionally installs an app-config fallback for resolving AppConfig, clears the default GraphQL URL pattern, creates any missing database tables for the test's registered models (including their history models and related models used by HistoricalChanges) and records created table names on `cls._gm_created_tables`, initializes GeneralManager classes, read-only interfaces, and GraphQL registrations, runs any user-defined `setUpClass`, and finally invokes the base `GraphQLTransactionTestCase.setUpClass`.
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
                handler = create_fallback_get_app(fallback_app)
                global_apps.get_containing_app_config = cast(  # type: ignore[assignment]
                    Callable[[str], AppConfig | None], handler
                )

            cls._gm_created_tables = set()
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
            preexisting_tables = set(connection.introspection.table_names())
            known_tables = set(preexisting_tables)
            with connection.schema_editor() as editor:
                for manager_class in cls.general_manager_classes:
                    if not hasattr(manager_class, "Interface") or not hasattr(
                        manager_class.Interface, "_model"
                    ):
                        continue
                    model_class = cast(
                        type[models.Model], manager_class.Interface._model
                    )  # type: ignore[attr-defined]
                    model_table = model_class._meta.db_table
                    if model_table not in known_tables:
                        editor.create_model(model_class)
                        known_tables.add(model_table)
                    history_model = getattr(model_class, "history", None)
                    if history_model:
                        history_model_class = cast(
                            type[models.Model],
                            history_model.model,  # type: ignore[attr-defined]
                        )
                        history_table = history_model_class._meta.db_table
                        if history_table not in known_tables:
                            editor.create_model(history_model_class)
                            known_tables.add(history_table)
                        for related_model in _get_historical_changes_related_models(
                            history_model_class
                        ):
                            related_table = related_model._meta.db_table
                            if related_table not in known_tables:
                                editor.create_model(related_model)
                                known_tables.add(related_table)
            post_tables = set(connection.introspection.table_names())
            cls._gm_created_tables.update(post_tables - preexisting_tables)
            # 4) GM & GraphQL initialization
            GeneralmanagerConfig.initialize_general_manager_classes(
                cls.general_manager_classes, cls.general_manager_classes
            )
            GeneralmanagerConfig.handle_read_only_interface(cls.read_only_classes)
            GeneralmanagerConfig.handle_graph_ql(cls.general_manager_classes)
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
    _gm_created_tables: ClassVar[set[str]] = set()

    def setUp(self) -> None:
        """
        Install a LoggingCache as the default cache backend for the test and clear its operation log.

        Replaces Django's default cache connection with a LoggingCache instance and resets the cache operation log so the test starts with no prior cache operations recorded.
        """
        super().setUp()
        caches._connections.default = LoggingCache("test-cache", {})  # type: ignore[attr-defined]
        self.__reset_cache_counter()

    @classmethod
    def tearDownClass(cls) -> None:
        """
        Tear down test-class state for GeneralManager tests by removing created database tables, unregistering their models, restoring patched global state, and clearing metaclass registries.

        Performs the following cleanup actions for the test class:
        - Removes the GraphQL URL pattern added during setup.
        - Drops database tables that were created for the test, including automatically created many-to-many through tables, history tables, and history-related models.
        - Unregisters those models (including through and history models) from Django's app registry and clears the app registry cache.
        - Removes the test's GeneralManager classes from metaclass registries used for initialization and GraphQL registration.
        - Restores the original app-config lookup function.
        - Resets the test-class created-table tracking.
        """
        # remove GraphQL URL pattern added during setUpClass
        _default_graphql_url_clear()

        # drop generated tables and unregister models from Django's app registry
        created_tables: set[str] = set(getattr(cls, "_gm_created_tables", set()))
        tables_to_remove = {
            table
            for table in created_tables
            if table in connection.introspection.table_names()
        }
        with connection.schema_editor() as editor:
            for manager_class in cls.general_manager_classes:
                interface = getattr(manager_class, "Interface", None)
                model = getattr(interface, "_model", None)
                if not model:
                    continue
                model = cast(type[models.Model], model)
                auto_through_models: set[type[models.Model]] = set()
                for field in model._meta.local_many_to_many:
                    m2m_field = cast(Any, field)
                    through_model = cast(
                        type[models.Model], m2m_field.remote_field.through
                    )
                    if getattr(through_model._meta, "auto_created", False):
                        auto_through_models.add(through_model)
                model_table = model._meta.db_table
                if model_table in tables_to_remove:
                    editor.delete_model(model)
                    tables_to_remove.discard(model_table)
                    for through in auto_through_models:
                        tables_to_remove.discard(through._meta.db_table)
                history_model = getattr(model, "history", None)
                related_history_models: list[type[models.Model]] = []
                if history_model:
                    history_model_class = cast(
                        type[models.Model],
                        history_model.model,  # type: ignore[attr-defined]
                    )
                    related_history_models = _get_historical_changes_related_models(
                        history_model_class
                    )
                if history_model:
                    history_table = history_model.model._meta.db_table
                    if history_table in tables_to_remove:
                        editor.delete_model(history_model.model)
                        tables_to_remove.discard(history_table)
                    for related_history_model in related_history_models:
                        related_history_table = related_history_model._meta.db_table
                        if related_history_table in tables_to_remove:
                            editor.delete_model(related_history_model)
                            tables_to_remove.discard(related_history_table)
                for through in auto_through_models:
                    through_table = through._meta.db_table
                    if through_table in tables_to_remove:
                        editor.delete_model(through)
                        tables_to_remove.discard(through_table)

                app_label = model._meta.app_label
                model_key = model.__name__.lower()
                if model_table in created_tables:
                    global_apps.all_models[app_label].pop(model_key, None)
                    app_config = global_apps.get_app_config(app_label)
                    with suppress(LookupError):
                        app_config.models.pop(model_key, None)
                if (
                    history_model
                    and history_model.model._meta.db_table in created_tables
                ):
                    hist_key = history_model.model.__name__.lower()
                    global_apps.all_models[app_label].pop(hist_key, None)
                    with suppress(LookupError):
                        app_config.models.pop(hist_key, None)
                for related_history_model in related_history_models:
                    table = related_history_model._meta.db_table
                    if table in created_tables:
                        label = related_history_model._meta.app_label
                        key = related_history_model.__name__.lower()
                        global_apps.all_models[label].pop(key, None)
                        with suppress(LookupError):
                            global_apps.get_app_config(label).models.pop(key, None)
                for through in auto_through_models:
                    through_label = through._meta.app_label
                    through_key = through.__name__.lower()
                    if through._meta.db_table in created_tables:
                        global_apps.all_models[through_label].pop(through_key, None)
                        with suppress(LookupError):
                            global_apps.get_app_config(through_label).models.pop(
                                through_key, None
                            )

        global_apps.clear_cache()
        cls._gm_created_tables = set()

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
    def assert_cache_miss(self) -> None:
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
        self.__reset_cache_counter()

    def assert_cache_hit(self) -> None:
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
        self.__reset_cache_counter()

    def __reset_cache_counter(self) -> None:
        """
        Clear the log of cache operations recorded by the LoggingCache instance.

        Returns:
            None
        """
        cast(LoggingCache, caches["default"]).ops = []
