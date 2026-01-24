from typing import ClassVar

import pytest

from general_manager.search.config import (
    FieldConfig,
    IndexConfig,
    SearchConfigSpec,
    iter_index_names,
    resolve_search_config,
)


def test_index_config_normalizes_fields_and_boosts() -> None:
    index = IndexConfig(
        name="global",
        fields=["name", FieldConfig(name="leader", boost=2.5)],
        filters=("status",),
    )

    fields = index.iter_fields()
    assert [field.name for field in fields] == ["name", "leader"]
    assert [field.boost for field in fields] == [None, 2.5]

    boosts = index.field_boosts()
    assert boosts == {"leader": 2.5}


def test_index_config_validates_boost_ranges() -> None:
    with pytest.raises(ValueError):
        IndexConfig(name="global", fields=["name"], boost=0)

    with pytest.raises(ValueError):
        IndexConfig(name="global", fields=["name"], min_score=-0.1)

    with pytest.raises(ValueError):
        FieldConfig(name="name", boost=0)


def test_resolve_search_config_supports_class_objects() -> None:
    class DummyConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"])
        ]
        type_label = "Project"

    resolved = resolve_search_config(DummyConfig)
    assert isinstance(resolved, SearchConfigSpec)
    assert resolved.type_label == "Project"
    assert iter_index_names(resolved) == ["global"]
