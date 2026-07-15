from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import ClassVar

import pytest

from general_manager.manager.general_manager import GeneralManager
from general_manager.search.config import (
    FieldConfig,
    IndexConfig,
    InvalidFieldBoostError,
    InvalidIndexBoostError,
    InvalidIndexMinScoreError,
    SearchChange,
    SearchConfigSpec,
    SearchInvalidationRule,
    iter_index_names,
    resolve_search_config,
)
from tests.utils.simple_manager_interface import BaseTestInterface


class ConfigSourceManager(GeneralManager):
    Interface = BaseTestInterface


def _resolve_targets(change: SearchChange, owner: type[GeneralManager]):
    del change, owner
    return ()


def test_field_config_rejects_invalid_boost() -> None:
    with pytest.raises(InvalidFieldBoostError):
        FieldConfig(name="name", boost=0)


def test_index_config_rejects_invalid_boost() -> None:
    """
    Verifies that creating an IndexConfig with a boost of 0 raises InvalidIndexBoostError.

    Raises:
        InvalidIndexBoostError: if `IndexConfig` is constructed with `boost=0`.
    """
    with pytest.raises(InvalidIndexBoostError):
        IndexConfig(name="global", fields=["name"], boost=0)


def test_index_config_rejects_invalid_min_score() -> None:
    with pytest.raises(InvalidIndexMinScoreError):
        IndexConfig(name="global", fields=["name"], min_score=-0.1)


def test_resolve_search_config_passthrough() -> None:
    spec = SearchConfigSpec(indexes=(IndexConfig(name="global", fields=["name"]),))
    assert resolve_search_config(spec) is spec


def test_iter_index_names_none() -> None:
    assert list(iter_index_names(None)) == []


def test_search_change_is_frozen() -> None:
    instance = ConfigSourceManager()
    change = SearchChange(
        action="update",
        phase="before",
        instance=instance,
        database_alias="default",
    )

    with pytest.raises(FrozenInstanceError):
        change.phase = "after"  # type: ignore[misc]


def test_search_invalidation_rule_is_frozen_and_preserves_class_source() -> None:
    rule = SearchInvalidationRule(
        source=ConfigSourceManager,
        resolve=_resolve_targets,
        indexes=("global",),
        relation="tags",
    )

    assert rule.source is ConfigSourceManager
    with pytest.raises(FrozenInstanceError):
        rule.source = "example.Source"  # type: ignore[misc]


def test_search_invalidation_rule_preserves_string_source_without_importing() -> None:
    rule = SearchInvalidationRule(source="missing.module.Source")

    assert rule.source == "missing.module.Source"
    assert rule.resolve is None
    assert rule.indexes is None
    assert rule.relation is None


def test_search_config_spec_defaults_to_empty_invalidation_rules() -> None:
    spec = SearchConfigSpec(indexes=())

    assert spec.invalidation_rules == ()


def test_resolve_search_config_normalizes_invalidation_rules_to_tuple() -> None:
    rule = SearchInvalidationRule(source=ConfigSourceManager)

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"])
        ]
        invalidation_rules: ClassVar[list[SearchInvalidationRule]] = [rule]

    spec = resolve_search_config(SearchConfig)

    assert spec is not None
    assert spec.invalidation_rules == (rule,)
