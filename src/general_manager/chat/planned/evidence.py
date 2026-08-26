"""Immutable, JSON-backed evidence for a planned chat turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Literal, NoReturn, TypeAlias

from general_manager.chat.planned.models import EvidenceRequirement


EvidenceKind: TypeAlias = Literal["schema", "path", "query", "calculation"]
_EVIDENCE_KINDS = frozenset(("schema", "path", "query", "calculation"))
_REDACTED = "[redacted]"
_SAFE_PROVENANCE_KEYS = frozenset(
    {
        "tool",
        "manager",
        "operation",
        "calculator",
        "kind",
        "relation",
        "direction",
        "field",
    }
)


class EvidenceError(ValueError):
    """Base class for invalid evidence or evidence links."""


class InvalidEvidenceError(EvidenceError):
    """Raised when an evidence record cannot be represented safely."""


class DuplicateEvidenceError(EvidenceError):
    """Raised when a store receives an evidence ID it already contains."""


class EvidenceNotFoundError(EvidenceError):
    """Raised when an evidence link references an unknown record."""


class IncompatibleEvidenceError(EvidenceError):
    """Raised when a requirement cannot be satisfied by an evidence record."""


# A descriptive alias is useful to callers that treat linking as its own error
# category while preserving one stable implementation and public base class.
EvidenceLinkError = IncompatibleEvidenceError


def _invalid(message: str, *, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise InvalidEvidenceError(message)
    raise InvalidEvidenceError(message) from cause


def _type_error(message: str, *, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise TypeError(message)
    raise TypeError(message) from cause


def _value_error(message: str) -> NoReturn:
    raise ValueError(message)


def _duplicate(message: str) -> NoReturn:
    raise DuplicateEvidenceError(message)


def _incompatible(message: str) -> NoReturn:
    raise IncompatibleEvidenceError(message)


def _not_found(message: str) -> NoReturn:
    raise EvidenceNotFoundError(message)


def _reject_json_constant(value: str) -> Any:
    _value_error(f"non-finite JSON constant {value!r} is not allowed")


def _decode_json(value: str) -> Any:
    if not isinstance(value, str):
        _invalid("payload_json must be a string.")
    try:
        return json.loads(value, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _invalid("payload_json must contain valid JSON.", cause=exc)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        _invalid("evidence payload must contain JSON values.", cause=exc)


def _provenance(value: Mapping[str, str] | None) -> Mapping[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        _invalid("provenance must be a mapping.")
    sanitized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            _invalid("provenance keys and values must be strings.")
        normalized_key = key.casefold()
        sanitized[key] = item if normalized_key in _SAFE_PROVENANCE_KEYS else _REDACTED
    return MappingProxyType(dict(sorted(sanitized.items())))


def _validate_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{label} must be a non-empty string.")
    return value


def canonical_call_identity(name: str, args: Mapping[str, Any]) -> str:
    """Return canonical JSON for one tool name and its JSON arguments."""
    _validate_text(name, "name")
    if not isinstance(args, Mapping):
        _type_error("args must be a mapping.")
    normalized: dict[str, Any] = {}
    for key, value in args.items():
        if not isinstance(key, str):
            _type_error("tool argument keys must be strings.")
        normalized[key] = _normalize_call_value(value)
    try:
        return json.dumps(
            {"name": name, "args": normalized},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        _type_error("tool arguments must be JSON-compatible.", cause=exc)


def _normalize_call_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            _value_error("tool arguments cannot contain non-finite numbers.")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _type_error("tool argument keys must be strings.")
            normalized[key] = _normalize_call_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_call_value(item) for item in value]
    _type_error("tool arguments must be JSON-compatible.")


@dataclass(frozen=True)
class EvidenceRecord:
    """One immutable snapshot of successful, structured evidence."""

    evidence_id: str
    task_id: str
    kind: EvidenceKind
    call_identity: str
    provenance: Mapping[str, str]
    payload_json: str

    def __post_init__(self) -> None:
        _validate_text(self.evidence_id, "evidence_id")
        _validate_text(self.task_id, "task_id")
        if self.kind not in _EVIDENCE_KINDS:
            _invalid(f"unsupported evidence kind {self.kind!r}.")
        _validate_text(self.call_identity, "call_identity")
        parsed = _decode_json(self.payload_json)
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        object.__setattr__(self, "payload_json", _canonical_json(parsed))

    @classmethod
    def create(
        cls,
        evidence_id: str,
        task_id: str,
        kind: EvidenceKind,
        call_identity: str,
        provenance: Mapping[str, str] | None,
        payload: object,
    ) -> "EvidenceRecord":
        """Create a record by serializing a detached JSON payload."""
        return cls(
            evidence_id=evidence_id,
            task_id=task_id,
            kind=kind,
            call_identity=call_identity,
            provenance={} if provenance is None else provenance,
            payload_json=_canonical_json(payload),
        )

    def payload(self) -> Any:
        """Decode and return a fresh payload snapshot on every access."""
        return _decode_json(self.payload_json)


class EvidenceStore:
    """Turn-local evidence collection with explicit requirement linking."""

    def __init__(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._links: dict[tuple[str, str], list[str]] = {}

    def add(
        self,
        record: EvidenceRecord,
        *,
        requirement: EvidenceRequirement | None = None,
    ) -> EvidenceRecord:
        if not isinstance(record, EvidenceRecord):
            _type_error("record must be an EvidenceRecord.")
        if record.evidence_id in self._records:
            _duplicate(f"evidence ID {record.evidence_id!r} already exists.")
        if requirement is not None and not _compatible(requirement, record):
            _incompatible(
                f"evidence {record.evidence_id!r} cannot satisfy requirement "
                f"{requirement.requirement_id!r}."
            )
        self._records[record.evidence_id] = record
        if requirement is not None:
            self._links.setdefault(
                (record.task_id, requirement.requirement_id), []
            ).append(record.evidence_id)
        return record

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self._records.get(evidence_id)

    def for_task(self, task_id: str) -> tuple[EvidenceRecord, ...]:
        return tuple(
            record for record in self._records.values() if record.task_id == task_id
        )

    def link(
        self, task_id: str, requirement: EvidenceRequirement, evidence_id: str
    ) -> EvidenceRecord:
        _validate_text(task_id, "task_id")
        if not isinstance(requirement, EvidenceRequirement):
            _type_error("requirement must be an EvidenceRequirement.")
        record = self._records.get(evidence_id)
        if record is None:
            _not_found(f"evidence ID {evidence_id!r} does not exist.")
        if record.task_id != task_id:
            _incompatible(
                f"evidence {evidence_id!r} belongs to task {record.task_id!r}, "
                f"not {task_id!r}."
            )
        if not _compatible(requirement, record):
            _incompatible(
                f"evidence {evidence_id!r} cannot satisfy requirement "
                f"{requirement.requirement_id!r}."
            )
        linked = self._links.setdefault((task_id, requirement.requirement_id), [])
        if evidence_id not in linked:
            linked.append(evidence_id)
        return record

    def for_requirement(
        self, task_id: str, requirement: EvidenceRequirement
    ) -> tuple[EvidenceRecord, ...]:
        _validate_text(task_id, "task_id")
        if not isinstance(requirement, EvidenceRequirement):
            _type_error("requirement must be an EvidenceRequirement.")
        return tuple(
            self._records[evidence_id]
            for evidence_id in self._links.get(
                (task_id, requirement.requirement_id), ()
            )
        )

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records.values())


def _compatible(requirement: EvidenceRequirement, record: EvidenceRecord) -> bool:
    if requirement.kind != record.kind:
        return False
    if requirement.kind != "calculation":
        return requirement.operation is None
    if requirement.operation is None:
        return False
    payload = record.payload()
    return (
        isinstance(payload, Mapping)
        and payload.get("operation") == requirement.operation
    )


__all__ = [
    "DuplicateEvidenceError",
    "EvidenceError",
    "EvidenceKind",
    "EvidenceLinkError",
    "EvidenceNotFoundError",
    "EvidenceRecord",
    "EvidenceStore",
    "IncompatibleEvidenceError",
    "InvalidEvidenceError",
    "canonical_call_identity",
]
