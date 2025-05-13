from typing import Optional, TypeVar, Literal
from general_manager.measurement import Measurement

NUMBERVALUE = TypeVar("NUMBERVALUE", int, float, Measurement)


def noneToZero(
    value: Optional[NUMBERVALUE],
) -> NUMBERVALUE | Literal[0]:
    if value is None:
        return 0
    return value
