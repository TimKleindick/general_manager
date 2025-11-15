from __future__ import annotations

import logging

import pytest

from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.cache.cache_decorator import cached
from general_manager.cache.dependency_index import (
    generic_cache_invalidation,
    set_full_index,
)
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.base_permission import (
    BasePermission,
    PermissionCheckError,
)
from general_manager.interface import ReadOnlyInterface
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)
from general_manager.utils.public_api import MissingExportError, resolve_export


class DummyInterface:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if args:
            self.identification = args[0]
        elif kwargs:
            self.identification = kwargs
        else:
            self.identification = {"id": "dummy"}

    @classmethod
    def filter(cls, *args: object, **kwargs: object) -> list[object]:
        return []

    @classmethod
    def exclude(cls, *args: object, **kwargs: object) -> list[object]:
        return []

    @classmethod
    def create(cls, *args: object, **kwargs: object) -> dict[str, object]:
        return {"id": "created"}

    def update(self, *args: object, **kwargs: object) -> None:
        """
        No-op placeholder for updating an interface; accepts any positional and keyword arguments but performs no action.

        Parameters:
            *args: Positional arguments are accepted and ignored.
            **kwargs: Keyword arguments are accepted and ignored.
        """
        return None

    def delete(self, *args: object, **kwargs: object) -> None:
        """
        No-op delete method that exists as a placeholder to satisfy the interface.
        """
        return None


def test_get_logger_uses_general_manager_namespace() -> None:
    adapter = get_logger("permission.base")

    assert adapter.logger.name == "general_manager.permission.base"
    assert adapter.extra["component"] == "permission.base"


def test_adapter_enriches_log_records(caplog: pytest.LogCaptureFixture) -> None:
    adapter = get_logger("cache.dependency_index")

    with caplog.at_level(logging.INFO, logger="general_manager.cache.dependency_index"):
        adapter.info("invalidated cache", context={"cache_key": "test"})

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.component == "cache.dependency_index"
    assert record.context == {"cache_key": "test"}


def test_adapter_validates_context_type() -> None:
    adapter = get_logger("apps")

    with pytest.raises(TypeError):
        adapter.info("context must be a mapping", context="user=1")  # type: ignore[arg-type]


def test_adapter_merges_existing_extra_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = get_logger("interface.read_only")

    with caplog.at_level(logging.INFO, logger="general_manager.interface.read_only"):
        adapter.info(
            "synced",
            extra={"context": {"model": "Project"}},
            context={"count": 5},
        )

    record = caplog.records[0]
    assert record.context == {"model": "Project", "count": 5}


def test_general_manager_logging_for_create_and_queries() -> None:
    class AllowPermission(BasePermission):
        def check_permission(self, action: str, attribute: str) -> bool:  # type: ignore[override]
            return True

    original_interface = getattr(GeneralManager, "Interface", None)
    original_permission = getattr(GeneralManager, "Permission", None)
    original_attributes = getattr(GeneralManager, "_attributes", None)
    try:
        GeneralManager.Interface = DummyInterface  # type: ignore[assignment]
        GeneralManager.Permission = AllowPermission  # type: ignore[assignment]
        GeneralManager._attributes = {}

        with patch("general_manager.manager.general_manager.logger") as mock_logger:
            GeneralManager.create(ignore_permission=True, foo="bar")
            GeneralManager.filter(status="active")
            GeneralManager.exclude(status="inactive")
            GeneralManager.all()

        created_context = next(
            c.kwargs["context"]
            for c in mock_logger.info.call_args_list
            if c.args and c.args[0] == "manager created"
        )
        assert created_context["fields"] == ["foo"]
        assert created_context["manager"] == "GeneralManager"

        instantiated_context = next(
            c.kwargs["context"]
            for c in mock_logger.debug.call_args_list
            if c.args and c.args[0] == "instantiated manager"
        )
        assert instantiated_context["identification"] == {"id": "created"}

        filter_context = next(
            c.kwargs["context"]
            for c in mock_logger.debug.call_args_list
            if c.args and c.args[0] == "manager filter"
        )
        assert filter_context["filters"] == {"status": "active"}

        exclude_context = next(
            c.kwargs["context"]
            for c in mock_logger.debug.call_args_list
            if c.args and c.args[0] == "manager exclude"
        )
        assert exclude_context["filters"] == {"status": "inactive"}

        all_context = next(
            c.kwargs["context"]
            for c in mock_logger.debug.call_args_list
            if c.args and c.args[0] == "manager all"
        )
        assert all_context["manager"] == "GeneralManager"
    finally:
        if original_interface is not None:
            GeneralManager.Interface = original_interface  # type: ignore[assignment]
        if original_permission is not None:
            GeneralManager.Permission = original_permission  # type: ignore[assignment]
        if original_attributes is not None:
            GeneralManager._attributes = original_attributes


def test_cached_decorator_logs_cache_hit_and_miss() -> None:
    class FakeCache:
        def __init__(self) -> None:
            self.storage: dict[str, object] = {}

        def get(self, key: str, default: object | None = None) -> object | None:
            return self.storage.get(key, default)

        def set(
            self, key: str, value: object, timeout: int | None = None
        ) -> None:  # pragma: no cover - interface compatibility
            self.storage[key] = value

    fake_cache = FakeCache()

    with patch("general_manager.cache.cache_decorator.logger") as mock_logger:

        @cached(timeout=None, cache_backend=fake_cache, record_fn=lambda _k, _d: None)
        def compute(x: int) -> int:
            return x * 2

        assert compute(3) == 6  # miss
        assert compute(3) == 6  # hit

    debug_messages = [c.args[0] for c in mock_logger.debug.call_args_list]
    assert "cache miss recorded" in debug_messages
    assert "cache hit" in debug_messages


def test_permission_check_logs_denial() -> None:
    class AlwaysDenyPermission(BasePermission):
        def check_permission(self, action: str, attribute: str) -> bool:  # type: ignore[override]
            return False

    user = AnonymousUser()

    with patch("general_manager.permission.base_permission.logger") as mock_logger:
        with pytest.raises(PermissionCheckError):
            AlwaysDenyPermission.check_create_permission(
                {"field": "value"},
                GeneralManager,
                user,
            )

    assert mock_logger.info.call_args_list
    call_args = mock_logger.info.call_args_list[0]
    assert call_args.args[0] == "permission denied"
    context = call_args.kwargs["context"]
    assert context["action"] == "create"
    assert context["attribute"] == "field"
    assert context["user_id"] is None


def test_rule_engine_logs_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    from general_manager.rule.rule import Rule

    class Dummy:
        value = 1

    failing_rule = Rule(lambda obj: obj.value > 10)

    with caplog.at_level(logging.INFO, logger="general_manager.rule.engine"):
        assert failing_rule.evaluate(Dummy()) is False
        failing_rule.get_error_message()

    messages = [record.message for record in caplog.records]
    assert "rule evaluation failed" in messages


def test_dependency_index_logs_invalidation() -> None:
    cache.clear()
    set_full_index(
        {
            "filter": {
                "FakeManager": {
                    "status": {
                        "'active'": {"cache-key"},
                    }
                }
            },
            "exclude": {},
        }
    )

    FakeManager = type("FakeManager", (), {})
    instance = SimpleNamespace(status="active", identification={"id": 1})

    with (
        patch("general_manager.cache.dependency_index.logger") as mock_logger,
        patch("general_manager.cache.dependency_index.invalidate_cache_key"),
        patch("general_manager.cache.dependency_index.remove_cache_key_from_index"),
    ):
        generic_cache_invalidation(
            FakeManager,
            instance,
            old_relevant_values={"status": "inactive"},
        )

    info_calls = [
        call.kwargs
        for call in mock_logger.info.call_args_list
        if call.args and call.args[0] == "invalidating cache key"
    ]
    assert info_calls
    assert info_calls[0]["context"]["key"] == "cache-key"


def test_apps_logging_when_asgi_missing() -> None:
    from general_manager.apps import GeneralmanagerConfig

    with (
        patch("general_manager.apps.logger") as mock_logger,
        override_settings(ASGI_APPLICATION=None),
    ):
        GeneralmanagerConfig._ensure_asgi_subscription_route("/graphql/")

    assert mock_logger.debug.call_args_list
    call_args = mock_logger.debug.call_args_list[0]
    assert call_args.args[0] == "asgi application missing"
    assert call_args.kwargs["context"]["graphql_url"] == "/graphql/"


def test_read_only_interface_schema_warning_logs() -> None:
    FakeModel = type("FakeModel", (), {"__name__": "FakeModel"})
    FakeParent = type("FakeParent", (), {"__name__": "FakeParent"})

    class TrivialReadOnly(ReadOnlyInterface):
        _model = FakeModel  # type: ignore[assignment]
        _parent_class = FakeParent  # type: ignore[assignment]

    with (
        patch.object(
            ReadOnlyManagementCapability,
            "ensure_schema_is_up_to_date",
            return_value=True,
        ),
        patch("general_manager.interface.capabilities.read_only.logger") as mock_logger,
    ):
        capability = ReadOnlyManagementCapability()
        capability.sync_data(TrivialReadOnly)

    warning_call = mock_logger.warning.call_args_list[0]
    assert warning_call.args[0] == "readonly schema out of date"
    assert warning_call.kwargs["context"]["manager"] == "FakeParent"


def test_utils_public_api_logging() -> None:
    with patch("general_manager.utils.public_api.logger") as mock_logger:
        module_globals = {"__name__": "tests.module"}
        with pytest.raises(MissingExportError):
            resolve_export(
                "unknown",
                module_all={"known"},
                module_map={"known": ("general_manager.logging", "get_logger")},
                module_globals=module_globals,
            )
        warning_call = mock_logger.warning.call_args_list[0]
        assert warning_call.args[0] == "missing public api export"

    with patch("general_manager.utils.public_api.logger") as mock_logger:
        module_globals = {"__name__": "tests.module"}
        resolve_export(
            "known",
            module_all={"known"},
            module_map={"known": ("general_manager.logging", "get_logger")},
            module_globals=module_globals,
        )
        debug_call = mock_logger.debug.call_args_list[0]
        assert debug_call.args[0] == "resolved public api export"
        assert debug_call.kwargs["context"]["export"] == "known"


def test_api_graphql_error_logging() -> None:
    with patch("general_manager.api.graphql.logger") as mock_logger:
        GraphQL._handle_graph_ql_error(PermissionError("denied"))
        info_call = mock_logger.info.call_args_list[0]
        assert info_call.args[0] == "graphql permission error"
        assert info_call.kwargs["context"]["error"] == "PermissionError"

    with patch("general_manager.api.graphql.logger") as mock_logger:
        GraphQL._handle_graph_ql_error(ValueError("bad input"))
        warning_call = mock_logger.warning.call_args_list[0]
        assert warning_call.args[0] == "graphql user error"
        assert warning_call.kwargs["context"]["error"] == "ValueError"

    with patch("general_manager.api.graphql.logger") as mock_logger:
        GraphQL._handle_graph_ql_error(RuntimeError("boom"))
        error_call = mock_logger.error.call_args_list[0]
        assert error_call.args[0] == "graphql internal error"
        assert error_call.kwargs["context"]["error"] == "RuntimeError"


def test_manager_meta_logging_on_class_creation() -> None:
    with patch("general_manager.manager.meta.logger") as mock_logger:

        class LoggedManager(GeneralManager):
            pass

    debug_messages = [c.args[0] for c in mock_logger.debug.call_args_list]
    assert "creating manager class" in debug_messages

    try:
        GeneralManagerMeta.pending_graphql_interfaces.remove(LoggedManager)
    except ValueError:  # pragma: no cover - not appended
        pass
