"""Django startup checks for declarative search invalidation rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import inspect

from django.core.checks import CheckMessage, Error, register
from django.db import models
from django.utils.module_loading import import_string

from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import SearchInvalidationRule, resolve_search_config

_PREFIX = "general_manager.search"
_registered = False


def _error(message: str, suffix: str) -> Error:
    return Error(message, id=f"{_PREFIX}.{suffix}")


def _resolve_source(value: object) -> type[GeneralManager] | None:
    """Resolve a declared source without exposing import failures to checks."""
    try:
        source = import_string(value) if isinstance(value, str) else value
    except Exception:  # noqa: BLE001 - system checks must not break startup
        return None
    if not isinstance(source, type) or not issubclass(source, GeneralManager):
        return None
    return source


def _orm_model(manager: type[GeneralManager]) -> type[models.Model] | None:
    """Return a manager's statically configured ORM model, when available."""
    interface = inspect.getattr_static(manager, "Interface", None)
    if not isinstance(interface, type) or not issubclass(interface, OrmInterfaceBase):
        return None
    model = getattr(interface, "_model", None)
    if not isinstance(model, type) or not issubclass(model, models.Model):
        return None
    return model


def _check_relation(
    owner: type[GeneralManager],
    source: type[GeneralManager],
    relation: object,
) -> list[CheckMessage]:
    """Validate static ORM metadata required by an M2M rule."""
    owner_model = _orm_model(owner)
    if owner_model is None:
        return [
            _error(
                "M2M search invalidation requires an ORM-backed owner.",
                "E004",
            )
        ]
    source_model = _orm_model(source)
    if source_model is None:
        return [
            _error(
                "M2M search invalidation requires an ORM-backed source.",
                "E005",
            )
        ]

    if not isinstance(relation, str):
        return [
            _error(
                "Search invalidation relation must name an owner M2M field targeting the source.",
                "E006",
            )
        ]
    try:
        field = owner_model._meta.get_field(relation)
    except Exception:  # noqa: BLE001 - malformed model metadata is a check error
        return [
            _error(
                "Search invalidation relation must name an owner M2M field targeting the source.",
                "E006",
            )
        ]
    if (
        not isinstance(field, models.ManyToManyField)
        or field.remote_field.model is not source_model
    ):
        return [
            _error(
                "Search invalidation relation must name an owner M2M field targeting the source.",
                "E006",
            )
        ]

    interface = inspect.getattr_static(owner, "Interface", None)
    input_fields = getattr(interface, "input_fields", None)
    if not isinstance(input_fields, Mapping) or tuple(input_fields) != ("id",):
        return [
            _error(
                "M2M search invalidation owners must use exactly the 'id' input.",
                "E007",
            )
        ]
    if owner_model is source_model and getattr(
        field.remote_field,
        "symmetrical",
        False,
    ):
        return [
            _error(
                "Self-symmetrical M2M search invalidation is not supported.",
                "E008",
            )
        ]
    return []


def _check_rule(
    owner: type[GeneralManager],
    owner_indexes: set[str],
    rule: object,
) -> list[CheckMessage]:
    """Validate one rule while containing malformed declaration errors."""
    if not isinstance(rule, SearchInvalidationRule):
        return [_error("Search invalidation rule declaration is invalid.", "E000")]

    issues: list[CheckMessage] = []
    source = _resolve_source(rule.source)
    if source is None:
        issues.append(
            _error(
                "Search invalidation rule source must resolve to a GeneralManager subclass.",
                "E001",
            )
        )
    if rule.resolve is not None and not callable(rule.resolve):
        issues.append(
            _error(
                "Search invalidation rule resolver must be callable.",
                "E002",
            )
        )
    if rule.indexes is not None and (
        not isinstance(rule.indexes, tuple)
        or not rule.indexes
        or any(not isinstance(index, str) for index in rule.indexes)
        or not set(rule.indexes).issubset(owner_indexes)
    ):
        issues.append(
            _error(
                "Search invalidation rule indexes must be a non-empty subset of owner indexes.",
                "E003",
            )
        )
    if rule.relation is not None and source is not None:
        issues.extend(_check_relation(owner, source, rule.relation))
    return issues


def run_search_checks(
    *,
    managers: Iterable[type[GeneralManager]] | None = None,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Return sanitized configuration errors without backend or database I/O."""
    if managers is None:
        managers = tuple(GeneralManagerMeta.all_classes)

    issues: list[CheckMessage] = []
    for owner in managers:
        try:
            declaration = inspect.getattr_static(owner, "SearchConfig", None)
            config = resolve_search_config(declaration)
            if config is None:
                continue
            owner_indexes = {index.name for index in config.indexes}
            rules = config.invalidation_rules
        except Exception:  # noqa: BLE001 - malformed configs become check errors
            issues.append(
                _error("Search invalidation configuration is invalid.", "E000")
            )
            continue
        for rule in rules:
            try:
                issues.extend(_check_rule(owner, owner_indexes, rule))
            except Exception:  # noqa: BLE001 - isolate each malformed rule
                issues.append(
                    _error("Search invalidation rule declaration is invalid.", "E000")
                )
    return issues


def register_search_checks() -> None:
    """Register the search invalidation check exactly once per process."""
    global _registered
    if _registered:
        return
    register("general_manager")(run_search_checks)
    _registered = True
