"""Type-check the public native identity property declaration."""

from typing import assert_type
from uuid import UUID

from general_manager import graph_ql_property


@graph_ql_property(is_identifier=True, cache="none")
def scalar_key(_instance: object) -> int:
    return 2**100 + 1


@graph_ql_property(is_identifier=True, cache="timeout", timeout=30)
def uuid_keys(_instance: object) -> list[UUID]:
    return []


assert_type(scalar_key.is_identifier, bool)
assert_type(uuid_keys.is_identifier, bool)
