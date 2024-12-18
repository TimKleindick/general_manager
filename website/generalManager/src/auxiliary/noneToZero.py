from typing import Optional, TypeVar, Literal
from generalManager.src.measurement import Measurement

VALUE = TypeVar("VALUE", int, float, Measurement)


def noneToZero(
    value: Optional[VALUE],
) -> VALUE | Literal[0]:
    if value is None:
        return 0
    return value
