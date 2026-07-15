"""Commit-safe direct search invalidation signal bridge."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, cast

from django.db import DEFAULT_DB_ALIAS, DatabaseError, transaction

from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.search.async_tasks import (
    dispatch_delete_documents,
    dispatch_index_update,
)
from general_manager.search.indexer import SearchDeleteTarget, capture_delete_targets
from general_manager.search.backend import SearchBackendError
from general_manager.search.reconciliation import (
    DirtySearchIndex,
    acknowledge_search_index_dirty,
    mark_search_index_dirty,
)
from general_manager.search.registry import get_search_config

logger = get_logger("search.invalidation")

_DIRECT_SEARCH_CHANGE_CONTEXT = "general_manager.search.direct_change"
_PRE_DISPATCH_UID = "general_manager.search.invalidation.pre"
_POST_DISPATCH_UID = "general_manager.search.invalidation.post"
_DirectAction = Literal["create", "update", "delete"]
_EXPECTED_SEARCH_FAILURES = (
    SearchBackendError,
    DatabaseError,
    ImportError,
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    LookupError,
)


@dataclass
class _PendingDirectSearchChange:
    """Per-lifecycle data that is safe to retain until post-change handling."""

    action: _DirectAction
    delete_targets: tuple[SearchDeleteTarget, ...] = ()


def _manager_class(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
) -> type[GeneralManager] | None:
    """Resolve the concrete manager class supplied by a lifecycle signal."""
    if instance is not None:
        return instance.__class__
    if isinstance(sender, type) and issubclass(sender, GeneralManager):
        return sender
    if isinstance(sender, GeneralManager):
        return sender.__class__
    return None


def _index_names(manager_class: type[GeneralManager]) -> tuple[str, ...]:
    """Return each configured index name once in declaration order."""
    config = get_search_config(manager_class)
    if config is None:
        return ()
    return tuple(dict.fromkeys(index.name for index in config.indexes))


def _mark_pairs(
    manager_class: type[GeneralManager],
    index_names: tuple[str, ...],
    *,
    action: _DirectAction,
) -> tuple[DirtySearchIndex, ...]:
    """Best-effort mark each affected pair and retain its generation fence."""
    tokens: list[DirtySearchIndex] = []
    for index_name in index_names:
        try:
            token = mark_search_index_dirty(manager_class, index_name)
        except Exception as exc:  # noqa: BLE001 - user marker hooks are open-ended
            logger.warning(
                "search dirty marker failed",
                context={
                    "manager": manager_class.__name__,
                    "index": index_name,
                    "action": action,
                },
                exc_info=exc,
            )
            continue
        if token is not None:
            tokens.append(token)
    return tuple(tokens)


def _acknowledge_tokens(tokens: tuple[DirtySearchIndex, ...]) -> None:
    """Acknowledge successful incremental work under its generation fences."""
    for token in tokens:
        acknowledge_search_index_dirty(token)


def _run_post_commit_safely(
    callback: Callable[[], None],
    *,
    manager_class: type[GeneralManager],
    action: _DirectAction,
) -> None:
    """Fence the open exception taxonomy of post-commit search integrations."""
    try:
        callback()
    except Exception as exc:  # noqa: BLE001 - brokers/backends expose open errors
        logger.warning(
            "search post-commit callback failed",
            context={"manager": manager_class.__name__, "action": action},
            exc_info=exc,
        )


def _dispatch_index_after_commit(
    *,
    manager_class: type[GeneralManager],
    manager_path: str,
    identification: dict[str, object],
    index_names: tuple[str, ...],
    tokens: tuple[DirtySearchIndex, ...],
    action: _DirectAction,
) -> None:
    """Reconstruct and dispatch exact-pair indexing after business commit."""
    tokens_by_index = {token.index_name: token for token in tokens}
    try:
        instance = manager_class(**identification)
    except Exception as exc:  # noqa: BLE001 - manager constructors are extensible
        logger.warning(
            "search manager reconstruction failed",
            context={"manager": manager_class.__name__, "action": action},
            exc_info=exc,
        )
        return

    for index_name in index_names:
        try:
            dispatch_index_update(
                action="index",
                manager_path=manager_path,
                identification=identification,
                instance=instance,
                index_name=index_name,
            )
            token = tokens_by_index.get(index_name)
            if token is not None:
                _acknowledge_tokens((token,))
        except _EXPECTED_SEARCH_FAILURES as exc:
            logger.warning(
                "search indexing failed",
                context={
                    "manager": manager_class.__name__,
                    "index": index_name,
                    "action": action,
                },
                exc_info=exc,
            )


def _dispatch_delete_after_commit(
    *,
    manager_class: type[GeneralManager],
    manager_path: str,
    targets: tuple[dict[str, str], ...],
    tokens: tuple[DirtySearchIndex, ...],
) -> None:
    """Dispatch captured deletion IDs and acknowledge only complete success."""
    if not targets:
        return
    target_index_names = {target["index_name"] for target in targets}
    completed_tokens = tuple(
        token for token in tokens if token.index_name in target_index_names
    )
    try:
        expected_generations = {
            token.index_name: token.generation for token in completed_tokens
        }
        dispatch_delete_documents(
            manager_path,
            targets,
            expected_generations=expected_generations or None,
        )
        _acknowledge_tokens(completed_tokens)
    except Exception as exc:  # noqa: BLE001 - user document-ID hooks are open-ended
        logger.warning(
            "search deletion failed",
            context={"manager": manager_class.__name__, "action": "delete"},
            exc_info=exc,
        )


def _handle_search_pre_change(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
    action: str | None = None,
    change_context: dict[str, object] | None = None,
    **_: object,
) -> None:
    """Capture direct-delete IDs while the source manager is still readable."""
    if action != "delete" or instance is None or change_context is None:
        return
    targets: tuple[SearchDeleteTarget, ...] = ()
    try:
        targets = capture_delete_targets(instance)
    except Exception as exc:  # noqa: BLE001 - user document-ID hooks are open-ended
        logger.warning(
            "search delete target capture failed",
            context={"manager": instance.__class__.__name__, "action": action},
            exc_info=exc,
        )
    change_context[_DIRECT_SEARCH_CHANGE_CONTEXT] = _PendingDirectSearchChange(
        action="delete",
        delete_targets=targets,
    )


def _handle_search_post_change(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
    action: str | None = None,
    change_context: dict[str, object] | None = None,
    database_alias: str = DEFAULT_DB_ALIAS,
    **_: object,
) -> None:
    """Mark direct changes dirty and register one commit-bound callback."""
    if action not in {"create", "update", "delete"} or change_context is None:
        return
    direct_action = cast(_DirectAction, action)
    manager_class = _manager_class(sender, instance)
    if manager_class is None:
        return
    try:
        index_names = _index_names(manager_class)
    except Exception as exc:  # noqa: BLE001 - user config hooks are open-ended
        logger.warning(
            "search configuration resolution failed",
            context={"manager": manager_class.__name__, "action": action},
            exc_info=exc,
        )
        return
    if not index_names:
        return

    manager_path = f"{manager_class.__module__}.{manager_class.__name__}"
    if direct_action == "delete":
        pending = change_context.get(_DIRECT_SEARCH_CHANGE_CONTEXT)
        delete_targets = (
            pending.delete_targets
            if isinstance(pending, _PendingDirectSearchChange)
            and pending.action == "delete"
            else ()
        )
        serialized_targets = tuple(
            {
                "index_name": target.index_name,
                "document_id": target.document_id,
            }
            for target in delete_targets
        )

        def dispatch(tokens: tuple[DirtySearchIndex, ...]) -> None:
            _dispatch_delete_after_commit(
                manager_class=manager_class,
                manager_path=manager_path,
                targets=serialized_targets,
                tokens=tokens,
            )

    else:
        if instance is None:
            return
        try:
            identification = deepcopy(instance.identification)
        except Exception as exc:  # noqa: BLE001 - manager metadata is extensible
            logger.warning(
                "search change metadata capture failed",
                context={"manager": manager_class.__name__, "action": action},
                exc_info=exc,
            )

            def dispatch(tokens: tuple[DirtySearchIndex, ...]) -> None:
                """Leave marker-only fallback work for reconciliation."""
                del tokens

        else:

            def dispatch(tokens: tuple[DirtySearchIndex, ...]) -> None:
                _dispatch_index_after_commit(
                    manager_class=manager_class,
                    manager_path=manager_path,
                    identification=identification,
                    index_names=index_names,
                    tokens=tokens,
                    action=direct_action,
                )

    if database_alias == DEFAULT_DB_ALIAS:
        tokens = _mark_pairs(manager_class, index_names, action=direct_action)
        transaction.on_commit(
            lambda: _run_post_commit_safely(
                lambda: dispatch(tokens),
                manager_class=manager_class,
                action=direct_action,
            ),
            using=database_alias,
        )
        return

    def after_non_default_commit() -> None:
        # The source transaction cannot atomically include the default-DB control
        # plane. A process crash after source commit but before this callback is
        # the explicitly accepted gap; a cross-database outbox is out of scope.
        tokens = _mark_pairs(manager_class, index_names, action=direct_action)
        dispatch(tokens)

    transaction.on_commit(
        lambda: _run_post_commit_safely(
            after_non_default_commit,
            manager_class=manager_class,
            action=direct_action,
        ),
        using=database_alias,
    )


def configure_search_invalidation() -> None:
    """Idempotently connect the direct lifecycle invalidation receivers."""
    pre_data_change.connect(
        _handle_search_pre_change,
        dispatch_uid=_PRE_DISPATCH_UID,
        weak=False,
    )
    post_data_change.connect(
        _handle_search_post_change,
        dispatch_uid=_POST_DISPATCH_UID,
        weak=False,
    )
