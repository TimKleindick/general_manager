from __future__ import annotations

import pytest

from general_manager.search.config import (
    FieldConfig,
    IndexConfig,
    InvalidFieldBoostError,
    InvalidIndexBoostError,
    InvalidIndexMinScoreError,
    SearchConfigSpec,
    iter_index_names,
    resolve_search_config,
)


def test_field_config_rejects_invalid_boost() -> None:
    with pytest.raises(InvalidFieldBoostError):
        FieldConfig(name="name", boost=0)


def test_index_config_rejects_invalid_boost() -> None:
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
