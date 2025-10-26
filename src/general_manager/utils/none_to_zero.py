"""Convenience helpers for normalising optional numeric inputs."""

from typing import Optional, TypeVar, Literal
from general_manager.measurement import Measurement

NUMBERVALUE = TypeVar("NUMBERVALUE", int, float, Measurement)


def none_to_zero(
    value: Optional[NUMBERVALUE],
) -> NUMBERVALUE | Literal[0]:
    """
    Replace None with zero while preserving existing numeric values.

    Parameters:
        value (Optional[NUMBERVALUE]): Numeric value or Measurement instance that may be None.

    Returns:
        NUMBERVALUE | Literal[0]: The input value if it is not None; otherwise, zero.
    """
    if value is None:
        return 0
    return value
