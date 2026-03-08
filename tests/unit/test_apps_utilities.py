from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.test.utils import override_settings
from unittest.mock import patch

from general_manager import apps as gm_apps


class AppsUtilitiesTests(SimpleTestCase):
    def tearDown(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        super().tearDown()

    def test_normalize_graphql_path(self) -> None:
        assert gm_apps._normalize_graphql_path("graphql") == "/graphql/"
        assert gm_apps._normalize_graphql_path("/graphql") == "/graphql/"
        assert gm_apps._normalize_graphql_path("/graphql/") == "/graphql/"

    def test_should_auto_reindex(self) -> None:
        settings = SimpleNamespace(
            GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True}, DEBUG=True
        )
        assert gm_apps._should_auto_reindex(settings) is True

        settings = SimpleNamespace(
            GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True}, DEBUG=False
        )
        assert gm_apps._should_auto_reindex(settings) is False

    def test_auto_reindex_search_skips_invalid_env(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        gm_apps._auto_reindex_search()
        gm_apps._auto_reindex_search(environ={"PATH_INFO": None})
        assert gm_apps._SEARCH_REINDEXED is False

    def test_auto_reindex_search_triggers_on_graphql_path(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        with patch("general_manager.apps.call_command") as call_command:
            gm_apps._auto_reindex_search(environ={"PATH_INFO": "/graphql"})
            call_command.assert_called_once_with("search_index", reindex=True)
            assert gm_apps._SEARCH_REINDEXED is True
        gm_apps._SEARCH_REINDEXED = False

    def test_import_optional_managers_module_imports_existing_module(self) -> None:
        app_config = SimpleNamespace(
            name="tests.custom_user_app",
            label="custom_user_app",
        )
        with patch("general_manager.apps.util.find_spec", return_value=object()):
            with patch("general_manager.apps.import_module") as import_mod:
                assert gm_apps._import_optional_managers_module(app_config) is True
        import_mod.assert_called_once_with("tests.custom_user_app.managers")

    def test_import_optional_managers_module_skips_missing_module(self) -> None:
        app_config = SimpleNamespace(name="tests.no_managers_app", label="no_managers")
        with patch("general_manager.apps.util.find_spec", return_value=None):
            with patch("general_manager.apps.import_module") as import_mod:
                assert gm_apps._import_optional_managers_module(app_config) is False
        import_mod.assert_not_called()

    def test_import_optional_managers_module_propagates_real_import_errors(
        self,
    ) -> None:
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

    @override_settings(AUTOCREATE_GRAPHQL=False)
    def test_ready_autoloads_managers_before_initialization(self) -> None:
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
                with patch(
                    "general_manager.apps._autoload_app_managers_modules",
                    side_effect=lambda *_args, **_kwargs: call_order.append("autoload"),
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
                            patch.object(
                                config,
                                "install_search_auto_reindex",
                                side_effect=lambda *_args, **_kwargs: call_order.append(
                                    "install_search_auto_reindex"
                                ),
                            ),
                        ):
                            config.ready()

        assert call_order == [
            "install_runner",
            "register_checks",
            "autoload",
            "initialize",
            "configure_audit",
            "configure_search",
            "configure_workflow_engine",
            "configure_event_registry",
            "configure_signal_bridge",
            "configure_beat_schedule",
            "install_search_auto_reindex",
        ]
