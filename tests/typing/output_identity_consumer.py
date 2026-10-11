"""Public output identity metadata keeps native constructor and value types."""

from dataclasses import field
from typing import assert_type
from uuid import UUID

from general_manager import GraphQLType


class NativeIdentityOutput(GraphQLType):
    record_key: int = field(metadata={"is_identifier": True})
    record_keys: list[UUID | None] | None = field(metadata={"is_identifier": True})
    code: str = field(metadata={"is_identifier": True})
    quantity: int


def use_identity_output(value: NativeIdentityOutput) -> None:
    assert_type(value.record_key, int)
    assert_type(value.record_keys, list[UUID | None] | None)
    assert_type(value.code, str)
    assert_type(value.quantity, int)
    NativeIdentityOutput(record_key=7, record_keys=None, code="007", quantity=17)
