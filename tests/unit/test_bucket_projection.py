from __future__ import annotations

from collections.abc import Generator

import pytest

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.projection import (
    DuplicateProjectionFieldError,
    EmptyProjectionFieldsError,
    FlatProjectionFieldCountError,
    UnknownProjectionFieldError,
)


class ProjectionRow:
    def __init__(self, *, code: str, amount: int) -> None:
        self.code = code
        self.amount = amount


class ProjectionManager:
    class Interface:
        @staticmethod
        def get_attributes() -> dict[str, object]:
            return {"code": None, "amount": None}

        @staticmethod
        def get_graph_ql_properties() -> dict[str, object]:
            return {}


class ProjectionIterationStartedError(RuntimeError):
    """Raised if validation consumes a bucket before it completes."""


class ProjectionBucket(Bucket[ProjectionManager]):
    def __init__(
        self,
        rows: list[ProjectionRow],
        *,
        raise_on_iteration: bool = False,
    ) -> None:
        super().__init__(ProjectionManager)
        self._rows = rows
        self._raise_on_iteration = raise_on_iteration

    def __or__(self, other: object) -> ProjectionBucket:
        raise NotImplementedError

    def __iter__(self) -> Generator[ProjectionRow, None, None]:
        if self._raise_on_iteration:
            raise ProjectionIterationStartedError
        yield from self._rows

    def filter(self, **kwargs: object) -> ProjectionBucket:
        raise NotImplementedError

    def exclude(self, **kwargs: object) -> ProjectionBucket:
        raise NotImplementedError

    def first(self) -> ProjectionRow | None:
        raise NotImplementedError

    def last(self) -> ProjectionRow | None:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def all(self) -> ProjectionBucket:
        raise NotImplementedError

    def get(self, **kwargs: object) -> ProjectionRow:
        raise NotImplementedError

    def __getitem__(self, item: int | slice) -> ProjectionRow | ProjectionBucket:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    def __contains__(self, item: ProjectionRow) -> bool:
        raise NotImplementedError

    def sort(
        self, key: tuple[str, ...] | str, reverse: bool = False
    ) -> ProjectionBucket:
        raise NotImplementedError


def test_values_returns_tuple_of_fresh_row_dicts() -> None:
    bucket = ProjectionBucket([ProjectionRow(code="A", amount=1)])

    first = bucket.values("code", "amount")
    first[0]["amount"] = 99
    second = bucket.values("code", "amount")

    assert first == ({"code": "A", "amount": 99},)
    assert second == ({"code": "A", "amount": 1},)
    assert first[0] is not second[0]


def test_values_list_returns_tuple_rows_and_flat_tuple() -> None:
    bucket = ProjectionBucket(
        [ProjectionRow(code="A", amount=1), ProjectionRow(code="B", amount=2)]
    )

    assert bucket.values_list("code", "amount") == (("A", 1), ("B", 2))
    assert bucket.values_list("code", flat=True) == ("A", "B")


@pytest.mark.parametrize(
    ("call", "expected_error"),
    [
        (lambda bucket: bucket.values(), EmptyProjectionFieldsError),
        (lambda bucket: bucket.values(1), TypeError),
        (lambda bucket: bucket.values("code", "code"), DuplicateProjectionFieldError),
        (lambda bucket: bucket.values("unknown"), UnknownProjectionFieldError),
        (lambda bucket: bucket.values_list("code", flat=1), TypeError),
        (
            lambda bucket: bucket.values_list("code", "amount", flat=True),
            FlatProjectionFieldCountError,
        ),
    ],
)
def test_projection_validation_precedes_bucket_iteration(call, expected_error) -> None:
    bucket = ProjectionBucket([], raise_on_iteration=True)

    with pytest.raises(expected_error):
        call(bucket)


def test_non_boolean_flat_is_rejected_before_flat_field_count() -> None:
    bucket = ProjectionBucket([], raise_on_iteration=True)

    with pytest.raises(TypeError):
        bucket.values_list("code", "amount", flat=1)
