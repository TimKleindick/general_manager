from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from general_manager.api import warmup_async
from general_manager.api.graphql import GraphQL
from general_manager.manager.general_manager import GeneralManager


class GraphQLWarmupAsyncTests(SimpleTestCase):
    def setUp(self) -> None:
        self._original_registry = GraphQL.manager_registry.copy()

    def tearDown(self) -> None:
        GraphQL.manager_registry = self._original_registry

    @override_settings(GRAPHQL_WARMUP_ASYNC=False)
    def test_dispatch_returns_false_when_async_disabled(self) -> None:
        with patch("general_manager.api.warmup_async.CELERY_AVAILABLE", True):
            dispatched = warmup_async.dispatch_graphql_warmup([GeneralManager])
        self.assertFalse(dispatched)

    @override_settings(GRAPHQL_WARMUP_ENABLED=False, GRAPHQL_WARMUP_ASYNC=True)
    def test_dispatch_returns_false_when_warmup_globally_disabled(self) -> None:
        with patch("general_manager.api.warmup_async.CELERY_AVAILABLE", True):
            dispatched = warmup_async.dispatch_graphql_warmup([GeneralManager])
        self.assertFalse(dispatched)

    @override_settings(GRAPHQL_WARMUP_ASYNC=True)
    def test_dispatch_returns_false_when_celery_unavailable(self) -> None:
        with patch("general_manager.api.warmup_async.CELERY_AVAILABLE", False):
            dispatched = warmup_async.dispatch_graphql_warmup([GeneralManager])
        self.assertFalse(dispatched)

    @override_settings(GRAPHQL_WARMUP_ASYNC=True)
    def test_dispatch_enqueues_task_when_available(self) -> None:
        mock_delay = Mock()
        with (
            patch("general_manager.api.warmup_async.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.warmup_async.warm_up_graphql_properties_task",
                new=SimpleNamespace(delay=mock_delay),
            ),
        ):
            dispatched = warmup_async.dispatch_graphql_warmup([GeneralManager])

        self.assertTrue(dispatched)
        mock_delay.assert_called_once_with(
            ["general_manager.manager.general_manager.GeneralManager"]
        )

    @override_settings(GRAPHQL_WARMUP_ASYNC=True)
    def test_dispatch_skips_non_importable_local_class(self) -> None:
        class LocalManager(GeneralManager):
            pass

        mock_delay = Mock()
        with (
            patch("general_manager.api.warmup_async.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.warmup_async.warm_up_graphql_properties_task",
                new=SimpleNamespace(delay=mock_delay),
            ),
        ):
            dispatched = warmup_async.dispatch_graphql_warmup([LocalManager])

        self.assertFalse(dispatched)
        mock_delay.assert_not_called()

    def test_task_resolves_manager_paths_and_calls_warmup(self) -> None:
        with patch(
            "general_manager.api.warmup_async.warm_up_graphql_properties"
        ) as warmup_mock:
            warmup_async.warm_up_graphql_properties_task(
                ["general_manager.manager.general_manager.GeneralManager"]
            )

        warmup_mock.assert_called_once()
        manager_classes = warmup_mock.call_args.args[0]
        self.assertEqual(manager_classes, [GeneralManager])

    def test_dispatch_for_dependencies_enqueues_warmup_managers(self) -> None:
        class _WarmProp:
            warm_up = True

        class _Iface:
            @classmethod
            def get_graph_ql_properties(cls):
                return {"score": _WarmProp()}

        class _Manager:
            Interface = _Iface

        GraphQL.manager_registry = {"XManager": _Manager}  # type: ignore[assignment]
        deps = {("XManager", "identification", "{'id': 1}")}

        with patch(
            "general_manager.api.warmup_async.dispatch_graphql_warmup",
            return_value=True,
        ) as dispatch_mock:
            warmup_async.dispatch_graphql_warmup_for_dependencies(deps)

        dispatch_mock.assert_called_once_with([_Manager])  # type: ignore[arg-type]

    def test_dispatch_for_dependencies_skips_when_enqueue_unavailable(self) -> None:
        class _WarmProp:
            warm_up = True

        class _Iface:
            @classmethod
            def get_graph_ql_properties(cls):
                return {"score": _WarmProp()}

        class _Manager:
            Interface = _Iface

        GraphQL.manager_registry = {"XManager": _Manager}  # type: ignore[assignment]
        deps = {("XManager", "identification", "{'id': 1}")}

        with (
            patch(
                "general_manager.api.warmup_async.dispatch_graphql_warmup",
                return_value=False,
            ),
            patch(
                "general_manager.api.warmup_async.warm_up_graphql_properties"
            ) as warmup_mock,
        ):
            warmup_async.dispatch_graphql_warmup_for_dependencies(deps)

        warmup_mock.assert_not_called()

    @override_settings(GRAPHQL_WARMUP_ENABLED=False)
    def test_dispatch_for_dependencies_skips_when_globally_disabled(self) -> None:
        with (
            patch(
                "general_manager.api.warmup_async.dispatch_graphql_warmup"
            ) as dispatch_mock,
            patch(
                "general_manager.api.warmup_async.warm_up_graphql_properties"
            ) as warmup_mock,
        ):
            warmup_async.dispatch_graphql_warmup_for_dependencies(
                {("XManager", "identification", "{'id': 1}")}
            )
        dispatch_mock.assert_not_called()
        warmup_mock.assert_not_called()
