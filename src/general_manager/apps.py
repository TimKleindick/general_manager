"""Django AppConfig for the general_manager package.

Boot logic lives in ``general_manager.bootstrap``; this module is a thin
``AppConfig`` adapter that coordinates startup phases and re-exports the
bootstrap helpers so existing imports (e.g.
``from general_manager.apps import GeneralmanagerConfig``) continue to work.
"""

from __future__ import annotations

from importlib import import_module, util
from typing import TYPE_CHECKING, Any, Iterable, Type

from django.apps import AppConfig, apps as django_apps
from django.conf import settings

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
from general_manager.api.graphql_warmup_tasks import (
    configure_graphql_warmup_beat_schedule_from_settings,
)

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("apps")

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


def configure_search_reconcile_beat_schedule_from_settings(
    django_settings: Any,
) -> bool:
    """Configure search reconciliation Beat schedule after apps are ready."""
    from general_manager.search.tasks import (
        configure_search_reconcile_beat_schedule_from_settings as _configure,
    )

    return _configure(django_settings)


class GeneralmanagerConfig(AppConfig):
    """Django application configuration for GeneralManager startup hooks."""

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
        configure_search_reconcile_beat_schedule_from_settings(settings)
        configure_graphql_warmup_beat_schedule_from_settings(settings)
        from general_manager.search import indexer as _search_indexer  # noqa: F401

        from general_manager.conf import get_setting

        if get_setting("AUTOCREATE_GRAPHQL", False):
            handle_graph_ql(GeneralManagerMeta.pending_graphql_interfaces)

    # ------------------------------------------------------------------
    # Static-method wrappers kept for backward compatibility with tests
    # that call GeneralmanagerConfig.<method>(...)
    # ------------------------------------------------------------------

    @staticmethod
    def install_startup_hook_runner() -> None:
        """Install the startup hook runner through the bootstrap module."""
        install_startup_hook_runner()

    @staticmethod
    def register_system_checks() -> None:
        """Register GeneralManager system checks through the bootstrap module."""
        register_system_checks()

    @staticmethod
    def initialize_general_manager_classes(
        pending_attribute_initialization: list[Type[GeneralManager]],
        all_classes: list[Type[GeneralManager]],
    ) -> None:
        """Initialize pending manager classes through the bootstrap module."""
        initialize_general_manager_classes(
            pending_attribute_initialization, all_classes
        )

    @staticmethod
    def check_permission_class(general_manager_class: Type[GeneralManager]) -> None:
        """Validate a manager permission class through the bootstrap module."""
        check_permission_class(general_manager_class)

    @staticmethod
    def handle_graph_ql(
        pending_graphql_interfaces: list[Type[GeneralManager]],
    ) -> None:
        """Build GraphQL integration for pending manager interfaces."""
        handle_graph_ql(pending_graphql_interfaces)

    @staticmethod
    def handle_remote_api(
        manager_classes: list[Type[GeneralManager]],
    ) -> None:
        """Register remote API integration for manager classes."""
        handle_remote_api(manager_classes)

    @staticmethod
    def add_graphql_url(schema: Any) -> None:
        """Add the generated GraphQL URL to the configured URLConf."""
        add_graphql_url(schema)

    @staticmethod
    def _ensure_asgi_subscription_route(graphql_url: str) -> None:
        """Ensure the ASGI subscription route exists for the GraphQL URL."""
        _ensure_asgi_subscription_route(graphql_url)
