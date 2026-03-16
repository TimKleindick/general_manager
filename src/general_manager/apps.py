"""Django AppConfig for the general_manager package.

Boot logic lives in ``general_manager.bootstrap``; this module is a thin
``AppConfig`` adapter that coordinates startup phases and re-exports the
bootstrap helpers so existing imports (e.g.
``from general_manager.apps import GeneralmanagerConfig``) continue to work.

The search auto-reindex helpers are defined here (not in bootstrap) because
they depend on a module-level ``_SEARCH_REINDEXED`` flag that tests reset via
``gm_apps._SEARCH_REINDEXED = False``.
"""

from __future__ import annotations

from importlib import import_module, util
from typing import TYPE_CHECKING, Any, Iterable, Type

from django.apps import AppConfig, apps as django_apps
from django.conf import settings
from django.core.management import call_command
from django.core.signals import request_started

from general_manager.bootstrap import (
    MissingRootUrlconfError,
    InvalidPermissionClassError,
    install_startup_hook_runner,
    register_system_checks,
    initialize_general_manager_classes,
    check_permission_class,
    handle_remote_api,
    handle_graph_ql,
    add_graphql_url,
    _ensure_asgi_subscription_route,
)
from general_manager.logging import get_logger
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.audit import configure_audit_logger_from_settings
from general_manager.search.backend_registry import (
    configure_search_backend_from_settings,
)
from general_manager.workflow.backend_registry import (
    configure_workflow_engine_from_settings,
)
from general_manager.workflow.event_registry import (
    configure_event_registry_from_settings,
)
from general_manager.workflow.signal_bridge import (
    configure_workflow_signal_bridge_from_settings,
)
from general_manager.workflow.tasks import (
    configure_workflow_beat_schedule_from_settings,
)

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("apps")

# Module-level flag so tests can reset it via ``gm_apps._SEARCH_REINDEXED = False``.
_SEARCH_REINDEXED = False

# Re-export for backward compatibility
__all__ = [
    "GeneralmanagerConfig",
    "InvalidPermissionClassError",
    "MissingRootUrlconfError",
]


# ---------------------------------------------------------------------------
# App manager auto-loader (kept here for backward-compatible patch targets)
# ---------------------------------------------------------------------------


def _import_optional_managers_module(app_config: AppConfig) -> bool:
    """Import `<app>.managers` when present so manager classes register at startup."""
    module_name = f"{app_config.name}.managers"
    spec = util.find_spec(module_name)
    if spec is None:
        return False
    import_module(module_name)
    logger.debug(
        "imported app managers module",
        context={"app": app_config.label, "module": module_name},
    )
    return True


def _autoload_app_managers_modules(
    app_configs: Iterable[AppConfig] | None = None,
) -> list[str]:
    """Auto-import app `managers` modules before GeneralManager initialization."""
    imported_modules: list[str] = []
    for app_config in app_configs or tuple(django_apps.get_app_configs()):
        if _import_optional_managers_module(app_config):
            imported_modules.append(f"{app_config.name}.managers")
    return imported_modules


# ---------------------------------------------------------------------------
# Search auto-reindex helpers (kept here to stay coupled to _SEARCH_REINDEXED)
# ---------------------------------------------------------------------------


def _normalize_graphql_path(raw_path: str) -> str:
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    if not raw_path.endswith("/"):
        raw_path = f"{raw_path}/"
    return raw_path


def _should_auto_reindex(django_settings: Any) -> bool:
    """
    Return True when search should be auto-reindexed on first request in development.

    Reads from ``django_settings`` directly (rather than the global ``settings`` object)
    so that callers can inject a settings-like object in tests.
    """
    config = getattr(django_settings, "GENERAL_MANAGER", {})
    if isinstance(config, dict) and "SEARCH_AUTO_REINDEX" in config:
        reindex = config["SEARCH_AUTO_REINDEX"]
    else:
        reindex = getattr(django_settings, "SEARCH_AUTO_REINDEX", False)
    return bool(reindex) and bool(getattr(django_settings, "DEBUG", False))


def _auto_reindex_search(*_args: object, **kwargs: object) -> None:
    global _SEARCH_REINDEXED
    environ = kwargs.get("environ")
    if not isinstance(environ, dict):
        return
    request_path = environ.get("PATH_INFO")
    if not isinstance(request_path, str):
        return
    from general_manager.conf import get_setting

    graphql_path = _normalize_graphql_path(get_setting("GRAPHQL_URL", "graphql"))
    if _normalize_graphql_path(request_path) != graphql_path:
        return
    if _SEARCH_REINDEXED:
        return
    _SEARCH_REINDEXED = True
    try:
        call_command("search_index", reindex=True)
        logger.info("auto reindex complete", context={"component": "search"})
    except Exception:  # pragma: no cover - defensive log
        logger.exception("auto reindex failed", context={"component": "search"})


def install_search_auto_reindex() -> None:
    """
    Optionally reindex search data once on first request in development.

    Enabled via ``GENERAL_MANAGER["SEARCH_AUTO_REINDEX"]`` (or legacy
    ``SEARCH_AUTO_REINDEX`` top-level setting).
    """
    if not _should_auto_reindex(settings):
        return
    request_started.connect(
        _auto_reindex_search,
        dispatch_uid="general_manager_auto_reindex_search",
    )


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------


class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "general_manager"

    def ready(self) -> None:
        """Coordinate all startup phases for the general_manager app."""
        self.install_startup_hook_runner()
        self.register_system_checks()
        _autoload_app_managers_modules()
        self.initialize_general_manager_classes(
            GeneralManagerMeta.pending_attribute_initialization,
            GeneralManagerMeta.all_classes,
        )
        handle_remote_api(GeneralManagerMeta.all_classes)
        configure_audit_logger_from_settings(settings)
        configure_search_backend_from_settings(settings)
        configure_workflow_engine_from_settings(settings)
        configure_event_registry_from_settings(settings)
        configure_workflow_signal_bridge_from_settings(settings)
        configure_workflow_beat_schedule_from_settings(settings)
        from general_manager.search import indexer as _search_indexer  # noqa: F401

        self.install_search_auto_reindex()

        from general_manager.conf import get_setting

        if get_setting("AUTOCREATE_GRAPHQL", False):
            handle_graph_ql(GeneralManagerMeta.pending_graphql_interfaces)

    # ------------------------------------------------------------------
    # Static-method wrappers kept for backward compatibility with tests
    # that call GeneralmanagerConfig.<method>(...)
    # ------------------------------------------------------------------

    @staticmethod
    def install_startup_hook_runner() -> None:
        install_startup_hook_runner()

    @staticmethod
    def register_system_checks() -> None:
        register_system_checks()

    @staticmethod
    def install_search_auto_reindex() -> None:
        install_search_auto_reindex()

    @staticmethod
    def initialize_general_manager_classes(
        pending_attribute_initialization: list[Type[GeneralManager]],
        all_classes: list[Type[GeneralManager]],
    ) -> None:
        initialize_general_manager_classes(
            pending_attribute_initialization, all_classes
        )

    @staticmethod
    def check_permission_class(general_manager_class: Type[GeneralManager]) -> None:
        check_permission_class(general_manager_class)

    @staticmethod
    def handle_graph_ql(
        pending_graphql_interfaces: list[Type[GeneralManager]],
    ) -> None:
        handle_graph_ql(pending_graphql_interfaces)

    @staticmethod
    def handle_remote_api(
        manager_classes: list[Type[GeneralManager]],
    ) -> None:
        handle_remote_api(manager_classes)

    @staticmethod
    def add_graphql_url(schema: Any) -> None:
        add_graphql_url(schema)

    @staticmethod
    def _ensure_asgi_subscription_route(graphql_url: str) -> None:
        _ensure_asgi_subscription_route(graphql_url)
