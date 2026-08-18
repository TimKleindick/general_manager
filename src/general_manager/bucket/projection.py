"""Shared validation and result shaping for bucket projections."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


type ProjectionRows = tuple[tuple[object, ...], ...]


class EmptyProjectionFieldsError(ValueError):
    """Raised when a projection is requested without any fields."""

    def __init__(self) -> None:
        super().__init__("At least one projection field is required.")


class DuplicateProjectionFieldError(ValueError):
    """Raised when a projection contains the same field more than once."""

    def __init__(self) -> None:
        super().__init__("Projection fields must be unique.")


class UnknownProjectionFieldError(ValueError):
    """Raised when a projection field is outside the manager public namespace."""

    def __init__(self, fields: tuple[str, ...]) -> None:
        self.fields = fields
        super().__init__(f"Unknown projection field(s): {', '.join(fields)}.")


class FlatProjectionFieldCountError(ValueError):
    """Raised when flat projection requests anything other than one field."""

    def __init__(self) -> None:
        super().__init__("Flat projections require exactly one field.")


def validate_projection_fields(
    manager_class: type[GeneralManager],
    fields: tuple[object, ...],
) -> tuple[str, ...]:
    """Validate and normalize an ordered projection field tuple."""
    if not fields:
        raise EmptyProjectionFieldsError()
    if not all(isinstance(field, str) for field in fields):
        raise TypeError("Projection fields must be strings.")  # noqa: TRY003
    normalized = cast(tuple[str, ...], fields)
    if len(set(normalized)) != len(normalized):
        raise DuplicateProjectionFieldError()
    allowed = set(manager_class.Interface.get_attributes())
    allowed.update(manager_class.Interface.get_graph_ql_properties())
    unknown = tuple(field for field in normalized if field not in allowed)
    if unknown:
        raise UnknownProjectionFieldError(unknown)
    return normalized


def validate_projection_flat(flat: object, fields: tuple[str, ...]) -> None:
    """Validate the flat result option after fields have been validated."""
    if type(flat) is not bool:
        raise TypeError("Projection flat must be a boolean.")  # noqa: TRY003
    if flat and len(fields) != 1:
        raise FlatProjectionFieldCountError()


def project_values(
    source: Iterable[tuple[object, ...]],
    fields: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    """Convert canonical projection rows into fresh dictionaries."""
    return tuple(dict(zip(fields, row, strict=True)) for row in source)


def project_values_list(
    source: Iterable[tuple[object, ...]],
    fields: tuple[str, ...],
    *,
    flat: object,
) -> ProjectionRows | tuple[object, ...]:
    """Convert canonical projection rows into tuple or flat scalar results."""
    validate_projection_flat(flat, fields)
    rows: ProjectionRows = tuple(tuple(row) for row in source)
    if flat:
        return tuple(row[0] for row in rows)
    return rows
