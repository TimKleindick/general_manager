"""One request contract for whole-case selection and explicit dialogue overrides."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .catalog import REFERENCE_VERSION, validate_reference_version
from .trace import fingerprint


class ValidationError(ValueError):
    """A local experimental validation request is inconsistent."""


def select_cases(
    cases: list[dict[str, Any]], case_ids: Sequence[str], scale: int | None
) -> list[dict[str, Any]]:
    duplicates = [cid for cid, count in Counter(case_ids).items() if count > 1]
    if duplicates:
        message = f"duplicate case IDs: {sorted(duplicates)}"
        raise ValidationError(message)
    unknown = set(case_ids) - {case["id"] for case in cases}
    if unknown:
        message = f"unknown case IDs: {sorted(unknown)}"
        raise ValidationError(message)
    selected = [
        case
        for case in cases
        if (not case_ids or case["id"] in case_ids)
        and (scale is None or case["managers"] == scale)
    ]
    if not selected:
        message = "no cases match selection"
        raise ValidationError(message)
    return selected


def dialogue_input(
    case: Mapping[str, Any], first_message: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace turn one only; keep the original case and every follow-up intact."""
    if first_message is not None and (
        not isinstance(first_message, str) or not first_message.strip()
    ):
        message = "first message must be a nonempty string"
        raise ValidationError(message)
    turns = case.get("turns")
    if (
        not isinstance(turns, list)
        or not turns
        or any(not isinstance(turn, str) or not turn.strip() for turn in turns)
    ):
        message = "a case requires complete nonempty original turns"
        raise ValidationError(message)
    effective = deepcopy(dict(case))
    if first_message is not None:
        effective["turns"][0] = first_message
    return effective, {
        "version": 1,
        "first_message_override": first_message,
        "original_turns": list(turns),
        "effective_turns": list(effective["turns"]),
        "original_case_sha256": fingerprint(case),
        "effective_case_sha256": fingerprint(effective),
        "original_prompt_sha256": fingerprint(turns),
        "effective_prompt_sha256": fingerprint(effective["turns"]),
        "benchmark_comparable": first_message is None,
    }


def build_run_request(
    catalog: list[dict[str, Any]],
    case_ids: Sequence[str] = (),
    *,
    first_message: str | None = None,
    reference_version: str = REFERENCE_VERSION,
) -> dict[str, Any]:
    """Freeze intent, not start authority. No IDs means the entire catalog."""
    validate_reference_version(reference_version)
    cases = deepcopy(select_cases(catalog, case_ids, None))
    dialogues = {case["id"]: dialogue_input(case, first_message)[1] for case in cases}
    return {
        "schema_version": "gm.eval-run-request/1",
        "reference_version": reference_version,
        "catalog_sha256": fingerprint(catalog),
        "case_ids": [case["id"] for case in cases],
        "cases": cases,
        "denominator": len(cases),
        "whole_turns": sum(len(case["turns"]) for case in cases),
        "first_message": first_message,
        "dialogues": dialogues,
        "benchmark_comparable": first_message is None,
    }


def validate_run_request(
    request: Mapping[str, Any], catalog: list[dict[str, Any]]
) -> dict[str, Any]:
    """Rebuild every field from the original catalog; reject edited frozen input."""
    expected = build_run_request(
        catalog,
        request["case_ids"],
        first_message=request["first_message"],
        reference_version=request["reference_version"],
    )
    if fingerprint(request) != fingerprint(expected):
        message = "run request does not match the complete original selection"
        raise ValidationError(message)
    return expected
