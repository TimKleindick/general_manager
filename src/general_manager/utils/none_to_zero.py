"""Convenience helpers for normalising optional numeric inputs."""

from typing import Literal, TypeVar

from general_manager.measurement import Measurement

NumberValue = TypeVar("NumberValue", int, float, Measurement)


def none_to_zero(
    value: NumberValue | None,
) -> NumberValue | Literal[0]:
    """
    Return ``0`` for ``None`` and otherwise return the original numeric value.

    The helper does not coerce, copy, or normalize non-``None`` values. Existing
    ``int``, ``float``, and ``Measurement`` inputs are returned unchanged, so
    falsey numeric values such as ``0`` or ``0.0`` remain their original values.

    Parameters:
        value: Numeric value or ``Measurement`` instance that may be ``None``.

    Returns:
        The original value when it is not ``None``; otherwise the integer
        literal ``0``.
    """
    if value is None:
        return 0
    return value
