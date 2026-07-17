from __future__ import annotations

from importlib import import_module
import os
import subprocess
import sys
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.test.utils import override_settings
from unittest.mock import Mock, patch

from general_manager import apps as gm_apps


class AppsUtilitiesTests(SimpleTestCase):
    def test_search_invalidation_configuration_is_idempotent(self) -> None:
        """Repeated startup calls keep one receiver per lifecycle signal."""
        from django.dispatch import Signal

        from general_manager.search import invalidation

        pre_receiver = Mock()
        post_receiver = Mock()
        pre_signal = Signal()
        post_signal = Signal()
        with (
            patch.object(invalidation, "_handle_search_pre_change", pre_receiver),
            patch.object(invalidation, "_handle_search_post_change", post_receiver),
            patch.object(invalidation, "pre_data_change", pre_signal),
            patch.object(invalidation, "post_data_change", post_signal),
        ):
            invalidation.configure_search_invalidation()
            invalidation.configure_search_invalidation()
            pre_signal.send(sender=object, instance=None, action="noop")
            post_signal.send(sender=object, instance=None, action="noop")

        pre_receiver.assert_called_once()
        post_receiver.assert_called_once()

    def test_django_startup_does_not_probe_private_public_api_exports(self) -> None:
        python_path = ["src", "."]
        if existing_python_path := os.environ.get("PYTHONPATH"):
            python_path.append(existing_python_path)
        environment = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tests.test_settings",
            "PYTHONPATH": os.pathsep.join(python_path),
        }
        result = subprocess.run(  # noqa: S603 - trusted current interpreter
            [sys.executable, "-c", "import django; django.setup()"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

        assert "missing public api export" not in result.stdout + result.stderr

    def test_import_optional_managers_module_imports_existing_module(self) -> None:
        """Import an app managers module when Django can find it."""
        app_config = SimpleNamespace(
            name="tests.custom_user_app",
            label="custom_user_app",
        )
        with patch("general_manager.apps.util.find_spec", return_value=object()):
            with patch("general_manager.apps.import_module") as import_mod:
                assert gm_apps._import_optional_managers_module(app_config) is True
        import_mod.assert_called_once_with("tests.custom_user_app.managers")

    def test_import_optional_managers_module_skips_missing_module(self) -> None:
        """Skip importing managers when the optional module is absent."""
        app_config = SimpleNamespace(name="tests.no_managers_app", label="no_managers")
        with patch("general_manager.apps.util.find_spec", return_value=None):
            with patch("general_manager.apps.import_module") as import_mod:
                assert gm_apps._import_optional_managers_module(app_config) is False
        import_mod.assert_not_called()

    def test_import_optional_managers_module_propagates_real_import_errors(
        self,
    ) -> None:
        """Propagate errors raised while importing an existing managers module."""
        app_config = SimpleNamespace(
            name="tests.custom_user_app",
            label="custom_user_app",
        )
        with patch("general_manager.apps.util.find_spec", return_value=object()):
            with patch(
                "general_manager.apps.import_module",
                side_effect=RuntimeError("boom"),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    gm_apps._import_optional_managers_module(app_config)
        with patch("general_manager.apps.util.find_spec", return_value=object()):
            with patch(
                "general_manager.apps.import_module",
                side_effect=ImportError("boom"),
            ):
                with self.assertRaisesRegex(ImportError, "boom"):
                    gm_apps._import_optional_managers_module(app_config)

    def test_autoload_app_managers_respects_explicit_empty_iterable(self) -> None:
        """Do not fall back to installed apps when caller passes no app configs."""
        with patch("general_manager.apps.django_apps.get_app_configs") as get_configs:
            imported = gm_apps._autoload_app_managers_modules([])

        assert imported == []
        get_configs.assert_not_called()

    def test_static_wrappers_forward_to_bootstrap_helpers(self) -> None:
        """Forward compatibility wrapper calls to bootstrap helpers."""
        pending: list[type[object]] = [object]
        all_classes: list[type[object]] = [str]
        schema = object()

        with (
            patch("general_manager.apps.install_startup_hook_runner") as install,
            patch("general_manager.apps.register_system_checks") as checks,
            patch("general_manager.apps.initialize_general_manager_classes") as init,
            patch("general_manager.apps.check_permission_class") as permission,
            patch("general_manager.apps.handle_graph_ql") as graph_ql,
            patch("general_manager.apps.handle_remote_api") as remote_api,
            patch("general_manager.apps.add_graphql_url") as graphql_url,
            patch("general_manager.apps._ensure_asgi_subscription_route") as asgi_route,
        ):
            gm_apps.GeneralmanagerConfig.install_startup_hook_runner()
            gm_apps.GeneralmanagerConfig.register_system_checks()
            gm_apps.GeneralmanagerConfig.initialize_general_manager_classes(
                pending,
                all_classes,
            )
            gm_apps.GeneralmanagerConfig.check_permission_class(object)
            gm_apps.GeneralmanagerConfig.handle_graph_ql(pending)
            gm_apps.GeneralmanagerConfig.handle_remote_api(all_classes)
            gm_apps.GeneralmanagerConfig.add_graphql_url(schema)
            gm_apps.GeneralmanagerConfig._ensure_asgi_subscription_route("graphql/")

        install.assert_called_once_with()
        checks.assert_called_once_with()
        init.assert_called_once_with(pending, all_classes)
        permission.assert_called_once_with(object)
        graph_ql.assert_called_once_with(pending)
        remote_api.assert_called_once_with(all_classes)
        graphql_url.assert_called_once_with(schema)
        asgi_route.assert_called_once_with("graphql/")

    def test_static_wrappers_propagate_bootstrap_errors(self) -> None:
        """Expose bootstrap helper errors unchanged through compatibility wrappers."""
        with patch(
            "general_manager.apps.add_graphql_url",
            side_effect=gm_apps.MissingRootUrlconfError,
        ):
            with self.assertRaises(gm_apps.MissingRootUrlconfError):
                gm_apps.GeneralmanagerConfig.add_graphql_url(object())

        with patch(
            "general_manager.apps.check_permission_class",
            side_effect=gm_apps.InvalidPermissionClassError("BadPermission"),
        ):
            with self.assertRaises(gm_apps.InvalidPermissionClassError):
                gm_apps.GeneralmanagerConfig.check_permission_class(object)

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_ready_autoloads_managers_before_initialization(self) -> None:
        """Autoload app managers before initializing manager classes."""
        config = gm_apps.GeneralmanagerConfig(
            "general_manager", import_module("general_manager")
        )
        config.apps = SimpleNamespace(get_app_configs=lambda: ())
        call_order: list[str] = []

        with patch.object(
            config,
            "install_startup_hook_runner",
            side_effect=lambda: call_order.append("install_runner"),
        ):
            with patch.object(
                config,
                "register_system_checks",
                side_effect=lambda: call_order.append("register_checks"),
            ):
                with (
                    patch(
                        "general_manager.uploads.checks.register_upload_checks",
                        side_effect=lambda: call_order.append("register_upload_checks"),
                    ),
                    patch(
                        "general_manager.search.checks.register_search_checks",
                        side_effect=lambda: call_order.append("register_search_checks"),
                    ),
                    patch(
                        "general_manager.apps._autoload_app_managers_modules",
                        side_effect=lambda *_args, **_kwargs: call_order.append(
                            "autoload"
                        ),
                    ),
                ):
                    with patch.object(
                        config,
                        "initialize_general_manager_classes",
                        side_effect=lambda *_args, **_kwargs: call_order.append(
                            "initialize"
                        ),
                    ):
                        with (
                            patch(
                                "general_manager.apps.configure_audit_logger_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_audit"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_search_backend_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_search"
                                ),
                            ),
                            patch(
                                "general_manager.search.invalidation.configure_search_invalidation",
                                side_effect=lambda: call_order.append(
                                    "configure_search_invalidation"
                                ),
                            ),
                            patch(
                                "general_manager.search.m2m_invalidation.configure_search_m2m_invalidation",
                                side_effect=lambda: call_order.append(
                                    "configure_search_m2m_invalidation"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_workflow_engine_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_workflow_engine"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_event_registry_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_event_registry"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_workflow_signal_bridge_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_signal_bridge"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_workflow_beat_schedule_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_beat_schedule"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_search_reconcile_beat_schedule_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_search_reconcile_beat_schedule"
                                ),
                            ),
                            patch(
                                "general_manager.apps.configure_graphql_warmup_beat_schedule_from_settings",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "configure_graphql_warmup_beat_schedule"
                                ),
                            ),
                        ):
                            config.ready()

        assert call_order == [
            "install_runner",
            "register_checks",
            "register_upload_checks",
            "register_search_checks",
            "autoload",
            "initialize",
            "configure_audit",
            "configure_search",
            "configure_search_invalidation",
            "configure_search_m2m_invalidation",
            "configure_workflow_engine",
            "configure_event_registry",
            "configure_signal_bridge",
            "configure_beat_schedule",
            "configure_search_reconcile_beat_schedule",
            "configure_graphql_warmup_beat_schedule",
        ]

    @override_settings(GENERAL_MANAGER={"AUTOCREATE_GRAPHQL": True})
    def test_ready_builds_graphql_after_startup_configuration(self) -> None:
        """Build GraphQL only after app startup configuration has run."""
        config = gm_apps.GeneralmanagerConfig(
            "general_manager", import_module("general_manager")
        )
        config.apps = SimpleNamespace(get_app_configs=lambda: ())
        pending_graphql = [object]
        original_pending = gm_apps.GeneralManagerMeta.pending_graphql_interfaces
        gm_apps.GeneralManagerMeta.pending_graphql_interfaces = pending_graphql
        call_order: list[str] = []

        try:
            with patch.object(
                config,
                "install_startup_hook_runner",
                side_effect=lambda: call_order.append("install_runner"),
            ):
                with patch.object(
                    config,
                    "register_system_checks",
                    side_effect=lambda: call_order.append("register_checks"),
                ):
                    with (
                        patch(
                            "general_manager.uploads.checks.register_upload_checks",
                            side_effect=lambda: call_order.append(
                                "register_upload_checks"
                            ),
                        ),
                        patch(
                            "general_manager.search.checks.register_search_checks",
                            side_effect=lambda: call_order.append(
                                "register_search_checks"
                            ),
                        ),
                        patch(
                            "general_manager.apps._autoload_app_managers_modules",
                            side_effect=lambda *_args, **_kwargs: call_order.append(
                                "autoload"
                            ),
                        ),
                    ):
                        with patch.object(
                            config,
                            "initialize_general_manager_classes",
                            side_effect=lambda *_args, **_kwargs: call_order.append(
                                "initialize"
                            ),
                        ):
                            with (
                                patch(
                                    "general_manager.apps.handle_remote_api",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("remote_api")
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_audit_logger_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("configure_audit")
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_search_backend_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("configure_search")
                                    ),
                                ),
                                patch(
                                    "general_manager.search.invalidation.configure_search_invalidation",
                                    side_effect=lambda: call_order.append(
                                        "configure_search_invalidation"
                                    ),
                                ),
                                patch(
                                    "general_manager.search.m2m_invalidation.configure_search_m2m_invalidation",
                                    side_effect=lambda: call_order.append(
                                        "configure_search_m2m_invalidation"
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_workflow_engine_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("configure_workflow_engine")
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_event_registry_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("configure_event_registry")
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_workflow_signal_bridge_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("configure_signal_bridge")
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_workflow_beat_schedule_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append("configure_beat_schedule")
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_search_reconcile_beat_schedule_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append(
                                            "configure_search_reconcile_beat_schedule"
                                        )
                                    ),
                                ),
                                patch(
                                    "general_manager.apps.configure_graphql_warmup_beat_schedule_from_settings",
                                    side_effect=lambda *_args, **_kwargs: (
                                        call_order.append(
                                            "configure_graphql_warmup_beat_schedule"
                                        )
                                    ),
                                ),
                                patch(
                                    "general_manager.conf.get_setting",
                                    return_value=True,
                                ),
                                patch(
                                    "general_manager.apps.handle_graph_ql",
                                    side_effect=lambda managers: call_order.append(
                                        f"graphql:{managers is pending_graphql}"
                                    ),
                                ),
                            ):
                                config.ready()
        finally:
            gm_apps.GeneralManagerMeta.pending_graphql_interfaces = original_pending

        assert call_order == [
            "install_runner",
            "register_checks",
            "register_upload_checks",
            "register_search_checks",
            "autoload",
            "initialize",
            "remote_api",
            "configure_audit",
            "configure_search",
            "configure_search_invalidation",
            "configure_search_m2m_invalidation",
            "configure_workflow_engine",
            "configure_event_registry",
            "configure_signal_bridge",
            "configure_beat_schedule",
            "configure_search_reconcile_beat_schedule",
            "configure_graphql_warmup_beat_schedule",
            "graphql:True",
        ]
