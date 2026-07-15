"""Commit-safe direct search invalidation signal bridge."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import islice
from types import MappingProxyType
from typing import Literal, cast

from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils.module_loading import import_string

from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.conf import get_setting
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.async_tasks import (
    dispatch_delete_documents,
    dispatch_index_update,
)
from general_manager.search.indexer import SearchDeleteTarget, capture_delete_targets
from general_manager.search.config import IndexConfig, SearchChange
from general_manager.search.reconciliation import (
    DirtySearchIndex,
    acknowledge_search_index_dirty,
    mark_search_index_dirty,
)
from general_manager.search.registry import get_search_config
from general_manager.search.utils import normalize_identification

logger = get_logger("search.invalidation")

_DIRECT_SEARCH_CHANGE_CONTEXT = "general_manager.search.direct_change"
_RELATED_SEARCH_CHANGE_CONTEXT = "general_manager.search.related_capture"
_PRE_DISPATCH_UID = "general_manager.search.invalidation.pre"
_POST_DISPATCH_UID = "general_manager.search.invalidation.post"
_DirectAction = Literal["create", "update", "delete"]
type SearchInvalidationKey = tuple[str, str, str, str]
type SearchRuleKey = tuple[str, int]


class InvalidSearchInvalidationSettingError(ValueError):
    """Raised when a bounded invalidation setting is not a positive integer."""

    def __init__(self, name: str) -> None:
        """Build the stable setting validation message."""
        super().__init__(f"{name} must be a positive integer.")


class _InvalidSearchInvalidationSourceError(TypeError):
    """Raised internally when a rule source is not a manager class."""


class _SearchInvalidationTargetLimitError(OverflowError):
    """Raised internally when a resolver exceeds the remaining event budget."""


class _InvalidSearchInvalidationTargetError(TypeError):
    """Raised internally when a resolver yields the wrong manager type."""


def _positive_int_setting(name: str, default: int) -> int:
    """Read and validate a positive non-boolean integer setting."""
    value = get_setting(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidSearchInvalidationSettingError(name)
    return value


@dataclass(frozen=True)
class SearchInvalidationTarget:
    """Immutable targeted owner/index work captured during a source mutation."""

    owner_class: type[GeneralManager]
    owner_path: str
    identification: Mapping[str, object]
    index_name: str
    database_alias: str
    canonical_key: SearchInvalidationKey


@dataclass(frozen=True)
class SearchInvalidationPair:
    """One exact owner/index pair requiring durable reconciliation fallback."""

    owner_class: type[GeneralManager]
    index_name: str


@dataclass(frozen=True)
class SearchRuleResolution:
    """Targets or fallback state produced by one stable owner rule."""

    key: SearchRuleKey
    owner_class: type[GeneralManager]
    index_names: tuple[str, ...]
    targets: tuple[SearchInvalidationTarget, ...] = ()
    fallback: bool = False


@dataclass(frozen=True)
class SearchInvalidationCapture:
    """Bounded per-event rule resolutions retained between update phases."""

    rules: tuple[SearchRuleResolution, ...] = ()
    consumed_target_budget: int = 0


@dataclass(frozen=True)
class SearchInvalidationPlan:
    """Final deduplicated targeted work and exact dirty fallbacks."""

    targets: tuple[SearchInvalidationTarget, ...] = ()
    dirty_fallbacks: tuple[SearchInvalidationPair, ...] = ()


@dataclass(frozen=True)
class SearchScheduledWork:
    """Separate immutable upsert and delete lanes scheduled for one event."""

    upserts: SearchInvalidationPlan = field(default_factory=SearchInvalidationPlan)
    deletes: tuple[SearchDeleteTarget, ...] = ()


def _owner_path(owner_class: type[GeneralManager]) -> str:
    """Return the stable import-like path used by search control-plane state."""
    return f"{owner_class.__module__}.{owner_class.__name__}"


def _selected_index_names(
    configured_index_names: tuple[str, ...],
    selected: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Resolve explicit or all configured index names in declared order."""
    if selected is not None:
        return tuple(dict.fromkeys(selected))
    return configured_index_names


def _safe_configured_index_names(
    configured_indexes: tuple[IndexConfig, ...],
    *,
    owner_class: type[GeneralManager],
    phase: str,
) -> tuple[str, ...]:
    """Read each configured name independently so one accessor cannot escape."""
    names: list[str] = []
    for ordinal, index in enumerate(configured_indexes):
        try:
            name = index.name
        except Exception as exc:  # noqa: BLE001 - config objects are extensible
            logger.warning(
                "related search invalidation index declaration failed",
                context={
                    "owner": _owner_path(owner_class),
                    "index": ordinal,
                    "phase": phase,
                },
                exc_info=exc,
            )
            continue
        if name not in names:
            names.append(name)
    return tuple(names)


def _source_class(source: type[GeneralManager] | str) -> type[GeneralManager]:
    """Lazily resolve and validate one rule source."""
    resolved = import_string(source) if isinstance(source, str) else source
    if not isinstance(resolved, type) or not issubclass(resolved, GeneralManager):
        raise _InvalidSearchInvalidationSourceError
    return resolved


def _rule_fallback(
    key: SearchRuleKey,
    owner_class: type[GeneralManager],
    index_names: tuple[str, ...],
) -> SearchRuleResolution:
    """Build fallback state without retaining user resolver objects."""
    return SearchRuleResolution(
        key=key,
        owner_class=owner_class,
        index_names=index_names,
        fallback=True,
    )


def _log_rule_failure(
    exc: Exception,
    *,
    owner_class: type[GeneralManager],
    ordinal: int,
    phase: str,
) -> None:
    """Log sanitized rule metadata without serializing user values."""
    logger.warning(
        "related search invalidation resolution failed",
        context={
            "owner": _owner_path(owner_class),
            "rule": ordinal,
            "phase": phase,
        },
        exc_info=exc,
    )


def _resolve_rule_targets(
    change: SearchChange,
    *,
    key: SearchRuleKey,
    owner_class: type[GeneralManager],
    index_names: tuple[str, ...],
    resolver: (
        Callable[[SearchChange, type[GeneralManager]], Iterable[GeneralManager]] | None
    ),
    remaining: int,
) -> tuple[SearchRuleResolution, int]:
    """Consume at most remaining-plus-one raw targets for one resolver."""
    if resolver is None:
        return _rule_fallback(key, owner_class, index_names), 0
    raw_targets = resolver(change, owner_class)
    iterator = iter(raw_targets)
    consumed = tuple(islice(iterator, remaining + 1))
    if len(consumed) > remaining:
        raise _SearchInvalidationTargetLimitError

    owner_path = _owner_path(owner_class)
    targets: list[SearchInvalidationTarget] = []
    for owner in consumed:
        if not isinstance(owner, owner_class):
            raise _InvalidSearchInvalidationTargetError
        copied_identification = deepcopy(dict(owner.identification))
        normalized = normalize_identification(copied_identification)
        identification = MappingProxyType(copied_identification)
        for index_name in index_names:
            canonical_key = (
                owner_path,
                normalized,
                index_name,
                change.database_alias,
            )
            targets.append(
                SearchInvalidationTarget(
                    owner_class=owner_class,
                    owner_path=owner_path,
                    identification=identification,
                    index_name=index_name,
                    database_alias=change.database_alias,
                    canonical_key=canonical_key,
                )
            )
    return (
        SearchRuleResolution(
            key=key,
            owner_class=owner_class,
            index_names=index_names,
            targets=tuple(targets),
        ),
        len(consumed),
    )


def resolve_search_invalidation_phase(
    change: SearchChange,
    *,
    previous: SearchInvalidationCapture | None = None,
) -> SearchInvalidationCapture:
    """Resolve one related invalidation lifecycle phase with a global bound."""
    if change.action == "create" and change.phase != "after":
        return SearchInvalidationCapture()
    if change.action == "delete" and change.phase != "before":
        return previous or SearchInvalidationCapture()
    previous_by_key = {
        resolution.key: resolution
        for resolution in (previous.rules if previous else ())
    }
    consumed_budget = previous.consumed_target_budget if previous else 0
    resolved_rules: list[SearchRuleResolution] = []

    setting_error: Exception | None = None
    try:
        max_targets = _positive_int_setting("SEARCH_INVALIDATION_MAX_TARGETS", 1000)
        _positive_int_setting("SEARCH_INVALIDATION_BATCH_SIZE", 100)
    except Exception as exc:  # noqa: BLE001 - settings backends are extensible
        max_targets = 0
        setting_error = exc

    for owner_class in tuple(GeneralManagerMeta.all_classes):
        if not isinstance(owner_class, type) or not issubclass(
            owner_class, GeneralManager
        ):
            continue
        owner_path = _owner_path(owner_class)
        try:
            config = get_search_config(owner_class)
            if config is None:
                continue
            configured_indexes = tuple(config.indexes)
            rules = tuple(config.invalidation_rules)
        except Exception as exc:  # noqa: BLE001 - user configuration is extensible
            logger.warning(
                "related search invalidation configuration failed",
                context={"owner": owner_path, "phase": change.phase},
                exc_info=exc,
            )
            for prior_resolution in previous.rules if previous is not None else ():
                if prior_resolution.key[0] != owner_path:
                    continue
                resolved_rules.append(
                    _rule_fallback(
                        prior_resolution.key,
                        prior_resolution.owner_class,
                        prior_resolution.index_names,
                    )
                )
            continue

        configured_index_names = _safe_configured_index_names(
            configured_indexes,
            owner_class=owner_class,
            phase=change.phase,
        )

        for ordinal, rule in enumerate(rules):
            key = (owner_path, ordinal)
            prior = previous_by_key.get(key)
            index_names = (
                prior.index_names if prior is not None else configured_index_names
            )
            try:
                index_names = _selected_index_names(
                    configured_index_names,
                    rule.indexes,
                )
                source_class = _source_class(rule.source)
                if not issubclass(type(change.instance), source_class):
                    continue
            except Exception as exc:  # noqa: BLE001 - declarations are user-owned
                if prior is not None:
                    index_names = prior.index_names
                _log_rule_failure(
                    exc,
                    owner_class=owner_class,
                    ordinal=ordinal,
                    phase=change.phase,
                )
                resolved_rules.append(_rule_fallback(key, owner_class, index_names))
                continue

            if prior is not None and prior.fallback:
                resolved_rules.append(_rule_fallback(key, owner_class, index_names))
                continue
            if setting_error is not None:
                _log_rule_failure(
                    setting_error,
                    owner_class=owner_class,
                    ordinal=ordinal,
                    phase=change.phase,
                )
                resolved_rules.append(_rule_fallback(key, owner_class, index_names))
                continue
            try:
                remaining = max(max_targets - consumed_budget, 0)
                current, newly_consumed = _resolve_rule_targets(
                    change,
                    key=key,
                    owner_class=owner_class,
                    index_names=index_names,
                    resolver=rule.resolve,
                    remaining=remaining,
                )
            except Exception as exc:  # noqa: BLE001 - resolvers are application code
                _log_rule_failure(
                    exc,
                    owner_class=owner_class,
                    ordinal=ordinal,
                    phase=change.phase,
                )
                resolved_rules.append(_rule_fallback(key, owner_class, index_names))
                continue
            consumed_budget += newly_consumed
            if prior is not None:
                current = SearchRuleResolution(
                    key=key,
                    owner_class=owner_class,
                    index_names=index_names,
                    targets=(*prior.targets, *current.targets),
                )
            resolved_rules.append(current)

    return SearchInvalidationCapture(
        rules=tuple(resolved_rules),
        consumed_target_budget=consumed_budget,
    )


def finalize_search_invalidation_capture(
    capture: SearchInvalidationCapture,
) -> SearchInvalidationPlan:
    """Deduplicate successful targets and exact fallback pairs in order."""
    targets: list[SearchInvalidationTarget] = []
    target_keys: set[SearchInvalidationKey] = set()
    fallback_pairs: list[SearchInvalidationPair] = []
    fallback_keys: set[tuple[type[GeneralManager], str]] = set()
    for resolution in capture.rules:
        if resolution.fallback:
            for index_name in resolution.index_names:
                key = (resolution.owner_class, index_name)
                if key not in fallback_keys:
                    fallback_keys.add(key)
                    fallback_pairs.append(
                        SearchInvalidationPair(resolution.owner_class, index_name)
                    )
            continue
        for target in resolution.targets:
            if target.canonical_key not in target_keys:
                target_keys.add(target.canonical_key)
                targets.append(target)
    return SearchInvalidationPlan(
        targets=tuple(targets),
        dirty_fallbacks=tuple(fallback_pairs),
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
    pairs: tuple[SearchInvalidationPair, ...],
    *,
    action: _DirectAction,
) -> tuple[tuple[SearchInvalidationPair, DirtySearchIndex], ...]:
    """Best-effort mark each affected pair and retain its generation fence."""
    tokens: list[tuple[SearchInvalidationPair, DirtySearchIndex]] = []
    for pair in pairs:
        try:
            token = mark_search_index_dirty(pair.owner_class, pair.index_name)
        except Exception as exc:  # noqa: BLE001 - user marker hooks are open-ended
            logger.warning(
                "search dirty marker failed",
                context={
                    "manager": pair.owner_class.__name__,
                    "index": pair.index_name,
                    "action": action,
                },
                exc_info=exc,
            )
            continue
        if token is not None:
            tokens.append((pair, token))
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


def _dispatch_scheduled_work(
    work: SearchScheduledWork,
    token_pairs: tuple[tuple[SearchInvalidationPair, DirtySearchIndex], ...],
    *,
    action: _DirectAction,
) -> None:
    """Dispatch both immutable lanes and acknowledge only complete exact pairs."""
    fallback_keys = {
        (pair.owner_class, pair.index_name) for pair in work.upserts.dirty_fallbacks
    }
    pair_success: dict[tuple[type[GeneralManager], str], bool] = {}
    for target in work.upserts.targets:
        key = (target.owner_class, target.index_name)
        pair_success.setdefault(key, True)
        try:
            instance = target.owner_class(**dict(target.identification))
            dispatch_index_update(
                action="index",
                manager_path=target.owner_path,
                identification=dict(target.identification),
                instance=instance,
                index_name=target.index_name,
            )
        except Exception as exc:  # noqa: BLE001 - manager/backend hooks are open-ended
            pair_success[key] = False
            logger.warning(
                "search indexing failed",
                context={
                    "manager": target.owner_class.__name__,
                    "index": target.index_name,
                    "action": action,
                },
                exc_info=exc,
            )

    deletes_by_manager: dict[
        tuple[type[GeneralManager], str], list[SearchDeleteTarget]
    ] = {}
    for delete_target in work.deletes:
        deletes_by_manager.setdefault(
            (delete_target.manager_class, delete_target.manager_path), []
        ).append(delete_target)
    token_map = {
        (pair.owner_class, pair.index_name): token for pair, token in token_pairs
    }
    for (manager_class, manager_path), delete_targets in deletes_by_manager.items():
        delete_keys = {(manager_class, target.index_name) for target in delete_targets}
        for key in delete_keys:
            pair_success.setdefault(key, True)
        serialized = tuple(
            {
                "index_name": target.index_name,
                "document_id": target.document_id,
            }
            for target in delete_targets
        )
        expected_generations = {
            index_name: token_map[(manager_class, index_name)].generation
            for _owner, index_name in delete_keys
            if (manager_class, index_name) in token_map
        }
        try:
            dispatch_delete_documents(
                manager_path,
                serialized,
                expected_generations=expected_generations or None,
            )
        except Exception as exc:  # noqa: BLE001 - document-ID hooks are open-ended
            for key in delete_keys:
                pair_success[key] = False
            logger.warning(
                "search deletion failed",
                context={"manager": manager_class.__name__, "action": "delete"},
                exc_info=exc,
            )

    completed = tuple(
        token
        for pair, token in token_pairs
        if (pair.owner_class, pair.index_name) not in fallback_keys
        and pair_success.get((pair.owner_class, pair.index_name), False)
    )
    _acknowledge_tokens(completed)


def _handle_search_pre_change(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
    action: str | None = None,
    change_context: dict[str, object] | None = None,
    **_: object,
) -> None:
    """Capture direct deletes and related before-phase targets synchronously."""
    if action not in {"update", "delete"} or instance is None or change_context is None:
        return
    if action == "delete":
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
    database_alias = cast(str, _.get("database_alias", DEFAULT_DB_ALIAS))
    try:
        capture = resolve_search_invalidation_phase(
            SearchChange(
                action=cast(Literal["update", "delete"], action),
                phase="before",
                instance=instance,
                database_alias=database_alias,
            )
        )
    except Exception as exc:  # noqa: BLE001 - registry/config hooks are open-ended
        logger.warning(
            "related search invalidation capture failed",
            context={"manager": instance.__class__.__name__, "phase": "before"},
            exc_info=exc,
        )
    else:
        change_context[_RELATED_SEARCH_CHANGE_CONTEXT] = capture


def _dedupe_pairs(
    pairs: list[SearchInvalidationPair],
) -> tuple[SearchInvalidationPair, ...]:
    """Deduplicate exact pairs in first-seen order."""
    seen: set[tuple[type[GeneralManager], str]] = set()
    result: list[SearchInvalidationPair] = []
    for pair in pairs:
        key = (pair.owner_class, pair.index_name)
        if key not in seen:
            seen.add(key)
            result.append(pair)
    return tuple(result)


def _combine_plans(
    direct: SearchInvalidationPlan,
    related: SearchInvalidationPlan,
) -> SearchInvalidationPlan:
    """Combine direct and related plans with canonical first-seen dedupe."""
    targets: list[SearchInvalidationTarget] = []
    seen: set[SearchInvalidationKey] = set()
    for target in (*direct.targets, *related.targets):
        if target.canonical_key not in seen:
            seen.add(target.canonical_key)
            targets.append(target)
    return SearchInvalidationPlan(
        targets=tuple(targets),
        dirty_fallbacks=_dedupe_pairs(
            [*direct.dirty_fallbacks, *related.dirty_fallbacks]
        ),
    )


def _direct_work(
    manager_class: type[GeneralManager],
    instance: GeneralManager | None,
    action: _DirectAction,
    change_context: dict[str, object],
    database_alias: str,
) -> tuple[SearchInvalidationPlan, tuple[SearchDeleteTarget, ...]]:
    """Build direct exact-index work while retaining immutable metadata only."""
    try:
        index_names = _index_names(manager_class)
    except Exception as exc:  # noqa: BLE001 - user config hooks are open-ended
        logger.warning(
            "search configuration resolution failed",
            context={"manager": manager_class.__name__, "action": action},
            exc_info=exc,
        )
        return SearchInvalidationPlan(), ()
    if not index_names:
        return SearchInvalidationPlan(), ()
    pairs = tuple(SearchInvalidationPair(manager_class, name) for name in index_names)
    if action == "delete":
        pending = change_context.get(_DIRECT_SEARCH_CHANGE_CONTEXT)
        deletes = (
            pending.delete_targets
            if isinstance(pending, _PendingDirectSearchChange)
            and pending.action == "delete"
            else ()
        )
        fallbacks = pairs if not deletes else ()
        return SearchInvalidationPlan(dirty_fallbacks=fallbacks), deletes
    if instance is None:
        return SearchInvalidationPlan(dirty_fallbacks=pairs), ()
    try:
        copied_identification = deepcopy(dict(instance.identification))
        normalized = normalize_identification(copied_identification)
        identification = MappingProxyType(copied_identification)
    except Exception as exc:  # noqa: BLE001 - manager metadata is extensible
        logger.warning(
            "search change metadata capture failed",
            context={"manager": manager_class.__name__, "action": action},
            exc_info=exc,
        )
        return SearchInvalidationPlan(dirty_fallbacks=pairs), ()
    manager_path = _owner_path(manager_class)
    targets = tuple(
        SearchInvalidationTarget(
            owner_class=manager_class,
            owner_path=manager_path,
            identification=identification,
            index_name=index_name,
            database_alias=database_alias,
            canonical_key=(
                manager_path,
                normalized,
                index_name,
                database_alias,
            ),
        )
        for index_name in index_names
    )
    return SearchInvalidationPlan(targets=targets), ()


def _handle_search_post_change(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
    action: str | None = None,
    change_context: dict[str, object] | None = None,
    database_alias: str = DEFAULT_DB_ALIAS,
    **_: object,
) -> None:
    """Finalize direct and related work and register one commit-bound callback."""
    if action not in {"create", "update", "delete"} or change_context is None:
        return
    direct_action = cast(_DirectAction, action)
    manager_class = _manager_class(sender, instance)
    if manager_class is None:
        return
    direct_plan, delete_targets = _direct_work(
        manager_class,
        instance,
        direct_action,
        change_context,
        database_alias,
    )
    prior = change_context.get(_RELATED_SEARCH_CHANGE_CONTEXT)
    prior_capture = prior if isinstance(prior, SearchInvalidationCapture) else None
    try:
        if direct_action == "delete":
            related_capture = prior_capture or SearchInvalidationCapture()
        elif instance is None:
            related_capture = SearchInvalidationCapture()
        else:
            related_capture = resolve_search_invalidation_phase(
                SearchChange(
                    action=direct_action,
                    phase="after",
                    instance=instance,
                    database_alias=database_alias,
                ),
                previous=prior_capture if direct_action == "update" else None,
            )
        related_plan = finalize_search_invalidation_capture(related_capture)
    except Exception as exc:  # noqa: BLE001 - registry/config hooks are open-ended
        logger.warning(
            "related search invalidation finalization failed",
            context={"manager": manager_class.__name__, "phase": "after"},
            exc_info=exc,
        )
        related_plan = SearchInvalidationPlan()
    work = SearchScheduledWork(
        upserts=_combine_plans(direct_plan, related_plan),
        deletes=delete_targets,
    )
    affected_pairs = _dedupe_pairs(
        [
            *(
                SearchInvalidationPair(target.owner_class, target.index_name)
                for target in work.upserts.targets
            ),
            *work.upserts.dirty_fallbacks,
            *(
                SearchInvalidationPair(target.manager_class, target.index_name)
                for target in work.deletes
            ),
        ]
    )
    if not affected_pairs:
        return

    if database_alias == DEFAULT_DB_ALIAS:
        tokens = _mark_pairs(affected_pairs, action=direct_action)
        transaction.on_commit(
            lambda: _run_post_commit_safely(
                lambda: _dispatch_scheduled_work(work, tokens, action=direct_action),
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
        tokens = _mark_pairs(affected_pairs, action=direct_action)
        _dispatch_scheduled_work(work, tokens, action=direct_action)

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
