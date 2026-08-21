from __future__ import annotations

from dataclasses import FrozenInstanceError, field, fields
from typing import ClassVar

import pytest

from general_manager.api.graphql_type import (
    GraphQLType,
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)


@pytest.fixture(autouse=True)
def restore_graphql_type_registry() -> None:
    snapshot = get_registered_graphql_types()
    yield
    _restore_registered_graphql_types(snapshot)


def test_graphql_type_is_frozen_and_uses_dataclass_fields() -> None:
    class ProjectHour(GraphQLType):
        task_id: int
        users: list[str] = field(default_factory=list)
        label: ClassVar[str] = "hours"

    value = ProjectHour(task_id=7)
    assert value.users == []
    assert [item.name for item in fields(ProjectHour)] == ["task_id", "users"]
    assert get_registered_graphql_types() == (ProjectHour,)
    with pytest.raises(FrozenInstanceError):
        value.task_id = 8  # type: ignore[misc]


def test_graphql_type_construction_matches_dataclass_defaults() -> None:
    class ProjectHour(GraphQLType):
        task_id: int
        label: str = "hours"
        users: list[str] = field(default_factory=list)

    positional = ProjectHour(7)
    keyword = ProjectHour(task_id=8, label="days", users=["alice"])
    another = ProjectHour(9)

    assert positional == ProjectHour(task_id=7, label="hours", users=[])
    assert positional.task_id == 7
    assert positional.label == "hours"
    assert positional.users == []
    assert keyword == ProjectHour(8, "days", ["alice"])
    assert another.users == []
    assert positional.users is not another.users

    with pytest.raises(TypeError):
        ProjectHour()  # type: ignore[call-arg]
