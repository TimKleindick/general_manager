from typing import TYPE_CHECKING, assert_type

from general_manager.cache.cache_decorator import cached


def source(value: int) -> str:
    return str(value)


direct = cached(source, timeout=30, cache="timeout")
assert_type(direct(1), str)


@cached
def bare(value: int) -> str:
    return str(value)


assert_type(bare(1), str)


@cached(cache="timeout", timeout=30)
def configured(value: int) -> str:
    return str(value)


assert_type(configured(1), str)


if TYPE_CHECKING:
    direct("wrong")  # type: ignore[arg-type]
    bare("wrong")  # type: ignore[arg-type]
    configured("wrong")  # type: ignore[arg-type]
