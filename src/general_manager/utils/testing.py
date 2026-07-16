"""Test utilities for GeneralManager GraphQL integrations."""

from contextlib import suppress
from importlib import import_module
from typing import TYPE_CHECKING, Callable, ClassVar, Iterable, Protocol, Sequence, cast

from django.apps import AppConfig, apps as global_apps
from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.db import connection, connections, models
from django.test import TransactionTestCase
from django.test import override_settings
from unittest.mock import ANY

from graphene_django.debug.sql.tracking import unwrap_cursor
from simple_history.models import HistoricalChanges

from general_manager.api.graphql import GraphQL
from general_manager.api.remote_api import clear_remote_api_urls
from general_manager.api.remote_invalidation import clear_remote_invalidation_routes
from general_manager.uploads.urls import clear_file_upload_urls
from general_manager.apps import GeneralmanagerConfig
from general_manager.cache.cache_decorator import _SENTINEL
from general_manager.cache.dependency_index import (
    DATA_CHANGE_COUNT_KEY,
    DATA_CHANGE_LOCK_KEY,
    DEPENDENCY_GENERATION_KEY,
    INDEX_KEY,
    LOCK_KEY,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.infrastructure.startup_hooks import (
    DependencyResolver,
    order_interfaces_by_dependency,
    registered_startup_hook_entries,
)

if TYPE_CHECKING:
    GraphQLTransactionTestCase = TransactionTestCase
else:
    from graphene_django.utils.testing import GraphQLTransactionTestCase

_original_get_app: Callable[[str], AppConfig | None] = (
    global_apps.get_containing_app_config
)
_DEPENDENCY_COORDINATION_KEYS = {
    DATA_CHANGE_COUNT_KEY,
    DATA_CHANGE_LOCK_KEY,
    DEPENDENCY_GENERATION_KEY,
    INDEX_KEY,
    LOCK_KEY,
}
CacheOperation = (
    tuple[str, object, bool]
    | tuple[str, object]
    | tuple[str, tuple[str, ...], tuple[str, ...]]
)


class _HistoryDescriptor(Protocol):
    """History descriptor shape used by simple-history model managers."""

    model: type[models.Model]


class _RemoteField(Protocol):
    """Many-to-many remote field attributes required during teardown."""

    through: type[models.Model]


class _ManyToManyField(Protocol):
    """Many-to-many field surface used by the test teardown cleanup."""

    remote_field: _RemoteField


class _CacheConnections(Protocol):
    """Django cache connection storage attributes mutated by the test harness."""

    default: object


class _MutableDjangoApps(Protocol):
    """Mutable Django app-registry hooks patched by the test harness."""

    get_containing_app_config: Callable[[str], AppConfig | None]


class _ClassSetUpDescriptor(Protocol):
    """Classmethod descriptor exposing the wrapped setup callable."""

    __func__: Callable[[type["GeneralManagerTransactionTestCase"]], None]


def create_fallback_get_app(fallback_app: str) -> Callable[[str], AppConfig | None]:
    """
    Create an app-config lookup that falls back to a specific Django app.

    Parameters:
        fallback_app (str): App label used when the default lookup cannot resolve the object.

    Returns:
        Callable[[str], AppConfig | None]: Function returning either the resolved configuration, the fallback app configuration, or `None` when both lookups miss.
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
    Remove the first root URLconf pattern whose view class is a GraphQL view.

    Searches the project's ROOT_URLCONF urlpatterns and removes the first pattern
    whose callback exposes a `view_class` attribute with the name "GraphQLView" or
    "GeneralManagerGraphQLView". This is used to reset GraphQL URL configuration
    between tests.
    """
    urlconf = import_module(settings.ROOT_URLCONF)
    for pattern in urlconf.urlpatterns:
        if not hasattr(pattern, "callback") or not hasattr(
            pattern.callback, "view_class"
        ):
            continue
        view_name = getattr(pattern.callback.view_class, "__name__", "")
        if view_name in {"GraphQLView", "GeneralManagerGraphQLView"}:
            urlconf.urlpatterns.remove(pattern)
            break


def _default_remote_api_url_clear() -> None:
    """Remove auto-generated RemoteAPI URL patterns from the root URLconf."""
    clear_remote_api_urls()
    clear_remote_invalidation_routes()
    clear_file_upload_urls()


def _restore_graphene_cursor_wrappers() -> None:
    """Remove Graphene SQL instrumentation from every configured connection."""
    for database_connection in connections.all():
        unwrap_cursor(database_connection)


def _get_historical_changes_related_models(
    history_model_class: type[models.Model],
) -> list[type[models.Model]]:
    """
    Collects model classes that subclass `HistoricalChanges` and are related to the given history model via a ManyToOne relation.

    @returns list[type[models.Model]]: List of model classes that subclass `HistoricalChanges` and are connected to `history_model_class` by a `ManyToOneRel`.
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


def run_registered_startup_hooks(
    *,
    managers: Sequence[type[GeneralManager]] | None = None,
    interfaces: Sequence[type[InterfaceBase]] | None = None,
) -> list[type[InterfaceBase]]:
    """
    Collect interfaces and run their registered startup hooks.

    Parameters:
        managers (Sequence[type[GeneralManager]] | None): GeneralManager classes whose nested `Interface` subclasses should be included.
        interfaces (Sequence[type[InterfaceBase]] | None): Explicit Interface classes to include.

    Returns:
        list[type[InterfaceBase]]: Collected Interface classes in collection order.

    Raises:
        Exception: Exceptions from capability initialization, dependency ordering, or hook execution propagate.
    """
    interface_list: list[type[InterfaceBase]] = []
    if managers:
        for manager_class in managers:
            interface_cls = getattr(manager_class, "Interface", None)
            if (
                isinstance(interface_cls, type)
                and issubclass(interface_cls, InterfaceBase)
                and interface_cls not in interface_list
            ):
                interface_list.append(interface_cls)
    if interfaces:
        for interface_cls in interfaces:
            if (
                isinstance(interface_cls, type)
                and issubclass(interface_cls, InterfaceBase)
                and interface_cls not in interface_list
            ):
                interface_list.append(interface_cls)
    if not interface_list:
        return []
    for interface_cls in interface_list:
        interface_cls.get_capabilities()

    registry = registered_startup_hook_entries()
    # Group interfaces by dependency resolver so each hook set orders independently.
    resolver_groups: list[
        tuple[DependencyResolver | None, list[type[InterfaceBase]]]
    ] = []

    def _group_for_resolver(
        resolver: DependencyResolver | None,
    ) -> list[type[InterfaceBase]]:
        for registered_resolver, resolver_interfaces in resolver_groups:
            if registered_resolver is resolver:
                return resolver_interfaces
        new_group: list[type[InterfaceBase]] = []
        resolver_groups.append((resolver, new_group))
        return new_group

    for interface_cls in interface_list:
        entries = registry.get(interface_cls, ())
        for entry in entries:
            resolver_list = _group_for_resolver(entry.dependency_resolver)
            if interface_cls not in resolver_list:
                resolver_list.append(interface_cls)

    for resolver, iface_list in resolver_groups:
        ordered = order_interfaces_by_dependency(iface_list, resolver)
        for interface_cls in ordered:
            for entry in registry.get(interface_cls, ()):
                if entry.dependency_resolver is resolver:
                    entry.hook()

    return interface_list


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
        Constructs a test case class whose setUpClass is augmented to initialize GeneralManager and GraphQL test state.

        The augmented setUpClass resets GraphQL internal registries and schema/type state, optionally installs an AppConfig fallback resolver, ensures database tables for the test's managed models (including history and related HistoricalChanges models) exist, records owned tables, and preserves model creation order for teardown. It then initializes GeneralManager classes and GraphQL registrations (including the startup hook runner and system checks), runs any user-defined setUpClass, and invokes the base GraphQLTransactionTestCase.setUpClass.

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
        base_setup = cast(_ClassSetUpDescriptor, GraphQLTransactionTestCase.setUpClass)

        def wrapped_setUpClass(
            cls: type["GeneralManagerTransactionTestCase"],
        ) -> None:
            """
            Prepare the class-level test environment for GeneralManager GraphQL tests.

            Resets GraphQL registries and schema/type state; optionally installs a fallback AppConfig lookup if configured; creates any missing database tables for models referenced by the test's GeneralManager interfaces (including their history models and models related via HistoricalChanges); records owned table names and model creation order; initializes GeneralManager classes and their GraphQL registrations (including installing the startup hook runner and registering system checks); clears the default GraphQL URL pattern; executes any user-defined setUpClass for the test class; and finally invokes the base GraphQLTransactionTestCase.setUpClass.
            """
            GraphQL.reset_registry()

            if fallback_app is not None:
                handler = create_fallback_get_app(fallback_app)
                cast(
                    _MutableDjangoApps,
                    global_apps,
                ).get_containing_app_config = handler

            cls._gm_created_tables = set()
            cls._gm_created_models = []
            # 1) user-defined setUpClass (if any)
            if user_setup:
                if isinstance(user_setup, classmethod):
                    cast(_ClassSetUpDescriptor, user_setup).__func__(cls)
                else:
                    cast(
                        Callable[[type["GeneralManagerTransactionTestCase"]], None],
                        user_setup,
                    )(cls)
            # 2) clear URL patterns
            _default_graphql_url_clear()
            _default_remote_api_url_clear()
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
                    )
                    model_table = model_class._meta.db_table
                    if model_table not in known_tables:
                        editor.create_model(model_class)
                        cls._gm_created_models.append(model_class)
                        known_tables.add(model_table)
                    history_model = getattr(model_class, "history", None)
                    if history_model:
                        history_descriptor = cast(_HistoryDescriptor, history_model)
                        history_model_class = history_descriptor.model
                        history_table = history_model_class._meta.db_table
                        if history_table not in known_tables:
                            editor.create_model(history_model_class)
                            cls._gm_created_models.append(history_model_class)
                            known_tables.add(history_table)
                        for related_model in _get_historical_changes_related_models(
                            history_model_class
                        ):
                            related_table = related_model._meta.db_table
                            if related_table not in known_tables:
                                editor.create_model(related_model)
                                cls._gm_created_models.append(related_model)
                                known_tables.add(related_table)
            post_tables = set(connection.introspection.table_names())
            cls._gm_created_tables.update(post_tables - preexisting_tables)
            # 4) GM & GraphQL initialization
            GeneralmanagerConfig.initialize_general_manager_classes(
                cls.general_manager_classes, cls.general_manager_classes
            )
            GeneralmanagerConfig.handle_remote_api(cls.general_manager_classes)
            GeneralmanagerConfig.install_startup_hook_runner()
            GeneralmanagerConfig.register_system_checks()
            GeneralmanagerConfig.handle_graph_ql(cls.general_manager_classes)
            # 5) GraphQLTransactionTestCase.setUpClass
            base_setup.__func__(cls)

        attrs["setUpClass"] = classmethod(wrapped_setUpClass)
        return super().__new__(mcs, name, bases, attrs)


class LoggingCache(LocMemCache):
    """In-memory cache backend that records get, get_many, and set operations."""

    def __init__(self, location: str, params: dict[str, object]) -> None:
        """Initialise the cache backend and the operation log store."""
        super().__init__(location, params)
        self.ops: list[CacheOperation] = []

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
            default (object): Fallback returned when the key is absent.
            version (int | None): Optional cache version used for the lookup.

        Returns:
            object: Cached value when present; otherwise, the provided default.
        """
        val = super().get(key, default)
        self.ops.append(("get", key, val is not _SENTINEL))
        return val

    def get_many(  # type: ignore[override]
        self,
        keys: Iterable[str],
        version: int | None = None,
    ) -> dict[str, object]:
        """Retrieve multiple keys and record the bulk lookup key/result names."""
        key_tuple = tuple(keys)
        values: dict[str, object] = dict(super().get_many(key_tuple, version=version))
        self.ops.append(("get_many", key_tuple, tuple(values.keys())))
        return values

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
    """
    Transaction test case that prepares GeneralManager GraphQL integration state.

    Subclasses set `general_manager_classes` to the managers under test. The
    harness creates missing model/history tables during class setup, initializes
    GeneralManager GraphQL and remote API registrations, installs a `LoggingCache`
    during `setUp`, runs registered startup hooks, and removes created test state
    during class teardown. Exceptions from Django schema editing, registration,
    startup hooks, or the Graphene-Django base test case propagate.
    """

    GRAPHQL_URL = "/graphql/"
    general_manager_classes: ClassVar[list[type[GeneralManager]]] = []
    fallback_app: str | None = "general_manager"
    _gm_created_tables: ClassVar[set[str]] = set()
    _gm_created_models: ClassVar[list[type[models.Model]]] = []

    def setUp(self) -> None:
        """
        Install a LoggingCache as the Django default cache for the test and clear its operation log.

        Replaces Django's default cache connection with a fresh LoggingCache and resets its recorded operations, then runs any registered startup hooks for the test class.
        """
        super().setUp()
        cache_connections = cast(_CacheConnections, vars(caches)["_connections"])
        cache_connections.default = LoggingCache("test-cache", {})
        cast(LoggingCache, caches["default"]).clear()
        self.__reset_cache_counter()
        self._run_registered_startup_hooks()

    @classmethod
    def _drop_created_test_models(cls) -> None:
        """Drop owned dynamic models in reverse schema creation order."""
        created_tables = set(getattr(cls, "_gm_created_tables", set()))
        tables_to_remove = created_tables.intersection(
            connection.introspection.table_names()
        )
        with connection.constraint_checks_disabled():
            with connection.schema_editor() as editor:
                for model in reversed(getattr(cls, "_gm_created_models", [])):
                    model_table = model._meta.db_table
                    if model_table not in tables_to_remove:
                        continue
                    editor.delete_model(model)
                    tables_to_remove.discard(model_table)
                    for field in model._meta.local_many_to_many:
                        m2m_field = cast(_ManyToManyField, field)
                        through_model = m2m_field.remote_field.through
                        if getattr(through_model._meta, "auto_created", False):
                            tables_to_remove.discard(through_model._meta.db_table)

    @classmethod
    def _unregister_created_test_models(cls) -> None:
        """Remove owned dynamic models from Django's global app registry."""
        created_tables = set(getattr(cls, "_gm_created_tables", set()))
        registered_models: list[type[models.Model]] = []
        for model in getattr(cls, "_gm_created_models", []):
            registered_models.append(model)
            for field in model._meta.local_many_to_many:
                m2m_field = cast(_ManyToManyField, field)
                through_model = m2m_field.remote_field.through
                if getattr(through_model._meta, "auto_created", False):
                    registered_models.append(through_model)

        for model in registered_models:
            if model._meta.db_table not in created_tables:
                continue
            app_label = model._meta.app_label
            model_key = model.__name__.lower()
            global_apps.all_models[app_label].pop(model_key, None)
            with suppress(LookupError):
                global_apps.get_app_config(app_label).models.pop(model_key, None)

    @classmethod
    def tearDownClass(cls) -> None:
        """
        Tear down test-class state for GeneralManager tests by removing created database tables, unregistering their models, restoring patched global state, and clearing metaclass registries.

        Performs the following cleanup actions for the test class:
        - Removes the GraphQL URL pattern added during setup.
        - Drops database tables in reverse model creation order while database constraint checks are disabled, including automatically created many-to-many through tables, history tables, and history-related models.
        - Unregisters those models (including through and history models) from Django's app registry and clears the app registry cache.
        - Removes the test's GeneralManager classes from metaclass registries used for initialization and GraphQL registration.
        - Restores the original app-config lookup function.
        - Resets the test-class created-table and created-model tracking.
        - Removes Graphene cursor instrumentation before Django restores database guards.

        Cleanup actions continue after an error, including the superclass teardown,
        and the first cleanup error is re-raised after all actions have run.
        """

        def clear_metaclass_registries() -> None:
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

        def restore_fallback_app_lookup() -> None:
            cast(
                _MutableDjangoApps,
                global_apps,
            ).get_containing_app_config = _original_get_app

        def reset_created_state() -> None:
            cls._gm_created_models = []
            cls._gm_created_tables = set()

        cleanup_error: Exception | None = None
        cleanup_actions: tuple[Callable[[], None], ...] = (
            _default_graphql_url_clear,
            _default_remote_api_url_clear,
            cls._drop_created_test_models,
            cls._unregister_created_test_models,
            global_apps.clear_cache,
            clear_metaclass_registries,
            restore_fallback_app_lookup,
            reset_created_state,
            _restore_graphene_cursor_wrappers,
            super().tearDownClass,
        )
        for cleanup_action in cleanup_actions:
            try:
                cleanup_action()
            except Exception as error:  # noqa: BLE001 - teardown must keep running.
                if cleanup_error is None:
                    cleanup_error = error

        if cleanup_error is not None:
            raise cleanup_error

    @classmethod
    def _run_registered_startup_hooks(cls) -> None:
        """
        Run startup hooks registered for the test class's GeneralManager interfaces.

        Collects each Interface subclass declared on classes in `general_manager_classes` (preserving that order), ensures each interface's capabilities are initialized by calling `get_capabilities()`, and executes the startup hooks registered for those interfaces. Hooks are executed grouped and ordered per interface dependency resolver so that only hooks whose resolver matches the group run in dependency-resolved sequence.
        """
        run_registered_startup_hooks(managers=cls.general_manager_classes)

    #
    def assert_cache_miss(self) -> None:
        """
        Assert that the default test cache recorded a miss followed by a set, then clear the cache operation log.

        Verifies the default LoggingCache's operation log contains a ("get", key, False) entry indicating a cache miss and a ("set", key) entry indicating a subsequent write. Clears the cache ops after verification.
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

        value_sets = [
            op
            for op in ops
            if op[0] == "set" and op[1] not in _DEPENDENCY_COORDINATION_KEYS
        ]
        self.assertEqual(
            value_sets,
            [],
            "Cache.set should not have stored a cached value",
        )
        self.__reset_cache_counter()

    def cache_ops(self) -> list[CacheOperation]:
        """Return recorded cache operations for assertions."""
        return list(cast(LoggingCache, caches["default"]).ops)

    def reset_cache_ops(self) -> None:
        """Clear recorded cache operations for later assertions."""
        self.__reset_cache_counter()

    def __reset_cache_counter(self) -> None:
        """
        Clear the log of cache operations recorded by the LoggingCache instance.

        Returns:
            None
        """
        cast(LoggingCache, caches["default"]).ops = []
