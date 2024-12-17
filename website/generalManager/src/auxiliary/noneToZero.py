from typing import Optional, TypeVar, Literal
from generalManager.src.measurement import Measurement

T = TypeVar("T", int, float, Measurement)


def noneToZero(
    value: Optional[T],
) -> T | Literal[0]:
    if value is None:
        return 0
    return value
