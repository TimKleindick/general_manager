"""Exact-through Django M2M bridge for bounded search invalidation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import partial
import inspect
from itertools import islice
from types import MappingProxyType
from typing import Protocol, cast

from django.db import models
from django.db.models.signals import m2m_changed
from django.utils.module_loading import import_string

from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import SearchInvalidationRule
from general_manager.search.invalidation import (
    SearchInvalidationPair,
    SearchInvalidationPlan,
    SearchInvalidationTarget,
    SearchScheduledWork,
    get_search_invalidation_max_targets,
    schedule_search_invalidation_work,
)
from general_manager.search.registry import get_search_config
from general_manager.search.utils import normalize_identification

logger = get_logger("search.m2m_invalidation")


@dataclass(frozen=True)
class M2MInvalidationBinding:
    """Validated owner/source metadata for one exact Django through model."""

    owner_manager: type[GeneralManager]
    source_manager: type[GeneralManager]
    index_names: tuple[str, ...]
    owner_model: type[models.Model]
    source_model: type[models.Model]
    relation_name: str
    through_model: type[models.Model]
    owner_through_field: str
    source_through_field: str


class _InvalidM2MInvalidationBinding(ValueError):
    """Internal marker for declarations left to the system-check surface."""


class _M2MRemoteMetadata(Protocol):
    """Django-stubs-compatible subset of runtime M2M remote metadata."""

    model: type[models.Model]
    symmetrical: bool
    through: type[models.Model]


class _M2MFieldMetadata(Protocol):
    """Runtime methods Django installs on concrete M2M field instances."""

    remote_field: _M2MRemoteMetadata

    def m2m_field_name(self) -> str: ...

    def m2m_reverse_field_name(self) -> str: ...


def _manager_path(manager: type[GeneralManager]) -> str:
    return f"{manager.__module__}.{manager.__name__}"


def _orm_model(manager: type[GeneralManager]) -> type[models.Model]:
    interface = inspect.getattr_static(manager, "Interface", None)
    if not isinstance(interface, type) or not issubclass(interface, OrmInterfaceBase):
        raise _InvalidM2MInvalidationBinding
    model = getattr(interface, "_model", None)
    if not isinstance(model, type) or not issubclass(model, models.Model):
        raise _InvalidM2MInvalidationBinding
    return model


def _standard_owner_contract(owner: type[GeneralManager]) -> bool:
    interface = inspect.getattr_static(owner, "Interface", None)
    input_fields = getattr(interface, "input_fields", None)
    return isinstance(input_fields, Mapping) and tuple(input_fields) == ("id",)


def _source_manager(value: type[GeneralManager] | str) -> type[GeneralManager]:
    source = import_string(value) if isinstance(value, str) else value
    if not isinstance(source, type) or not issubclass(source, GeneralManager):
        raise _InvalidM2MInvalidationBinding
    return source


def _through_field_targets_primary_key(
    through_model: type[models.Model],
    field_name: str,
    endpoint_model: type[models.Model],
) -> bool:
    """Return whether one exact through FK stores the endpoint primary key."""
    try:
        through_field = through_model._meta.get_field(field_name)
    except Exception:  # noqa: BLE001 - malformed metadata is an invalid binding
        return False
    return (
        isinstance(through_field, models.ForeignKey)
        and through_field.remote_field.model is endpoint_model
        and through_field.target_field is endpoint_model._meta.pk
    )


def _compile_binding(
    owner: type[GeneralManager],
    rule: SearchInvalidationRule,
    configured_indexes: tuple[str, ...],
) -> M2MInvalidationBinding:
    """Compile one check-valid declaration into exact Django metadata."""
    source = _source_manager(rule.source)
    owner_model = _orm_model(owner)
    source_model = _orm_model(source)
    if not _standard_owner_contract(owner) or not isinstance(rule.relation, str):
        raise _InvalidM2MInvalidationBinding
    try:
        field = owner_model._meta.get_field(rule.relation)
    except Exception as exc:
        raise _InvalidM2MInvalidationBinding from exc
    if (
        not isinstance(field, models.ManyToManyField)
        or field.remote_field.model is not source_model
        or (
            owner_model is source_model
            and getattr(field.remote_field, "symmetrical", False)
        )
    ):
        raise _InvalidM2MInvalidationBinding
    metadata = cast(_M2MFieldMetadata, field)
    index_names = (
        tuple(dict.fromkeys(rule.indexes))
        if rule.indexes is not None
        else configured_indexes
    )
    if not index_names or not set(index_names).issubset(configured_indexes):
        raise _InvalidM2MInvalidationBinding
    through_model = metadata.remote_field.through
    owner_field = metadata.m2m_field_name()
    source_field = metadata.m2m_reverse_field_name()
    if (
        not isinstance(through_model, type)
        or not issubclass(through_model, models.Model)
        or not isinstance(owner_field, str)
        or not isinstance(source_field, str)
    ):
        raise _InvalidM2MInvalidationBinding
    if not _through_field_targets_primary_key(
        through_model,
        owner_field,
        owner_model,
    ) or not _through_field_targets_primary_key(
        through_model,
        source_field,
        source_model,
    ):
        raise _InvalidM2MInvalidationBinding
    return M2MInvalidationBinding(
        owner_manager=owner,
        source_manager=source,
        index_names=index_names,
        owner_model=owner_model,
        source_model=source_model,
        relation_name=rule.relation,
        through_model=through_model,
        owner_through_field=owner_field,
        source_through_field=source_field,
    )


def compile_m2m_invalidation_bindings(
    managers: Iterable[type[GeneralManager]] | None = None,
) -> tuple[M2MInvalidationBinding, ...]:
    """Compile valid relation rules while containing malformed declarations."""
    if managers is None:
        managers = tuple(GeneralManagerMeta.all_classes)
    bindings: list[M2MInvalidationBinding] = []
    for owner in managers:
        try:
            config = get_search_config(owner)
            if config is None:
                continue
            configured_indexes = tuple(
                dict.fromkeys(index.name for index in config.indexes)
            )
            rules = tuple(config.invalidation_rules)
        except Exception as exc:  # noqa: BLE001 - checks own user-facing detail
            logger.warning(
                "M2M search invalidation configuration skipped",
                context={"owner": _manager_path(owner)},
                exc_info=exc,
            )
            continue
        for ordinal, rule in enumerate(rules):
            if not isinstance(rule, SearchInvalidationRule) or rule.relation is None:
                continue
            try:
                bindings.append(_compile_binding(owner, rule, configured_indexes))
            except Exception as exc:  # noqa: BLE001 - startup must remain available
                logger.warning(
                    "M2M search invalidation binding skipped",
                    context={"owner": _manager_path(owner), "rule": ordinal},
                    exc_info=exc,
                )
    return tuple(bindings)


def _fallback_plan(binding: M2MInvalidationBinding) -> SearchInvalidationPlan:
    return SearchInvalidationPlan(
        dirty_fallbacks=tuple(
            SearchInvalidationPair(binding.owner_manager, index_name)
            for index_name in binding.index_names
        )
    )


def _target_plan(
    binding: M2MInvalidationBinding,
    owner_ids: Iterable[object],
    *,
    database_alias: str,
) -> SearchInvalidationPlan:
    owner_path = _manager_path(binding.owner_manager)
    targets: list[SearchInvalidationTarget] = []
    seen: set[str] = set()
    for owner_id in owner_ids:
        identification_dict = {"id": owner_id}
        normalized = normalize_identification(identification_dict)
        if normalized in seen:
            continue
        seen.add(normalized)
        identification = MappingProxyType(dict(identification_dict))
        for index_name in binding.index_names:
            targets.append(
                SearchInvalidationTarget(
                    owner_class=binding.owner_manager,
                    owner_path=owner_path,
                    identification=identification,
                    index_name=index_name,
                    database_alias=database_alias,
                    canonical_key=(
                        owner_path,
                        normalized,
                        index_name,
                        database_alias,
                    ),
                )
            )
    return SearchInvalidationPlan(targets=tuple(targets))


def _schedule_owner_ids(
    binding: M2MInvalidationBinding,
    owner_ids: Iterable[object],
    *,
    using: str,
) -> None:
    """Bound one event, then schedule exact targets or exact-pair fallback."""
    try:
        limit = get_search_invalidation_max_targets()
        bounded = tuple(islice(iter(owner_ids), limit + 1))
        plan = (
            _fallback_plan(binding)
            if len(bounded) > limit
            else _target_plan(binding, bounded, database_alias=using)
        )
    except Exception as exc:  # noqa: BLE001 - settings/PK serialization are extensible
        logger.warning("M2M search invalidation targeting failed", exc_info=exc)
        plan = _fallback_plan(binding)
    if not plan.targets and not plan.dirty_fallbacks:
        return
    schedule_search_invalidation_work(
        SearchScheduledWork(upserts=plan),
        source_database_alias=using,
    )


def handle_m2m_invalidation(
    binding: M2MInvalidationBinding,
    *,
    sender: type[models.Model],
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[object] | None,
    using: str,
    **_: object,
) -> None:
    """Translate supported public M2M signal phases into bounded owner work."""
    if sender is not binding.through_model:
        return
    expected_instance_model = binding.source_model if reverse else binding.owner_model
    expected_related_model = binding.owner_model if reverse else binding.source_model
    if (
        not isinstance(instance, expected_instance_model)
        or model is not expected_related_model
    ):
        return

    if action in {"post_add", "post_remove"}:
        if not pk_set:
            return
        owner_ids: Iterable[object] = pk_set if reverse else (instance.pk,)
        _schedule_owner_ids(binding, owner_ids, using=using)
        return
    if action != "pre_clear":
        return
    if not reverse:
        _schedule_owner_ids(binding, (instance.pk,), using=using)
        return

    try:
        limit = get_search_invalidation_max_targets()
        owner_field = binding.through_model._meta.get_field(binding.owner_through_field)
        owner_column = owner_field.attname
        owner_ids = tuple(
            binding.through_model._default_manager.using(using)
            .filter(**{binding.source_through_field: instance.pk})
            .values_list(owner_column, flat=True)[: limit + 1]
        )
    except Exception as exc:  # noqa: BLE001 - DB routing failures must not abort clear
        logger.warning("M2M reverse-clear capture failed", exc_info=exc)
        schedule_search_invalidation_work(
            SearchScheduledWork(upserts=_fallback_plan(binding)),
            source_database_alias=using,
        )
        return
    _schedule_owner_ids(binding, owner_ids, using=using)


def _receive_m2m_invalidation(
    binding: M2MInvalidationBinding,
    sender: type[models.Model],
    **kwargs: object,
) -> None:
    """Contain receiver failures so search never aborts a relation mutation."""
    try:
        handle_m2m_invalidation(binding, sender=sender, **kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - Django/user metadata is extensible
        logger.warning("M2M search invalidation signal failed", exc_info=exc)


def _dispatch_uid(
    binding: M2MInvalidationBinding,
) -> tuple[str, M2MInvalidationBinding]:
    """Return a stable, collision-free hashable identity for one binding."""
    return ("general_manager.search.m2m", binding)


def configure_search_m2m_invalidation() -> tuple[M2MInvalidationBinding, ...]:
    """Idempotently connect every valid exact-through relation binding."""
    bindings = compile_m2m_invalidation_bindings()
    for binding in bindings:
        m2m_changed.connect(
            partial(_receive_m2m_invalidation, binding),
            sender=binding.through_model,
            weak=False,
            dispatch_uid=cast(str, _dispatch_uid(binding)),
        )
    return bindings
