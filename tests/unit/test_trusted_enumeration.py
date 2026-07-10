"""Tests for the private trusted-enumeration validation scope."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping
from copy import copy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import struct
from typing import Any, ClassVar, cast
from uuid import UUID

import pytest
from django.test import override_settings

from general_manager.bucket.calculation_bucket import (
    CalculationBucket,
    _EnumerationEvidence,
    _TrustedToken,
    _trusted_candidate_token,
    _trusted_enumeration_evidence,
)
from general_manager.interface import CalculationInterface
from general_manager.interface.base_interface import (
    InterfaceBase,
    InvalidInputConstraintError,
    InvalidInputTypeError,
    InvalidInputValueError,
    _trusted_enumeration_scope,
)
from general_manager.manager.input import DateRangeDomain, Input, NumericRangeDomain
from general_manager.manager.general_manager import GeneralManager


def _calculation_bucket_with_inputs(
    input_fields: dict[str, Input[type[object]]],
) -> CalculationBucket[GeneralManager]:
    class EnumerationInterface(CalculationInterface):
        pass

    EnumerationInterface.input_fields = input_fields

    class EnumerationManager:
        Interface = EnumerationInterface

        def __init__(self, **identification: object) -> None:
            interface = EnumerationInterface(**identification)
            self.identification = interface.identification

    manager_class = cast(type[GeneralManager], EnumerationManager)
    EnumerationInterface._parent_class = manager_class
    return CalculationBucket(manager_class)


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


@pytest.mark.parametrize(
    "candidate",
    [
        True,
        7,
        1.25,
        "value",
        b"value",
        date(2026, 7, 11),
        datetime(2026, 7, 11, 8, 30),
        UUID("12345678-1234-5678-1234-567812345678"),
    ],
)
def test_trusted_candidate_token_accepts_only_exact_safe_scalars(
    candidate: object,
) -> None:
    token = _trusted_candidate_token(candidate)

    assert isinstance(token, _TrustedToken)
    assert token == _trusted_candidate_token(candidate)


class _IntegerSubclass(int):
    pass


class _DateSubclass(date):
    pass


class _EnumerationValue(Enum):
    ITEM = 1


@dataclass(frozen=True)
class _FrozenValue:
    code: int


@dataclass
class _MutableValue:
    code: int


class _UnsafeValue:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    @property
    def code(self) -> int:
        self.callbacks.append("property")
        return 1

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        return self is other

    def __hash__(self) -> int:
        self.callbacks.append("hash")
        return 1


class _HostileStateKey:
    def __init__(self) -> None:
        self.hash_value = hash(("initial", id(self)))
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        self.callbacks.append("hash")
        return self.hash_value

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError(type(other).__name__)


@pytest.mark.parametrize(
    "candidate",
    [
        Decimal("1.25"),
        Decimal("sNaN"),
        datetime(2026, 7, 11, tzinfo=timezone.utc),
        _EnumerationValue.ITEM,
        [1],
        {"code": 1},
        {1},
        _FrozenValue(1),
        _MutableValue(1),
        _IntegerSubclass(1),
        _DateSubclass(2026, 7, 11),
    ],
)
def test_trusted_candidate_token_rejects_non_exact_or_unsafe_values(
    candidate: object,
) -> None:
    assert _trusted_candidate_token(candidate) is None


def test_trusted_candidate_token_rejects_custom_value_without_running_hooks() -> None:
    candidate = _UnsafeValue()

    assert _trusted_candidate_token(candidate) is None
    assert candidate.callbacks == []


def _static_evidence(
    input_field: Input[type[object]],
    source: object,
    candidate: object,
    identification: dict[str, object] | None = None,
    *,
    source_index: int | None = None,
) -> _EnumerationEvidence:
    evidence = _trusted_enumeration_evidence(
        input_field,
        source,
        candidate,
        {} if identification is None else identification,
        source_index=source_index,
    )
    assert evidence is not None
    return evidence


@pytest.mark.parametrize("source", [[1, 2], (1, 2)])
def test_sequence_evidence_requires_same_candidate_at_same_position(
    source: list[int] | tuple[int, ...],
) -> None:
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    evidence = _static_evidence(input_field, source, source[1], source_index=1)

    assert evidence.authorizes(input_field, 2, {})

    if isinstance(source, list):
        source.reverse()
        assert not evidence.authorizes(input_field, 2, {})


def test_sequence_evidence_denies_replaced_or_removed_candidate() -> None:
    candidate = "candidate value not expected to be interned"
    source = ["first", candidate]
    input_field = cast(Input[type[object]], Input(str, possible_values=source))
    evidence = _static_evidence(input_field, source, candidate, source_index=1)

    source[1] = "replacement"
    assert not evidence.authorizes(input_field, candidate, {})

    source[:] = ["first"]
    assert not evidence.authorizes(input_field, candidate, {})


@pytest.mark.parametrize("source_type", [list, tuple])
def test_sequence_evidence_denies_distinct_same_bits_nan_after_normalization(
    source_type: type[list[object]] | type[tuple[object, ...]],
) -> None:
    emitted_nan = float("nan")
    normalized_nan = _same_bits_float(emitted_nan)
    source = source_type((emitted_nan,))
    input_field = cast(Input[type[object]], Input(float, possible_values=source))
    evidence = _static_evidence(
        input_field,
        source,
        emitted_nan,
        source_index=0,
    )

    assert evidence.authorizes(input_field, emitted_nan, {})
    assert not evidence.authorizes(input_field, normalized_nan, {})


def test_set_evidence_requires_current_membership_in_same_source() -> None:
    source = {1, 2}
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    evidence = _static_evidence(input_field, source, 2)

    assert evidence.authorizes(input_field, 2, {})
    source.remove(2)
    assert not evidence.authorizes(input_field, 2, {})


class _CollidingUnsafeSetMember:
    def __init__(self, collision_hash: int) -> None:
        self.collision_hash = collision_hash
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        self.callbacks.append("hash")
        return self.collision_hash

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        return False


@pytest.mark.parametrize("source_type", [set, frozenset])
def test_set_evidence_creation_rejects_unsafe_members_without_running_hooks(
    source_type: type[set[object]] | type[frozenset[object]],
) -> None:
    candidate = 2
    unsafe_member = _CollidingUnsafeSetMember(hash(candidate))
    source = source_type((candidate, unsafe_member))
    unsafe_member.callbacks.clear()
    input_field = cast(Input[type[object]], Input(int, possible_values=source))

    assert _trusted_enumeration_evidence(input_field, source, candidate, {}) is None
    assert unsafe_member.callbacks == []


def test_set_evidence_authorization_rejects_new_unsafe_member_without_hooks() -> None:
    candidate = 2
    source: set[object] = {candidate, 3}
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    evidence = _static_evidence(input_field, source, candidate)
    unsafe_member = _CollidingUnsafeSetMember(hash(candidate))
    source.remove(candidate)
    source.add(unsafe_member)
    unsafe_member.callbacks.clear()

    assert not evidence.authorizes(input_field, candidate, {})
    assert unsafe_member.callbacks == []


def _same_bits_float(value: float) -> float:
    replacement = cast(float, struct.unpack("!d", struct.pack("!d", value))[0])
    assert replacement is not value
    return replacement


@pytest.mark.parametrize("source_type", [set, frozenset])
def test_set_evidence_creation_requires_exact_emitted_nan_identity(
    source_type: type[set[object]] | type[frozenset[object]],
) -> None:
    emitted_nan = float("nan")
    separate_same_bits_nan = _same_bits_float(emitted_nan)
    source = source_type((emitted_nan,))
    input_field = cast(Input[type[object]], Input(float, possible_values=source))

    assert (
        _trusted_enumeration_evidence(
            input_field,
            source,
            separate_same_bits_nan,
            {},
        )
        is None
    )


def test_mutable_set_evidence_revokes_replaced_same_bits_nan() -> None:
    emitted_nan = float("nan")
    replacement_nan = _same_bits_float(emitted_nan)
    source: set[object] = {emitted_nan}
    input_field = cast(Input[type[object]], Input(float, possible_values=source))
    evidence = _static_evidence(input_field, source, emitted_nan)

    source.remove(emitted_nan)
    source.add(replacement_nan)

    assert not evidence.authorizes(input_field, emitted_nan, {})
    assert not evidence.authorizes(input_field, replacement_nan, {})


def test_frozenset_evidence_authorizes_only_exact_emitted_nan() -> None:
    emitted_nan = float("nan")
    separate_same_bits_nan = _same_bits_float(emitted_nan)
    source: frozenset[object] = frozenset((emitted_nan,))
    input_field = cast(Input[type[object]], Input(float, possible_values=source))
    evidence = _static_evidence(input_field, source, emitted_nan)

    assert evidence.authorizes(input_field, emitted_nan, {})
    assert not evidence.authorizes(input_field, separate_same_bits_nan, {})


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (NumericRangeDomain(1, 5, 2), 3),
        (
            DateRangeDomain(date(2026, 1, 1), date(2026, 1, 3)),
            date(2026, 1, 2),
        ),
    ],
)
def test_exact_builtin_domain_evidence_authorizes_emitted_candidate(
    source: NumericRangeDomain | DateRangeDomain,
    candidate: object,
) -> None:
    input_field = cast(
        Input[type[object]], Input(type(candidate), possible_values=source)
    )
    evidence = _static_evidence(input_field, source, candidate)

    assert evidence.authorizes(input_field, candidate, {})


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (NumericRangeDomain(1, 5, 2), 3),
        (
            DateRangeDomain(date(2026, 1, 1), date(2026, 1, 3)),
            date(2026, 1, 2),
        ),
    ],
)
def test_domain_behavior_override_prevents_evidence_creation_without_execution(
    source: NumericRangeDomain | DateRangeDomain,
    candidate: object,
) -> None:
    behavior_calls: list[object] = []

    def overridden_contains(value: object) -> bool:
        behavior_calls.append(value)
        return False

    object.__setattr__(source, "contains", overridden_contains)
    input_field = cast(
        Input[type[object]], Input(type(candidate), possible_values=source)
    )

    assert _trusted_enumeration_evidence(input_field, source, candidate, {}) is None
    assert behavior_calls == []


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (NumericRangeDomain(1, 5, 2), 3),
        (
            DateRangeDomain(date(2026, 1, 1), date(2026, 1, 3)),
            date(2026, 1, 2),
        ),
    ],
)
def test_domain_behavior_override_revokes_existing_evidence_without_execution(
    source: NumericRangeDomain | DateRangeDomain,
    candidate: object,
) -> None:
    input_field = cast(
        Input[type[object]], Input(type(candidate), possible_values=source)
    )
    evidence = _static_evidence(input_field, source, candidate)
    behavior_calls: list[object] = []

    def overridden_contains(value: object) -> bool:
        behavior_calls.append(value)
        return False

    object.__setattr__(source, "contains", overridden_contains)

    assert not evidence.authorizes(input_field, candidate, {})
    assert behavior_calls == []


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (NumericRangeDomain(1, 5, 2), 3),
        (
            DateRangeDomain(date(2026, 1, 1), date(2026, 1, 3)),
            date(2026, 1, 2),
        ),
    ],
)
def test_domain_hostile_state_key_prevents_evidence_without_running_hooks(
    source: NumericRangeDomain | DateRangeDomain,
    candidate: object,
) -> None:
    hostile_key = _HostileStateKey()
    hostile_key.hash_value = hash("kind")
    source.__dict__.pop("kind")
    cast(dict[object, object], source.__dict__)[hostile_key] = None
    hostile_key.callbacks.clear()
    input_field = cast(
        Input[type[object]], Input(type(candidate), possible_values=source)
    )

    assert _trusted_enumeration_evidence(input_field, source, candidate, {}) is None
    assert hostile_key.callbacks == []


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        (NumericRangeDomain(1, 5, 2), 3),
        (
            DateRangeDomain(date(2026, 1, 1), date(2026, 1, 3)),
            date(2026, 1, 2),
        ),
    ],
)
def test_domain_hostile_state_key_revokes_evidence_without_running_hooks(
    source: NumericRangeDomain | DateRangeDomain,
    candidate: object,
) -> None:
    input_field = cast(
        Input[type[object]], Input(type(candidate), possible_values=source)
    )
    evidence = _static_evidence(input_field, source, candidate)
    hostile_key = _HostileStateKey()
    hostile_key.hash_value = hash("kind")
    source.__dict__.pop("kind")
    cast(dict[object, object], source.__dict__)[hostile_key] = None
    hostile_key.callbacks.clear()

    assert not evidence.authorizes(input_field, candidate, {})
    assert hostile_key.callbacks == []


def test_evidence_denies_changed_input_provider_or_normalized_value() -> None:
    source = ["VALUE"]
    input_field = cast(Input[type[object]], Input(str, possible_values=source))
    evidence = _static_evidence(input_field, source, source[0], source_index=0)

    assert not evidence.authorizes(
        cast(Input[type[object]], Input(str, possible_values=source)), "VALUE", {}
    )

    input_field.possible_values = ["VALUE"]
    assert not evidence.authorizes(input_field, "VALUE", {})

    input_field.possible_values = source
    assert not evidence.authorizes(input_field, "value", {})


def test_static_dependency_snapshot_must_remain_safe_present_and_equal() -> None:
    source = [10]
    input_field = cast(
        Input[type[object]], Input(int, possible_values=source, depends_on=["root"])
    )
    evidence = _static_evidence(
        input_field,
        source,
        source[0],
        {"root": "A"},
        source_index=0,
    )

    assert evidence.authorizes(input_field, 10, {"root": "A"})
    assert not evidence.authorizes(input_field, 10, {})
    assert not evidence.authorizes(input_field, 10, {"root": "B"})
    assert not evidence.authorizes(input_field, 10, {"root": _UnsafeValue()})

    input_field.depends_on.append("other")
    assert not evidence.authorizes(input_field, 10, {"root": "A", "other": 1})


def test_changed_dependency_names_deny_without_running_custom_equality() -> None:
    source = [10]
    input_field = cast(
        Input[type[object]], Input(int, possible_values=source, depends_on=["root"])
    )
    evidence = _static_evidence(
        input_field,
        source,
        source[0],
        {"root": "A"},
        source_index=0,
    )
    unsafe_name = _UnsafeValue()
    cast(list[object], input_field.depends_on)[0] = unsafe_name

    assert not evidence.authorizes(input_field, 10, {"root": "A"})
    assert unsafe_name.callbacks == []


def test_unsafe_or_missing_static_dependency_prevents_evidence_creation() -> None:
    source = [10]
    input_field = cast(
        Input[type[object]], Input(int, possible_values=source, depends_on=["root"])
    )

    assert (
        _trusted_enumeration_evidence(input_field, source, 10, {}, source_index=0)
        is None
    )
    assert (
        _trusted_enumeration_evidence(
            input_field,
            source,
            10,
            {"root": _UnsafeValue()},
            source_index=0,
        )
        is None
    )


def test_callable_provider_is_rejected_without_invocation() -> None:
    calls: list[int] = []

    def provider() -> list[int]:
        calls.append(1)
        return [1]

    input_field = cast(Input[type[object]], Input(int, possible_values=provider))

    assert _trusted_enumeration_evidence(input_field, [1], 1, {}) is None
    assert calls == []


def test_custom_sources_and_input_subclasses_are_ineligible() -> None:
    class CustomInput(Input[type[int]]):
        pass

    class CustomIterable:
        def __iter__(self) -> Iterator[int]:
            yield 1

    source = CustomIterable()
    exact_input = cast(Input[type[object]], Input(int, possible_values=source))
    custom_input = cast(Input[type[object]], CustomInput(int, possible_values=[1]))

    assert _trusted_enumeration_evidence(exact_input, source, 1, {}) is None
    assert (
        _trusted_enumeration_evidence(
            custom_input, custom_input.possible_values, 1, {}, source_index=0
        )
        is None
    )


def test_input_subclass_is_rejected_before_accessing_hostile_attributes() -> None:
    class HostileInput(Input[type[int]]):
        callbacks: list[str]

        def __init__(self) -> None:
            object.__setattr__(self, "callbacks", [])

        def __getattribute__(self, name: str) -> object:
            if name != "callbacks":
                callbacks = cast(list[str], object.__getattribute__(self, "callbacks"))
                callbacks.append(name)
                raise AssertionError(name)
            return object.__getattribute__(self, name)

    input_field = HostileInput()

    assert (
        _trusted_enumeration_evidence(
            cast(Input[type[object]], input_field), [1], 1, {}, source_index=0
        )
        is None
    )
    assert input_field.callbacks == []


@pytest.mark.parametrize(
    "override_name",
    ["resolve_possible_values", "normalize", "cast", "_build_dependency_values"],
)
def test_late_input_behavior_override_revokes_existing_evidence(
    override_name: str,
) -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    evidence = _static_evidence(input_field, source, source[0], source_index=0)
    behavior_calls: list[int] = []

    def overridden_behavior(*_args: object, **_kwargs: object) -> object:
        behavior_calls.append(1)
        return source

    input_field.__dict__[override_name] = overridden_behavior

    assert not evidence.authorizes(input_field, 1, {})
    assert behavior_calls == []


def test_input_hostile_state_key_prevents_evidence_without_running_hooks() -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    hostile_key = _HostileStateKey()
    hostile_key.hash_value = hash("resolve_possible_values")
    cast(dict[object, object], input_field.__dict__)[hostile_key] = None
    hostile_key.callbacks.clear()

    assert (
        _trusted_enumeration_evidence(
            input_field,
            source,
            source[0],
            {},
            source_index=0,
        )
        is None
    )
    assert hostile_key.callbacks == []


def test_input_hostile_state_key_revokes_evidence_without_running_hooks() -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    evidence = _static_evidence(input_field, source, source[0], source_index=0)
    hostile_key = _HostileStateKey()
    hostile_key.hash_value = hash("resolve_possible_values")
    cast(dict[object, object], input_field.__dict__)[hostile_key] = None
    hostile_key.callbacks.clear()

    assert not evidence.authorizes(input_field, source[0], {})
    assert hostile_key.callbacks == []


def test_evidence_tracking_delegates_to_source_witness() -> None:
    class WitnessDouble:
        def __init__(self) -> None:
            self.track_calls = 0

        def authorizes(self, _value: object) -> bool:
            return True

        def track_membership_dependency(self) -> None:
            self.track_calls += 1

    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    token = _trusted_candidate_token(1)
    assert token is not None
    witness = WitnessDouble()
    evidence = _EnumerationEvidence(
        input_field=input_field,
        provider=source,
        dependency_names=(),
        dependency_tokens=(),
        candidate_token=token,
        witness=witness,
    )

    evidence.track_membership_dependency()

    assert witness.track_calls == 1


def test_private_materialization_retains_identity_keyed_evidence_off_dicts() -> None:
    source = [1, 2]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})

    combinations = bucket._materialize_combinations(expose=False)

    assert combinations == [{"code": 1}, {"code": 2}]
    assert all(type(combination) is dict for combination in combinations)
    assert all("_evidence" not in combination for combination in combinations)
    assert bucket._lookup_combination_evidence(combinations[0]) is not None
    assert bucket._lookup_combination_evidence(combinations[1]) is not None
    assert repr(combinations) == "[{'code': 1}, {'code': 2}]"


def test_source_position_is_captured_before_input_filters() -> None:
    source = [1, 2, 3]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    bucket._filters = {"code": {"filter_funcs": [lambda value: cast(int, value) >= 2]}}

    combinations = bucket._materialize_combinations(expose=False)
    evidence = bucket._lookup_combination_evidence(combinations[0])

    assert combinations == [{"code": 2}, {"code": 3}]
    assert evidence is not None
    assert evidence["code"].authorizes(input_field, 2, {})
    source.insert(0, 0)
    assert not evidence["code"].authorizes(input_field, 2, {})


@pytest.mark.parametrize(
    "possible_values",
    [
        lambda: [1],
        iter([1]),
    ],
)
def test_callable_and_iterator_sources_do_not_retain_evidence(
    possible_values: object,
) -> None:
    input_field = cast(
        Input[type[object]],
        Input(int, possible_values=cast(Any, possible_values)),
    )
    bucket = _calculation_bucket_with_inputs({"code": input_field})

    combinations = bucket._materialize_combinations(expose=False)

    assert combinations == [{"code": 1}]
    assert bucket._lookup_combination_evidence(combinations[0]) is None


def test_public_generation_permanently_invalidates_private_evidence() -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    combinations = bucket._materialize_combinations(expose=False)
    assert bucket._lookup_combination_evidence(combinations[0]) is not None

    public_combinations = bucket.generate_combinations()

    assert public_combinations is combinations
    assert bucket._lookup_combination_evidence(combinations[0]) is None
    assert bucket._evidence_exposed
    assert bucket._materialize_combinations(expose=False) is combinations
    assert bucket._lookup_combination_evidence(combinations[0]) is None


def test_slice_revokes_original_evidence_and_does_not_transfer_it() -> None:
    source = [1, 2]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    combinations = bucket._materialize_combinations(expose=False)
    assert bucket._lookup_combination_evidence(combinations[0]) is not None

    sliced = bucket[:1]

    assert isinstance(sliced, CalculationBucket)
    assert sliced._data == [{"code": 1}]
    assert sliced._data is not None
    assert sliced._data[0] is combinations[0]
    assert bucket._lookup_combination_evidence(combinations[0]) is None
    assert sliced._lookup_combination_evidence(sliced._data[0]) is None


def test_copy_pickle_union_and_none_never_transfer_evidence() -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    combinations = bucket._materialize_combinations(expose=False)
    assert bucket._lookup_combination_evidence(combinations[0]) is not None

    copied = bucket.all()
    shallow_copied = copy(bucket)
    combined = bucket | bucket
    empty = bucket.none()
    reduced = bucket.__reduce__()
    assert isinstance(reduced, tuple)
    reduced_class = cast(Any, reduced[0])
    reduced_args = cast(tuple[object, ...], reduced[1])
    reduced_state = cast(dict[str, object], reduced[2])
    restored = reduced_class(*reduced_args)
    restored.__setstate__(reduced_state)

    assert copied._combination_evidence == {}
    assert shallow_copied._combination_evidence == {}
    assert combined._combination_evidence == {}
    assert empty._combination_evidence == {}
    assert restored._combination_evidence == {}
    assert set(reduced_state) == {"data"}


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_publicly_exposed_stale_combination_keeps_membership_error() -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    public_combinations = bucket.generate_combinations()
    assert public_combinations == [{"code": 1}]
    source[0] = 2

    expected_error = r"^Invalid value for code: 1, allowed: \[2\]\.$"
    with pytest.raises(InvalidInputValueError, match=expected_error):
        _ = bucket[0]
    with pytest.raises(InvalidInputValueError, match=expected_error):
        next(iter(bucket))


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
def test_publicly_mutated_combination_keeps_membership_error() -> None:
    source = [1]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    public_combinations = bucket.generate_combinations()
    public_combinations[0]["code"] = 3

    with pytest.raises(
        InvalidInputValueError,
        match=r"^Invalid value for code: 3, allowed: \[1\]\.$",
    ):
        _ = bucket[0]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda source: source.insert(0, 0),
        lambda source: source.pop(0),
        lambda source: source.reverse(),
        lambda source: source.__setitem__(0, 9),
    ],
)
def test_private_sequence_evidence_detects_source_structure_changes(
    mutate: Callable[[list[int]], object],
) -> None:
    source = [1, 2]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    combinations = bucket._materialize_combinations(expose=False)
    evidence = bucket._lookup_combination_evidence(combinations[0])
    assert evidence is not None

    mutate(source)

    assert not evidence["code"].authorizes(input_field, 1, {})


def test_materialization_error_clears_partially_registered_evidence() -> None:
    source = [1, 2]
    input_field = cast(Input[type[object]], Input(int, possible_values=source))
    bucket = _calculation_bucket_with_inputs({"code": input_field})
    expected_error = RuntimeError("filter failed")

    def fail_for_second(value: object) -> bool:
        if value == 2:
            raise expected_error
        return True

    bucket._filters = {"code": {"filter_funcs": [fail_for_second]}}

    with pytest.raises(RuntimeError, match="filter failed"):
        bucket._materialize_combinations(expose=False)

    assert bucket._data is None
    assert bucket._combination_evidence == {}
