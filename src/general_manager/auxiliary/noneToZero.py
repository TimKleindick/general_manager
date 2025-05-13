from typing import Optional, TypeVar, Literal
from general_manager.measurement import Measurement

NUMBERVALUE = TypeVar("NUMBERVALUE", int, float, Measurement)


def noneToZero(
    value: Optional[NUMBERVALUE],
) -> NUMBERVALUE | Literal[0]:
    """
    Returns zero if the input is None; otherwise, returns the original value.
    
    Args:
        value: An integer, float, or Measurement, or None.
    
    Returns:
        The original value if not None, otherwise 0.
    """
    if value is None:
        return 0
    return value
