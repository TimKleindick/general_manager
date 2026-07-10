"""Tests for the private trusted-enumeration validation scope."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import ClassVar, cast

import pytest
from django.test import override_settings

from general_manager.interface.base_interface import (
    InterfaceBase,
    InvalidInputConstraintError,
    InvalidInputTypeError,
    InvalidInputValueError,
    _trusted_enumeration_scope,
)
from general_manager.manager.input import Input


class EvidenceDouble:
    """Record trusted-membership authorization and dependency tracking calls."""

    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.authorization_calls: list[
            tuple[Input[type[object]], object, Mapping[str, object]]
        ] = []
        self.membership_dependency_calls = 0

    def authorizes(
        self,
        input_field: Input[type[object]],
        value: object,
        identification: Mapping[str, object],
    ) -> bool:
        self.authorization_calls.append((input_field, value, dict(identification)))
        return self.allowed

    def track_membership_dependency(self) -> None:
        self.membership_dependency_calls += 1


def _interface_with_counted_possible_values() -> tuple[
    type[InterfaceBase], Input[type[object]], list[int]
]:
    possible_values_calls: list[int] = []

    def possible_values() -> list[int]:
        possible_values_calls.append(1)
        return [1, 2]

    input_field = cast(
        Input[type[object]],
        Input(int, possible_values=possible_values),
    )

    class ScopedInterface(InterfaceBase):
        input_fields: ClassVar[dict[str, Input[type[object]]]] = {"code": input_field}

    return ScopedInterface, input_field, possible_values_calls


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_no_scope_uses_existing_membership_validation() -> None:
    interface_class, _, possible_values_calls = (
        _interface_with_counted_possible_values()
    )

    with pytest.raises(
        InvalidInputValueError,
        match=r"^Invalid value for code: 7, allowed: \[1, 2\]\.$",
    ):
        interface_class(code=7)

    assert possible_values_calls == [1]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_scope_for_another_interface_uses_existing_membership_validation() -> None:
    interface_class, _, possible_values_calls = (
        _interface_with_counted_possible_values()
    )

    class OtherInterface(InterfaceBase):
        input_fields: ClassVar[dict[str, Input[type[object]]]] = {}

    evidence = EvidenceDouble(allowed=True)
    with _trusted_enumeration_scope(OtherInterface, {"code": evidence}):
        with pytest.raises(InvalidInputValueError):
            interface_class(code=7)

    assert evidence.authorization_calls == []
    assert evidence.membership_dependency_calls == 0
    assert possible_values_calls == [1]


@pytest.mark.parametrize(
    "evidence_by_name", [{}, {"other": EvidenceDouble(allowed=True)}]
)
@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_missing_exact_field_evidence_uses_existing_membership_validation(
    evidence_by_name: dict[str, EvidenceDouble],
) -> None:
    interface_class, _, possible_values_calls = (
        _interface_with_counted_possible_values()
    )

    with _trusted_enumeration_scope(interface_class, evidence_by_name):
        with pytest.raises(InvalidInputValueError):
            interface_class(code=7)

    assert possible_values_calls == [1]
    assert all(
        not evidence.authorization_calls for evidence in evidence_by_name.values()
    )


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_denied_evidence_falls_through_to_membership_validation_once() -> None:
    interface_class, input_field, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    evidence = EvidenceDouble(allowed=False)

    with _trusted_enumeration_scope(interface_class, {"code": evidence}):
        with pytest.raises(InvalidInputValueError):
            interface_class(code=7)

    assert evidence.authorization_calls == [(input_field, 7, {})]
    assert evidence.membership_dependency_calls == 0
    assert possible_values_calls == [1]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_allowed_evidence_skips_membership_and_tracks_dependency_once() -> None:
    interface_class, input_field, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    evidence = EvidenceDouble(allowed=True)

    with _trusted_enumeration_scope(interface_class, {"code": evidence}):
        interface = interface_class(code=7)

    assert interface.identification == {"code": 7}
    assert evidence.authorization_calls == [(input_field, 7, {})]
    assert evidence.membership_dependency_calls == 1
    assert possible_values_calls == []


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_nested_scopes_restore_the_outer_scope() -> None:
    interface_class, input_field, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    outer_evidence = EvidenceDouble(allowed=True)
    inner_evidence = EvidenceDouble(allowed=False)

    with _trusted_enumeration_scope(interface_class, {"code": outer_evidence}):
        interface_class(code=7)
        with _trusted_enumeration_scope(interface_class, {"code": inner_evidence}):
            with pytest.raises(InvalidInputValueError):
                interface_class(code=7)
        interface_class(code=7)

    assert outer_evidence.authorization_calls == [
        (input_field, 7, {}),
        (input_field, 7, {}),
    ]
    assert outer_evidence.membership_dependency_calls == 2
    assert inner_evidence.authorization_calls == [(input_field, 7, {})]
    assert inner_evidence.membership_dependency_calls == 0
    assert possible_values_calls == [1]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_scope_resets_after_exception_and_does_not_leak() -> None:
    interface_class, _, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    evidence = EvidenceDouble(allowed=True)
    scope_body_error = RuntimeError("scope body failed")

    with pytest.raises(RuntimeError, match="scope body failed"):
        with _trusted_enumeration_scope(interface_class, {"code": evidence}):
            interface_class(code=7)
            raise scope_body_error

    with pytest.raises(InvalidInputValueError):
        interface_class(code=7)

    assert evidence.membership_dependency_calls == 1
    assert possible_values_calls == [1]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_scope_revokes_context_inherited_by_child_task_after_exit() -> None:
    interface_class, _, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    evidence = EvidenceDouble(allowed=True)

    async def exercise_child_task() -> None:
        release_child = asyncio.Event()

        async def validate_after_parent_scope_exits() -> None:
            await release_child.wait()
            with pytest.raises(InvalidInputValueError):
                interface_class(code=7)

        with _trusted_enumeration_scope(interface_class, {"code": evidence}):
            child = asyncio.create_task(validate_after_parent_scope_exits())
            await asyncio.sleep(0)

        release_child.set()
        await child

    asyncio.run(exercise_child_task())

    assert evidence.authorization_calls == []
    assert evidence.membership_dependency_calls == 0
    assert possible_values_calls == [1]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_allowed_evidence_sees_normalized_value() -> None:
    input_field = cast(
        Input[type[object]],
        Input(str, possible_values=["OTHER"], normalizer=lambda value: value.upper()),
    )

    class NormalizingInterface(InterfaceBase):
        input_fields: ClassVar[dict[str, Input[type[object]]]] = {"code": input_field}

    evidence = EvidenceDouble(allowed=True)
    with _trusted_enumeration_scope(NormalizingInterface, {"code": evidence}):
        interface = NormalizingInterface(code="value")

    assert interface.identification == {"code": "VALUE"}
    assert evidence.authorization_calls == [(input_field, "VALUE", {})]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_trusted_gate_runs_after_type_bounds_and_validator_checks() -> None:
    possible_values_calls: list[int] = []
    validator_calls: list[int] = []

    def possible_values() -> list[int]:
        possible_values_calls.append(1)
        return [1]

    def validator(value: int) -> bool:
        validator_calls.append(value)
        return value != 8

    input_field = cast(
        Input[type[object]],
        Input(
            int,
            possible_values=possible_values,
            min_value=1,
            max_value=10,
            validator=validator,
        ),
    )

    class ConstrainedInterface(InterfaceBase):
        input_fields: ClassVar[dict[str, Input[type[object]]]] = {"code": input_field}

    interface = object.__new__(ConstrainedInterface)
    evidence = EvidenceDouble(allowed=True)

    with _trusted_enumeration_scope(ConstrainedInterface, {"code": evidence}):
        with pytest.raises(InvalidInputTypeError):
            interface._process_input_field(
                "code", input_field, "7", {}, cache_context=None
            )
        with pytest.raises(
            InvalidInputConstraintError, match="outside the allowed range"
        ):
            interface._process_input_field(
                "code", input_field, 11, {}, cache_context=None
            )
        with pytest.raises(
            InvalidInputConstraintError,
            match="did not satisfy the configured validator",
        ):
            interface._process_input_field(
                "code", input_field, 8, {}, cache_context=None
            )
        interface._process_input_field("code", input_field, 7, {}, cache_context=None)

    assert validator_calls == [8, 7]
    assert evidence.authorization_calls == [(input_field, 7, {})]
    assert evidence.membership_dependency_calls == 1
    assert possible_values_calls == []


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_direct_processing_without_authorization_keeps_exact_membership_error() -> None:
    interface_class, input_field, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    interface = object.__new__(interface_class)
    evidence = EvidenceDouble(allowed=False)
    expected_error = r"^Invalid value for code: 7, allowed: \[1, 2\]\.$"

    with pytest.raises(InvalidInputValueError, match=expected_error):
        interface._process_input_field("code", input_field, 7, {}, cache_context=None)
    with _trusted_enumeration_scope(interface_class, {"code": evidence}):
        with pytest.raises(InvalidInputValueError, match=expected_error):
            interface._process_input_field(
                "code", input_field, 7, {}, cache_context=None
            )

    assert evidence.authorization_calls == [(input_field, 7, {})]
    assert evidence.membership_dependency_calls == 0
    assert possible_values_calls == [1, 1]


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=False)
def test_disabled_membership_validation_does_not_consult_evidence() -> None:
    interface_class, _, possible_values_calls = (
        _interface_with_counted_possible_values()
    )
    evidence = EvidenceDouble(allowed=True)

    with _trusted_enumeration_scope(interface_class, {"code": evidence}):
        interface = interface_class(code=7)

    assert interface.identification == {"code": 7}
    assert evidence.authorization_calls == []
    assert evidence.membership_dependency_calls == 0
    assert possible_values_calls == []
