"""Separate deterministic facts, physical evidence and blind semantic judgment.

This is a trust boundary, not an answer parser: ``facts`` must come from a
separate validated extraction of the saved answer and real evidence. Neither
model-authored facts nor absent judgments count as success. The harness supplies
registry/trace data; the tested model cannot certify its own interface type.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any

from .catalog import DIMENSIONS
from .evidence_rows import query_identity_rows
from .presentation_normalization import normalize_presentations
from .query_completeness import complete_identity_queries
from .semantic_contracts import valid_repetition_judgment

CLARIFICATION_TOPICS = frozenset(
    {"criterion", "metric", "horizon", "customer_identity", "unit"}
)

FAILURE_HIERARCHY = (
    "fixture_invalid",
    "interface_capability_gap",
    "harness_failure",
    "transport_failure",
    "judge_failure",
    "budget_exhausted",
    "model_task_failure",
)


def _digest(answer: Any) -> str:
    return sha256(str(answer).encode("utf-8")).hexdigest()


def evidence_trace_digest(trace: Any) -> str:
    """Fingerprint the exact normalized tool/history content reviewed externally."""
    return sha256(
        json.dumps(
            trace,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _matches(expected: Any, actual: Any, comparison: str = "exact") -> bool:
    if comparison == "required_topic_groups":
        return (
            isinstance(expected, list)
            and bool(expected)
            and all(
                isinstance(group, list)
                and bool(group)
                and all(
                    isinstance(topic, str) and topic in CLARIFICATION_TOPICS
                    for topic in group
                )
                and len(group) == len(set(group))
                for group in expected
            )
            and isinstance(actual, list)
            and all(
                isinstance(topic, str) and topic in CLARIFICATION_TOPICS
                for topic in actual
            )
            and len(actual) == len(set(actual))
            and all(any(topic in actual for topic in group) for group in expected)
        )
    if comparison == "required_topics":
        return (
            isinstance(actual, list)
            and isinstance(expected, list)
            and all(
                isinstance(item, str) and item in CLARIFICATION_TOPICS
                for item in actual
            )
            and len(actual) == len(set(actual))
            and set(expected) <= set(actual)
        )
    if comparison == "set":
        if not isinstance(actual, list) or not isinstance(expected, list):
            return False
        # Duplicate result rows are not a complete, deduplicated result set.
        return (
            len(actual) == len(expected)
            and all(any(_matches(item, value) for value in actual) for item in expected)
            and len({repr(item) for item in actual}) == len(actual)
        )
    if expected is None or isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and all(_matches(value, actual[key]) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _matches(left, right)
                for left, right in zip(expected, actual, strict=True)
            )
        )
    if isinstance(actual, bool) or actual is None:
        return False
    if isinstance(expected, (int, float, Decimal, str)) and isinstance(
        actual, (int, float, Decimal, str)
    ):
        try:
            left, right = Decimal(str(expected)), Decimal(str(actual))
        except InvalidOperation:
            return bool(expected == actual)
        return left.is_finite() and right.is_finite() and left == right
    return bool(expected == actual)


def _check(field: str, status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {"field": field, "status": status, "reason": reason, **details}


def _field_identity(name: str) -> str:
    return name.replace("_", "").casefold()


def _has_output_field(output: Any, name: str) -> bool:
    if isinstance(output, Mapping):
        normalized = _field_identity(name)
        return any(
            _field_identity(str(key)) == normalized or _has_output_field(value, name)
            for key, value in output.items()
        )
    if isinstance(output, list):
        return any(_has_output_field(value, name) for value in output)
    return False


def _evidence(
    observation: Mapping[str, Any], turn_index: int
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    trace = observation.get("trace") or {}
    if not isinstance(trace, Mapping):
        return {}, ["trace must be an object"]
    records = trace.get("evidence") or []
    calls = trace.get("tool_calls") or []
    history = trace.get("history_evidence") or []
    if not all(
        isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        for rows in (records, calls, history)
    ):
        return {}, [
            "evidence, tool_calls and history_evidence must be arrays of objects"
        ]
    calls_by_id = {row.get("id"): row for row in calls}
    history_by_id = {row.get("id"): row for row in history}
    valid: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    identity_queries: dict[str, dict[str, Any]] = {}

    def real_call(record: Mapping[str, Any]) -> bool:
        call = calls_by_id.get(record.get("call_id"))
        return (
            isinstance(call, dict)
            and record.get("manager") in (call.get("managers") or [])
            and ("output" in call or "result" in call)
            and not call.get("error")
        )

    for record in records:
        identifier = record.get("id")
        manager = record.get("manager")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in valid
            or not isinstance(manager, str)
        ):
            errors.append(
                "evidence IDs and manager names must be unique nonempty strings"
            )
            continue
        origin = record.get("origin")
        source_record = record
        if origin == "tool":
            grounded = real_call(record)
        elif origin == "history":
            source = history_by_id.get(record.get("source_evidence_id"))
            source_turn = record.get("source_turn")
            source_record = source if isinstance(source, dict) else {}
            grounded = (
                isinstance(source_turn, int)
                and not isinstance(source_turn, bool)
                and 0 <= source_turn < turn_index
                and isinstance(source, dict)
                and source.get("manager") == manager
                and source.get("origin") == "tool"
                and real_call(source)
            )
        else:
            grounded = False
        if not grounded:
            errors.append(
                f"Evidence {identifier} is not linked to a real tool result or prior-turn evidence"
            )
        else:
            call = calls_by_id[source_record["call_id"]]
            fields = (call.get("manager_fields") or {}).get(manager, [])
            output = call.get("output", call.get("result"))
            identity_rows, identity_proofs = query_identity_rows(call, calls, manager)
            if identity_rows:
                identity_queries[call["id"]] = call
            valid[identifier] = {
                **record,
                "root_manager": call.get("root_manager", manager),
                "identity_rows": identity_rows,
                "identity_proofs": identity_proofs,
                "root_query": call
                if call.get("name") == "query"
                and call.get("root_manager") == manager
                and isinstance(call.get("arguments"), Mapping)
                and call["arguments"].get("manager") == manager
                and isinstance(output, dict)
                and isinstance(output.get("data"), list)
                and all(isinstance(row, dict) for row in output["data"])
                else None,
                "fields": [
                    name
                    for name in fields
                    if isinstance(name, str) and _has_output_field(output, name)
                ],
            }
    # Reuse typed query proofs for object roles without adding evidence records
    # or crediting identities for managers absent from actual evidence records.
    manager_rows = {
        identifier: {
            tuple(proof["row_path"])
            for manager in call["managers"]
            for proof in query_identity_rows(call, calls, manager)[1]
        }
        for identifier, call in identity_queries.items()
    }
    complete = complete_identity_queries(identity_queries, manager_rows)
    for record in valid.values():
        if any(
            proof["query_call_id"] not in complete
            for proof in record["identity_proofs"]
        ):
            record["identity_rows"] = []
            record["identity_proofs"] = []
    return valid, errors


def _identifier_key(value: Any) -> str | None:
    """Match literal integer/string IDs without bool/null/numeric coercion."""
    if type(value) is int:
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def _returned_identities(
    manager: str,
    source_rows: list[dict[str, Any]],
    evidence: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Resolve selected typed rows against the complete manager identity source."""
    codes: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, list[str]] = {}
    for row in source_rows:
        codes.setdefault(row["code"], []).append(row)
        if isinstance(row.get("name"), str):
            names.setdefault(row["name"], []).append(row["code"])
    candidates: dict[str, dict[str, list[dict[str, Any]]]] = {}
    blocked: dict[str, str] = {}
    for evidence_id, record in evidence.items():
        if record["manager"] != manager:
            continue
        for position, row in enumerate(record["identity_rows"]):
            identifier = _identifier_key(row.get("id"))
            if identifier is None:
                continue
            # An identity-free selection contributes no mapping or contradiction.
            # Another selected typed row may independently identify the same ID.
            if "code" not in row and "name" not in row:
                continue
            if "code" in row:
                code = row["code"]
                matches = codes.get(code, []) if isinstance(code, str) else []
                if len(matches) != 1:
                    blocked[identifier] = "unknown_or_ambiguous_returned_code"
                    continue
                if "name" in row and row["name"] != matches[0].get("name"):
                    blocked[identifier] = "conflicting_returned_code_and_name"
                    continue
                identity_field = "code"
            else:
                name = row.get("name")
                matching_codes = names.get(name, []) if isinstance(name, str) else []
                if len(matching_codes) != 1:
                    blocked[identifier] = (
                        "ambiguous_source_name"
                        if len(matching_codes) > 1
                        else "missing_or_unknown_returned_identity"
                    )
                    continue
                code = matching_codes[0]
                identity_field = "name"
            candidates.setdefault(identifier, {}).setdefault(code, []).append(
                {
                    "evidence_id": evidence_id,
                    "row_index": position,
                    "identity_field": identity_field,
                    "identity_value": row[identity_field],
                    **record["identity_proofs"][position],
                }
            )
    code_ids: dict[str, set[str]] = {}
    for identifier, matches_by_code in candidates.items():
        if len(matches_by_code) != 1:
            blocked[identifier] = "conflicting_returned_id"
        for code in matches_by_code:
            code_ids.setdefault(code, set()).add(identifier)
    for identifiers in code_ids.values():
        if len(identifiers) > 1:
            for identifier in identifiers:
                blocked.setdefault(identifier, "multiple_returned_ids_for_entity")
    resolved: dict[str, dict[str, Any]] = {}
    for identifier, matches_by_code in candidates.items():
        if identifier in blocked:
            continue
        code, proof = next(iter(matches_by_code.items()))
        resolved[identifier] = {
            "canonical_code": code,
            "evidence_ids": sorted({row["evidence_id"] for row in proof}),
            "proof": proof,
        }
    return resolved, blocked


def _designation_audit(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
    evidence: Mapping[str, dict[str, Any]],
    *,
    extracted: bool,
) -> dict[str, Any]:
    """Normalize a designation from one bound entity, never from expected text."""
    raw = facts.get("designation") if facts is not None else None
    audit: dict[str, Any] = {
        "field": "designation",
        "raw_observed": raw,
        "normalized_observed": raw,
        "evidence_ids": [],
        "proof": [],
        "rule": "bound-returned-name-and-aliases-v1",
    }
    if not extracted or not isinstance(raw, str) or facts is None:
        return audit
    reference = expectation.get("entity_identity_reference", {})
    descriptor = reference.get("fields", {}).get("result_ids", {})
    manager = descriptor.get("manager")
    identifiers = facts.get("result_ids")
    if (
        not isinstance(manager, str)
        or descriptor.get("shape") != "list"
        or not isinstance(identifiers, list)
        or len(identifiers) != 1
    ):
        return audit
    support = observation.get("fact_support")
    extraction = observation.get("extraction")
    if not isinstance(support, Mapping) or not isinstance(extraction, Mapping):
        return audit
    eligible = set(extraction.get("evidence_ids", []))
    for field in ("designation", "result_ids"):
        item = support.get(field)
        if not isinstance(item, Mapping) or item.get("status") != "present":
            return audit
        eligible &= set(item.get("evidence_ids", []))
    bound = {key: row for key, row in evidence.items() if key in eligible}
    identities, _ = _returned_identities(
        manager, reference.get("managers", {}).get(manager, []), bound
    )
    candidates: list[tuple[str, frozenset[str], str, dict[str, Any]]] = []
    for evidence_id, record in bound.items():
        if record["manager"] != manager:
            continue
        for index, row in enumerate(record["identity_rows"]):
            identity = identities.get(_identifier_key(row.get("id")) or "")
            if identity is None or identity["canonical_code"] != identifiers[0]:
                continue
            selected = record["identity_proofs"][index]["selected_fields"]
            if not {"id", "name"} <= set(selected):
                return audit
            name = row.get("name")
            aliases = row.get("aliases") if "aliases" in selected else None
            if not isinstance(name, str) or not name.strip():
                return audit
            if isinstance(aliases, str):
                terms = aliases.split("|")
            elif isinstance(aliases, list) and all(isinstance(x, str) for x in aliases):
                terms = aliases
            else:
                terms = []
            labels = frozenset([name.strip(), *(x.strip() for x in terms if x.strip())])
            candidates.append(
                (name, labels, evidence_id, record["identity_proofs"][index])
            )
    # Conflicting records for the same entity must not be combined into a
    # permissive vocabulary; every returned definition must agree.
    if (
        not candidates
        or len({(name, labels) for name, labels, _, _ in candidates}) != 1
    ):
        return audit
    name, labels, _, _ = candidates[0]
    parts = [raw.strip()]
    if parts[0] not in labels:
        paired = re.fullmatch(r"([^()]+)\s+\(([^()]+)\)", raw.strip())
        if paired is None:
            return audit
        parts = [part.strip() for part in paired.groups()]
    other_names = {
        row["name"]
        for row in reference.get("managers", {}).get(manager, [])
        if row["code"] != identifiers[0] and isinstance(row.get("name"), str)
    }
    if not all(part in labels and part not in other_names for part in parts):
        return audit
    audit.update(
        normalized_observed=name,
        evidence_ids=sorted({item[2] for item in candidates}),
        returned_name=name,
        returned_labels=sorted(labels),
        matched_terms=parts,
        proof=[{"evidence_id": item[2], **item[3]} for item in candidates],
    )
    return audit


def _normalize_entity_facts(
    expectation: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
    evidence: Mapping[str, dict[str, Any]],
    *,
    extracted: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    """Canonicalize declared entity fields, retaining raw facts and every proof."""
    normalized = deepcopy(dict(facts)) if facts is not None else None
    audit: dict[str, Any] = {
        "scope": "verified_selected_typed_query_rows",
        "raw_facts": deepcopy(normalized),
        "normalized_facts": normalized,
        "mappings": [],
        "unresolved": [],
    }
    uncertain: set[str] = set()
    if not extracted or normalized is None:
        return normalized, audit, uncertain
    reference = expectation.get("entity_identity_reference", {})
    managers = reference.get("managers", {})
    maps = {
        manager: _returned_identities(manager, rows, evidence)
        for manager, rows in managers.items()
    }

    def normalize(
        value: Any, field: str, manager: str, path: list[str] | None = None
    ) -> Any:
        canonical_codes = {row["code"] for row in managers[manager]}
        resolved, blocked = maps[manager]
        identifier = _identifier_key(value)
        if isinstance(value, str) and value in canonical_codes:
            return value
        if identifier is None:
            # Invalid result IDs still fail comparison; bool/null must not
            # alias database integer IDs through Python's equality rules.
            audit["unresolved"].append(
                {
                    "field": field,
                    "manager": manager,
                    "observed": value,
                    "reason": "invalid_identifier",
                    "status": "invalid",
                }
            )
            return value
        if identifier not in resolved:
            uncertain.add(field)
            audit["unresolved"].append(
                {
                    "field": field,
                    "manager": manager,
                    "observed": value,
                    "reason": blocked.get(identifier, "no_eligible_returned_identity"),
                    "status": "unscored",
                }
            )
            return value
        mapping = resolved[identifier]
        audit["mappings"].append(
            {
                "field": field,
                "manager": manager,
                "observed": value,
                **mapping,
                **({"path": path} if path else {}),
            }
        )
        return mapping["canonical_code"]

    for field, descriptor in reference.get("fields", {}).items():
        if field not in normalized:
            continue
        manager = descriptor["manager"]
        value = normalized[field]
        if descriptor["shape"] == "list" and isinstance(value, list):
            normalized[field] = [normalize(item, field, manager) for item in value]
        elif descriptor["shape"] == "mapping_keys" and isinstance(value, dict):
            entries = [
                (normalize(key, field, manager), item) for key, item in value.items()
            ]
            if len({key for key, _ in entries}) != len(entries):
                audit["unresolved"].append(
                    {
                        "field": field,
                        "manager": manager,
                        "reason": "duplicate_entity_keys",
                        "status": "invalid",
                    }
                )
            else:
                normalized[field] = dict(entries)
    for descriptor in reference.get("paths", []):
        path = descriptor["path"]
        parent = normalized
        for part in path[:-1]:
            child = parent.get(part)
            if not isinstance(child, dict):
                break
            parent = child
        else:
            if path[-1] in parent:
                value = parent[path[-1]]
                if descriptor["shape"] == "list" and isinstance(value, list):
                    parent[path[-1]] = [
                        normalize(
                            item, path[0], descriptor["manager"], [*path, str(index)]
                        )
                        for index, item in enumerate(value)
                    ]
                elif descriptor["shape"] == "scalar":
                    parent[path[-1]] = normalize(
                        value, path[0], descriptor["manager"], path
                    )
    return normalized, audit, uncertain


def _known_identity_contradictions(
    expected: Any, actual: Any, comparison: str, canonical_codes: set[str]
) -> list[dict[str, Any]]:
    """Keep proven errors while leaving unrelated unknown identities unresolved."""
    contradictions: list[dict[str, Any]] = []
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual) or len({repr(item) for item in actual}) != len(
            actual
        ):
            contradictions.append(
                {"reason": "missing_extra_or_duplicate_ids", "observed": actual}
            )
        for index, identifier in enumerate(actual):
            if not isinstance(identifier, str) or identifier not in canonical_codes:
                continue
            if comparison == "set":
                if not any(_matches(value, identifier) for value in expected):
                    contradictions.append(
                        {
                            "reason": "unexpected_canonical_entity",
                            "observed": identifier,
                        }
                    )
            elif index < len(expected) and not _matches(expected[index], identifier):
                contradictions.append(
                    {
                        "reason": "wrong_canonical_position",
                        "index": index,
                        "expected": expected[index],
                        "observed": identifier,
                    }
                )
    elif isinstance(expected, dict) and isinstance(actual, dict):
        if len(expected) != len(actual):
            contradictions.append(
                {"reason": "missing_or_extra_entity_keys", "observed": list(actual)}
            )
        for identifier, value in actual.items():
            if not isinstance(identifier, str) or identifier not in canonical_codes:
                continue
            if identifier not in expected:
                contradictions.append(
                    {"reason": "unexpected_canonical_key", "observed": identifier}
                )
            elif not _matches(expected[identifier], value):
                contradictions.append(
                    {
                        "reason": "wrong_value_for_canonical_key",
                        "key": identifier,
                        "expected": expected[identifier],
                        "observed": value,
                    }
                )
    return contradictions


def _sources_satisfied(
    requirement: Mapping[str, Any],
    managers: set[str],
    fields: Mapping[str, set[str]] | None = None,
) -> bool:
    return any(
        set(alternative) <= managers
        and (
            fields is None
            or all(
                {
                    _field_identity(name)
                    for name in requirement.get("required_fields", {}).get(manager, [])
                }
                <= {_field_identity(name) for name in fields.get(manager, set())}
                for manager in alternative
            )
        )
        for alternative in requirement["alternatives"]
    )


def _evidence_fields(records: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    for record in records:
        fields.setdefault(record["manager"], set()).update(record["fields"])
    return fields


def _internal_grounding_check(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
    evidence: Mapping[str, dict[str, Any]],
    extracted: bool,
    judgment: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind data claims to independently reviewed, eligible real tool results.

    Calling a tool is insufficient: each data fact needs external answer-quote
    support and the independent answer-supported judgment must use the required
    sources. Visible citation prose and model-authored IDs cannot replace this.
    """
    if not extracted:
        return _check(
            "evidence_grounding", "unscored", "No validated external extraction"
        )
    support = observation.get("fact_support")
    extraction = observation["extraction"]
    answer = observation.get("answer", "")
    fields = {
        row["field"]
        for dimension in ("R", "C", "I")
        for row in expectation["dimensions"][dimension]["checks"]
    }
    linked: set[str] = set()
    unsupported: list[str] = []
    for field in sorted(fields):
        item = support.get(field) if isinstance(support, Mapping) else None
        scope = expectation.get("selection_scope_contract")
        if (
            scope
            and field == scope["claims_field"]
            and isinstance(item, Mapping)
            and item.get("status") == "absent"
            and observation.get("facts", {}).get(field) is None
        ):
            # Effective scope is independently checked against actual selected rows.
            continue
        refs = item.get("evidence_ids") if isinstance(item, Mapping) else None
        quotes = item.get("answer_quotes") if isinstance(item, Mapping) else None
        valid = (
            isinstance(item, Mapping)
            and item.get("status") == "present"
            and isinstance(refs, list)
            and bool(refs)
            and all(isinstance(ref, str) and ref in evidence for ref in refs)
            and len(set(refs)) == len(refs)
            and all(ref in extraction["evidence_ids"] for ref in refs)
            and isinstance(quotes, list)
            and bool(quotes)
            and all(
                isinstance(quote, str) and quote.strip() and quote in answer
                for quote in quotes
            )
        )
        if valid and isinstance(refs, list):
            linked.update(refs)
        else:
            unsupported.append(field)
    # This validates only the source binding. The normal semantic checks below
    # still verify the judgment's hash/header and its independent truth verdict.
    semantic = (judgment or {}).get("checks", {})
    answer_check = (
        semantic.get("answer_supported", {}) if isinstance(semantic, Mapping) else {}
    )
    reviewed = (
        answer_check.get("evidence_ids", [])
        if isinstance(answer_check, Mapping)
        else []
    )
    reviewed_ids = (
        {ref for ref in reviewed if isinstance(ref, str) and ref in evidence}
        if isinstance(reviewed, list)
        else set()
    )
    bound = linked & reviewed_ids
    records = [evidence[ref] for ref in sorted(bound)]
    covered = bool(bound) and all(
        _sources_satisfied(
            requirement, {row["manager"] for row in records}, _evidence_fields(records)
        )
        for requirement in expectation["source_requirements"]
    )
    return _check(
        "evidence_grounding",
        "pass" if covered and not unsupported else "fail",
        "Data facts and independent answer support must bind to actual required tool evidence",
        evidence_ids=sorted(bound),
        unsupported_fields=unsupported,
    )


def _unit_audit(
    expectation: Mapping[str, Any], facts: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Exact spelling aliases only; never rescale values or normalize other text."""
    observed = facts.get("unit") if facts is not None else None
    aliases = {"g/cm³": "g/cm3", "g/cm^3": "g/cm3", "kg/m³": "kg/m3", "kg/m^3": "kg/m3"}
    rule = expectation.get("unit_normalization")
    enabled = rule in {"typography-v1", "unit-labels-v2"}
    if rule == "unit-labels-v2":
        aliases["Stück"] = "pieces"
    normalized = (
        aliases.get(observed, observed)
        if enabled and isinstance(observed, str)
        else observed
    )
    context = facts.get("context") if isinstance(facts, Mapping) else None
    context_unit = context.get("unit") if isinstance(context, Mapping) else None
    normalized_context = (
        aliases.get(context_unit, context_unit)
        if enabled and isinstance(context_unit, str)
        else context_unit
    )
    return {
        "context": {
            "raw_observed": context_unit,
            "normalized_observed": normalized_context,
            "changed": context_unit != normalized_context,
        },
        "field": "unit",
        "rule": rule if enabled else None,
        "raw_observed": observed,
        "normalized_observed": normalized,
        "changed": observed != normalized,
    }


def _query_signature_digest(signature: Mapping[str, Any]) -> str:
    """Ignore JSON object key order while retaining array order and values."""
    return sha256(
        json.dumps(
            signature,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _complete_scope_queries(
    linked: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Keep whole root queries or complete, consistently ordered native pages.

    Every page must already be bound by both extraction and fact support. The
    tool's ``complete=False`` on an individual page is expected; completeness
    here follows from contiguous page numbers, row counts and unique row IDs.
    """
    complete: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for ref, record in linked.items():
        call = record.get("root_query")
        if not isinstance(call, Mapping):
            continue
        args = call["arguments"]
        output = call.get("output", call.get("result"))
        native = args.get("arguments", {})
        if not isinstance(native, Mapping):
            continue
        page, size = native.get("page", 1), native.get("pageSize")
        total, rows = output.get("total_count"), output["data"]
        if (
            type(page) is not int
            or page < 1
            or type(total) is not int
            or total < len(rows)
            or type(args.get("offset", 0)) is not int
            or args.get("offset", 0) != 0
        ):
            continue
        if (
            page == 1
            and output.get("has_more") is False
            and total == len(rows)
            and output.get("complete") is not False
        ):
            complete[ref] = record
            continue
        ordering = native.get("orderBy")
        if isinstance(ordering, list) and all(
            isinstance(term, Mapping) for term in ordering
        ):
            # graphql_ordering.create_ordering_types defines ASC for omission.
            # Explicit null/other values remain unchanged and cannot become ASC.
            ordering = [
                {**term, "direction": term.get("direction", "ASC")} for term in ordering
            ]
        limit = args.get("limit")
        if (
            type(size) is not int
            or size < 1
            or total <= size
            or (limit is not None and (type(limit) is not int or limit < size))
            or not isinstance(ordering, list)
            or not ordering
            or not all(isinstance(term, Mapping) for term in ordering)
            or not any(
                term.get("field") == "id" and term.get("direction") in {"ASC", "DESC"}
                for term in ordering
            )
        ):
            continue
        # Preserve every other argument, including filters, order and projection.
        signature = {
            **args,
            "arguments": {
                **{k: v for k, v in native.items() if k != "page"},
                "orderBy": ordering,
            },
        }
        groups.setdefault(_query_signature_digest(signature), []).append((ref, record))
    for group in groups.values():
        first = group[0][1]["root_query"]
        size = first["arguments"]["arguments"]["pageSize"]
        total = first.get("output", first.get("result"))["total_count"]
        pages: set[int] = set()
        identities: set[str] = set()
        valid = True
        for _, record in group:
            call = record["root_query"]
            output = call.get("output", call.get("result"))
            page = call["arguments"]["arguments"].get("page", 1)
            offset = (page - 1) * size
            rows = output["data"]
            ids = [_identifier_key(row.get("id")) for row in rows]
            if (
                page in pages
                or output["total_count"] != total
                or offset >= total
                or len(rows) != min(size, total - offset)
                or output.get("has_more") is not (offset + len(rows) < total)
                or any(
                    identifier is None or identifier in identities for identifier in ids
                )
                or len(set(ids)) != len(ids)
            ):
                valid = False
                break
            pages.add(page)
            identities.update(
                identifier for identifier in ids if identifier is not None
            )
        count = (total + size - 1) // size
        if valid and pages == set(range(1, count + 1)) and len(identities) == total:
            complete.update(group)
    return complete


def _selection_scope(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
    evidence: Mapping[str, dict[str, Any]],
    *,
    extracted: bool,
) -> dict[str, Any] | None:
    """Prove selected row scope without adding unspoken flags to answer facts."""
    contract = expectation.get("selection_scope_contract")
    if not contract:
        return None
    manager, result_field, claims_field = (
        contract["manager"],
        contract["result_field"],
        contract["claims_field"],
    )
    ids = facts.get(result_field) if facts else None
    claims = facts.get(claims_field) if facts else None
    required = expectation["facts"][claims_field]
    all_support = observation.get("fact_support")
    support = (
        all_support.get(result_field) if isinstance(all_support, Mapping) else None
    )
    refs = support.get("evidence_ids", []) if isinstance(support, Mapping) else []
    extraction = observation.get("extraction")
    extraction_refs = (
        extraction.get("evidence_ids", []) if isinstance(extraction, Mapping) else []
    )
    linked = (
        {
            ref: evidence[ref]
            for ref in refs
            if isinstance(ref, str)
            and ref in evidence
            and ref in extraction_refs
            and evidence[ref]["manager"] == manager
        }
        if extracted and isinstance(refs, list)
        else {}
    )
    linked = _complete_scope_queries(linked)
    returned, _ = _returned_identities(
        manager, expectation["entity_identity_reference"]["managers"][manager], linked
    )
    rows: list[Mapping[str, Any]] = []
    covered: set[str] = set()
    for record in linked.values():
        for row in record["identity_rows"]:
            code = row.get("code")
            if not isinstance(code, str):
                code = returned.get(_identifier_key(row.get("id")) or "", {}).get(
                    "canonical_code"
                )
            if isinstance(ids, list) and isinstance(code, str) and code in ids:
                covered.add(code)
                rows.append(row)
    complete = (
        isinstance(ids, list)
        and bool(ids)
        and all(isinstance(x, str) for x in ids)
        and len(ids) == len(set(ids))
        and set(ids) == covered
    )
    effective = {}
    for name, column in contract["row_fields"].items():
        values = [row.get(column) for row in rows]
        if values and all(_matches(values[0], value) for value in values):
            effective[name] = values[0]
    scope_passed = bool(extracted and complete and _matches(required, effective))
    contradictions = []
    if claims is not None and not isinstance(claims, Mapping):
        contradictions.append({"reason": "invalid_explicit_scope"})
    elif isinstance(claims, Mapping):
        for name, value in claims.items():
            column = contract["row_fields"].get(name, name)
            if not rows or any(
                column not in row or not _matches(value, row[column]) for row in rows
            ):
                contradictions.append(
                    {
                        "field": name,
                        "claimed": value,
                        "reason": "claim_not_supported_by_selected_rows",
                    }
                )
    return {
        "status": "pass" if scope_passed else "fail" if extracted else "unscored",
        "effective_constraints": effective,
        "explicit_constraints": deepcopy(claims),
        "explicit_claims_status": "fail"
        if contradictions
        else "pass"
        if extracted
        else "unscored",
        "contradictions": contradictions,
        "evidence_ids": sorted(linked),
        "selected_ids_proved": sorted(covered),
        "complete_selection": complete,
    }


def _physical_interface(value: Any) -> str | None:
    if isinstance(value, str):
        return value.removesuffix("Interface")
    if isinstance(value, Mapping):
        return _physical_interface(value.get("interface_type", value.get("interface")))
    return None


def make_judge_packet(
    expectation: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Blind semantic packet: actual answer/evidence, never model or old scores.

    Expected numeric facts are deliberately absent. Semantic instructions cover
    only residual judgment; numeric/ID truth stays with the deterministic oracle.
    """
    trace = observation.get("trace") or {}
    return {
        "schema_version": "1.1",
        "case_id": expectation["case_id"],
        "turn_index": expectation["turn_index"],
        "answer": observation.get("answer", ""),
        "answer_sha256": _digest(observation.get("answer", "")),
        "semantic_checks": expectation["semantic_checks"],
        "evidence": trace.get("evidence", []),
        "tool_calls": trace.get("tool_calls", []),
        "history_evidence": trace.get("history_evidence", []),
        "citations": observation.get("citations", []),
    }


def score_turn(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    registry: Mapping[str, Any] | None = None,
    semantic_judgment: Mapping[str, Any] | None = None,
    failure_flags: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Score one turn without a live judge, prose matching or inferred success.

    ``registry`` is captured from real runtime interfaces. ``trace`` is captured
    by the harness. ``extraction`` and ``semantic_judgment`` are independently
    validated external artifacts bound to this exact saved answer digest. The
    supplied reason/phase for external failures is retained in the result.
    """
    failures: list[dict[str, Any]] = []
    secondary: set[str] = set()
    for flag in failure_flags:
        if (
            not isinstance(flag, Mapping)
            or flag.get("category") not in FAILURE_HIERARCHY
            or not flag.get("reason")
        ):
            message = (
                "Failure flags need a known category and an evidence-backed reason"
            )
            raise ValueError(message)
        failures.append(dict(flag))
    checks: dict[str, list[dict[str, Any]]] = {key: [] for key in DIMENSIONS}
    evidence, evidence_errors = _evidence(observation, int(expectation["turn_index"]))
    for error in evidence_errors:
        failures.append(
            {
                "category": "harness_failure",
                "reason": error,
                "phase": "trace_validation",
            }
        )
    facts = observation.get("facts")
    extraction = observation.get("extraction")
    digest = _digest(observation.get("answer", ""))
    extracted = (
        isinstance(facts, Mapping)
        and isinstance(extraction, Mapping)
        and extraction.get("status") == "validated"
        and extraction.get("origin") == "external"
        and (
            expectation.get("adjudication_schema_version") is None
            or extraction.get("adjudication_schema_version")
            == expectation["adjudication_schema_version"]
        )
        and isinstance(extraction.get("validator"), str)
        and bool(extraction.get("validator"))
        and extraction.get("answer_sha256") == digest
        and isinstance(extraction.get("evidence_ids"), list)
        and all(identifier in evidence for identifier in extraction["evidence_ids"])
    )
    # A valid answer hash does not authenticate its accompanying evidence.
    # New internal-grounding contracts require both independently parsed records
    # to bind the exact trace. Legacy saved references without bindings remain
    # readable; whenever a binding is present, it must still match.
    binding_required = expectation.get("citation_policy") == "internal_grounding"
    extraction_binding = (
        extraction.get("evidence_trace_sha256")
        if isinstance(extraction, Mapping)
        else None
    )
    judgment_binding = (
        semantic_judgment.get("evidence_trace_sha256")
        if isinstance(semantic_judgment, Mapping)
        else None
    )
    trace_bound = True
    if (
        binding_required
        or extraction_binding is not None
        or judgment_binding is not None
    ):
        try:
            current_trace_digest = evidence_trace_digest(observation.get("trace") or {})
        except (TypeError, ValueError):
            current_trace_digest = None
        trace_bound = (
            current_trace_digest is not None
            and extraction_binding == current_trace_digest
            and judgment_binding == current_trace_digest
        )
        if not trace_bound:
            failures.append(
                {
                    "category": "judge_failure",
                    "reason": "Missing or stale evidence-content binding; revalidate the original judge request and response",
                    "phase": "evidence_binding",
                }
            )
    extracted = extracted and trace_bound
    if not extracted and any(
        expectation["dimensions"][dimension]["checks"] for dimension in DIMENSIONS
    ):
        failures.append(
            {
                "category": "judge_failure",
                "reason": "Missing, stale or invalid external fact extraction; deterministic answer correctness is unscored",
                "phase": "fact_extraction",
            }
        )
    # Only independently validated answer extraction may select an optional
    # clarification path. A raw/model-authored response_mode cannot waive data checks.
    if extracted and isinstance(facts, Mapping):
        response_mode = facts.get("response_mode")
        alternatives = expectation.get("response_alternatives", {})
        if isinstance(response_mode, str) and response_mode in alternatives:
            expectation = alternatives[response_mode]
    normalized_facts, identity_audit, uncertain_identities = _normalize_entity_facts(
        expectation,
        facts if isinstance(facts, Mapping) else None,
        evidence,
        extracted=extracted,
    )
    reference_managers = expectation.get("entity_identity_reference", {}).get(
        "managers", {}
    )
    normalized_facts, presentation_audit = normalize_presentations(
        expectation,
        normalized_facts,
        observation,
        evidence,
        semantic_judgment,
        extracted=extracted,
        resolve=lambda manager: _returned_identities(
            manager, reference_managers.get(manager, []), evidence
        )[0],
        matches=_matches,
    )
    designation_audit = _designation_audit(
        expectation, observation, normalized_facts, evidence, extracted=extracted
    )
    unit_audit = _unit_audit(expectation, normalized_facts)
    scope_audit = _selection_scope(
        expectation, observation, normalized_facts, evidence, extracted=extracted
    )
    if scope_audit is not None:
        checks["A"].append(
            _check(
                "explicit_selection_scope_claims",
                scope_audit["explicit_claims_status"],
                "Explicit claims are checked separately from effective tool-backed scope",
                contradictions=scope_audit["contradictions"],
            )
        )
    invalid_identities = {
        row["field"]
        for row in identity_audit["unresolved"]
        if row["status"] == "invalid"
    }
    for dimension in DIMENSIONS:
        for rule in expectation["dimensions"][dimension]["checks"]:
            field = rule["field"]
            if not extracted:
                checks[dimension].append(
                    _check(field, "unscored", "No validated fact extraction")
                )
                continue
            actual = (
                normalized_facts.get(field) if normalized_facts is not None else None
            )
            if (
                scope_audit is not None
                and field == expectation["selection_scope_contract"]["claims_field"]
            ):
                checks[dimension].append(
                    _check(
                        field,
                        scope_audit["status"],
                        "Effective selected scope proved from linked complete query rows; raw answer claims remain unchanged",
                        expected=rule["expected"],
                        observed=scope_audit["effective_constraints"],
                        raw_observed=actual,
                        evidence_ids=scope_audit["evidence_ids"],
                    )
                )
                continue
            if field == "unit":
                actual = unit_audit["normalized_observed"]
            if field == "context" and isinstance(actual, Mapping) and "unit" in actual:
                actual = {
                    **actual,
                    "unit": unit_audit["context"]["normalized_observed"],
                }
            if field == "designation":
                actual = designation_audit["normalized_observed"]
            if field in uncertain_identities:
                reference = expectation["entity_identity_reference"]
                descriptor = reference["fields"].get(field)
                contradictions = (
                    _known_identity_contradictions(
                        rule["expected"],
                        actual,
                        rule["comparison"],
                        {
                            row["code"]
                            for row in reference["managers"][descriptor["manager"]]
                        },
                    )
                    if descriptor
                    else []
                )
                for path_descriptor in reference.get("paths", []):
                    if path_descriptor["path"][0] != field:
                        continue
                    observed_path, expected_path = actual, rule["expected"]
                    for part in path_descriptor["path"][1:]:
                        observed_path = (
                            observed_path.get(part)
                            if isinstance(observed_path, Mapping)
                            else None
                        )
                        expected_path = (
                            expected_path.get(part)
                            if isinstance(expected_path, Mapping)
                            else None
                        )
                    codes = {
                        row["code"]
                        for row in reference["managers"][path_descriptor["manager"]]
                    }
                    if (
                        isinstance(observed_path, str)
                        and observed_path in codes
                        and not _matches(expected_path, observed_path)
                    ):
                        contradictions.append(
                            {
                                "reason": "wrong_canonical_identity_at_path",
                                "path": path_descriptor["path"],
                                "observed": observed_path,
                                "expected": expected_path,
                            }
                        )
                proven_failure = bool(contradictions) or field in invalid_identities
                checks[dimension].append(
                    _check(
                        field,
                        "fail" if proven_failure else "unscored",
                        "Proven identity contradiction; other identities remain unresolved"
                        if proven_failure
                        else "Entity identity lacks a unique eligible evidence mapping",
                        expected=rule["expected"],
                        observed=actual,
                        raw_observed=facts.get(field)
                        if isinstance(facts, Mapping)
                        else None,
                        identity_contradictions=contradictions,
                    )
                )
                # An unresolved ID remains a judge limitation even when a
                # separate known identity demonstrates a model task failure.
                failures.append(
                    {
                        "category": "judge_failure",
                        "reason": f"Entity identity for {field} cannot be resolved from the saved selected typed-row evidence",
                        "phase": "identity_normalization",
                    }
                )
                continue
            present = isinstance(facts, Mapping) and field in facts
            passed = present and _matches(rule["expected"], actual, rule["comparison"])
            checks[dimension].append(
                _check(
                    field,
                    "pass" if passed else "fail",
                    "Compared independently derived facts"
                    if present
                    else "Required fact missing from the answer",
                    expected=rule["expected"],
                    observed=actual,
                    raw_observed=facts.get(field)
                    if isinstance(facts, Mapping)
                    else None,
                )
            )
    support = observation.get("fact_support")
    if extracted and isinstance(support, Mapping):
        unsupported = sorted(
            name
            for name, item in support.items()
            if isinstance(item, Mapping) and item.get("status") == "unsupported"
        )
        if unsupported:
            checks["A"].append(
                _check(
                    "unsupported_answer_claims",
                    "fail",
                    "Independent extraction identifies claims unsupported by eligible evidence",
                    fields=unsupported,
                )
            )
    requirements = expectation["source_requirements"]
    trace = observation.get("trace") or {}
    complete_trace = trace.get("observation_complete") is True
    discovered = trace.get("discovery") or []
    managers = {row["manager"] for row in evidence.values()}
    reused_only = bool(evidence) and all(
        row["origin"] == "history" for row in evidence.values()
    )
    discovery_na = bool(requirements) and reused_only and not discovered
    if requirements and not discovery_na:
        if not discovered:
            checks["D"].append(
                _check(
                    "discovery_trace",
                    "fail" if complete_trace else "unscored",
                    "Complete trace contains no discovery"
                    if complete_trace
                    else "No normalized discovery observation; manager quality is not inferred",
                )
            )
        elif not isinstance(discovered, list) or not all(
            isinstance(record, dict)
            and isinstance(record.get("candidates"), list)
            and isinstance(record.get("selected"), list)
            and all(
                isinstance(name, str)
                for name in [*record["candidates"], *record["selected"]]
            )
            for record in discovered
        ):
            checks["D"].append(
                _check("discovery_trace", "unscored", "Malformed discovery observation")
            )
            failures.append(
                {
                    "category": "harness_failure",
                    "reason": "Malformed normalized discovery trace",
                    "phase": "discovery",
                }
            )
        else:
            candidates = {
                name for record in discovered for name in record["candidates"]
            }
            selected = {name for record in discovered for name in record["selected"]}
            for requirement in requirements:
                for field, observed in (
                    ("candidates", candidates),
                    ("selected", selected),
                ):
                    expanded = observed | {
                        entry["manager"]
                        for entry in evidence.values()
                        if entry["root_manager"] in observed
                    }
                    passed = _sources_satisfied(requirement, expanded)
                    checks["D"].append(
                        _check(
                            f"{requirement['role']}.{field}",
                            "pass" if passed else "fail",
                            "Accept any supported source path",
                            alternatives=requirement["alternatives"],
                            observed=sorted(observed),
                        )
                    )
            ranks = {
                name: min(
                    record["candidates"].index(name) + 1
                    for record in discovered
                    if name in record["candidates"]
                )
                for name in candidates
            }
            checks["D"].append(
                _check(
                    "candidate_ranks",
                    "pass",
                    "Rank telemetry; no undisclosed top-k cutoff",
                    ranks=ranks,
                    available_at_k={
                        str(k): sorted(
                            name for name, rank in ranks.items() if rank <= k
                        )
                        for k in (1, 3, 5, 10)
                    },
                )
            )
    if requirements:
        if not evidence:
            checks["I"].append(
                _check(
                    "physical_sources",
                    "fail" if complete_trace else "unscored",
                    "No linked tool or history evidence",
                )
            )
        else:
            for requirement in requirements:
                passed = _sources_satisfied(
                    requirement, managers, _evidence_fields(list(evidence.values()))
                )
                checks["I"].append(
                    _check(
                        requirement["role"],
                        "pass" if passed else "fail",
                        "Physical source managers from linked trace evidence",
                        alternatives=requirement["alternatives"],
                        observed=sorted(managers),
                    )
                )
            for manager in sorted(managers):
                expected_interface = expectation["manager_interfaces"].get(manager)
                if expected_interface is None:
                    continue
                actual_interface = _physical_interface((registry or {}).get(manager))
                if actual_interface is None:
                    checks["I"].append(
                        _check(
                            f"{manager}.interface",
                            "unscored",
                            "Runtime registry did not supply the physical interface",
                        )
                    )
                else:
                    checks["I"].append(
                        _check(
                            f"{manager}.interface",
                            "pass"
                            if actual_interface == expected_interface
                            else "fail",
                            "Compared runtime registry interface, never answer prose",
                            expected=expected_interface,
                            observed=actual_interface,
                        )
                    )
    citations = observation.get("citations")
    citation_ids = citations if isinstance(citations, list) else []
    citation_policy = expectation.get("citation_policy", "visible_required")
    if citation_policy not in {"internal_grounding", "visible_required"}:
        message = "Unknown citation policy"
        raise ValueError(message)
    if requirements and citation_policy == "internal_grounding":
        checks["A"].append(
            _internal_grounding_check(
                expectation, observation, evidence, extracted, semantic_judgment
            )
        )
    if requirements and citation_policy == "visible_required":
        if citations is None and not extracted:
            checks["A"].append(
                _check(
                    "evidence_references",
                    "unscored",
                    "No validated citation extraction",
                )
            )
        else:
            cited_ok = (
                isinstance(citations, list)
                and bool(citations)
                and all(
                    isinstance(identifier, str) and identifier in evidence
                    for identifier in citations
                )
            )
            cited_managers = (
                {evidence[identifier]["manager"] for identifier in citation_ids}
                if cited_ok
                else set()
            )
            cited_fields = (
                _evidence_fields([evidence[identifier] for identifier in citation_ids])
                if cited_ok
                else {}
            )
            covered = cited_ok and all(
                _sources_satisfied(requirement, cited_managers, cited_fields)
                for requirement in requirements
            )
            checks["A"].append(
                _check(
                    "evidence_references",
                    "pass" if covered else "fail",
                    "Citations must exist and cover the claimed source roles",
                    observed=citations,
                )
            )
    judgment = semantic_judgment
    judge_header_valid = (
        trace_bound
        and isinstance(judgment, Mapping)
        and judgment.get("status") == "completed"
        and judgment.get("origin") == "external"
        and isinstance(judgment.get("judge_id"), str)
        and bool(judgment.get("judge_id"))
        and judgment.get("answer_sha256") == digest
        and isinstance(judgment.get("checks"), Mapping)
    )
    judge_checks = (
        judgment["checks"] if judge_header_valid and judgment is not None else {}
    )
    for required in expectation["semantic_checks"]:
        result = judge_checks.get(required["id"])
        valid = (
            isinstance(result, Mapping)
            and isinstance(result.get("passed"), bool)
            and isinstance(result.get("reason"), str)
            and bool(result.get("reason", "").strip())
            and isinstance(result.get("evidence_ids"), list)
            and bool(result.get("evidence_ids"))
            and all(
                identifier == "answer" or identifier in evidence
                for identifier in result["evidence_ids"]
            )
        )
        if required["id"] == "no_repeated_clarification":
            valid = valid and valid_repetition_judgment(
                result,
                observation.get("answer"),
                (observation.get("trace") or {}).get("conversation_context"),
            )
        if not valid or not isinstance(result, Mapping):
            checks[required["dimension"]].append(
                _check(
                    required["id"],
                    "unscored",
                    "Missing, stale or invalid independent semantic judgment",
                )
            )
            failures.append(
                {
                    "category": "judge_failure",
                    "reason": f"Semantic check {required['id']} lacks a valid independent judgment",
                    "phase": "semantic_judgment",
                }
            )
        else:
            checks[required["dimension"]].append(
                _check(
                    required["id"],
                    "pass" if result["passed"] else "fail",
                    result["reason"],
                    evidence_ids=result["evidence_ids"],
                )
            )
    for issue in expectation.get("reference_issues", []):
        failures.append(
            {
                "category": "judge_failure",
                "reason": issue.get("message", "Unresolved reference discrepancy"),
                "phase": "reference",
                "reference_id": issue.get("id"),
            }
        )
        secondary.add("reference_discrepancy")
    dimensions: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        applicable = expectation["dimensions"][dimension]["applicable"] and not (
            dimension == "D" and discovery_na
        )
        statuses = {check["status"] for check in checks[dimension]}
        if not applicable:
            status = "N/A"
        elif "fail" in statuses:
            status = "fail"
        elif "unscored" in statuses or not statuses:
            status = "unscored"
        else:
            status = "pass"
        dimensions[dimension] = {
            "status": status,
            "score": {"pass": 1, "fail": 0}.get(status),
            "checks": checks[dimension],
            "reason": "Evidence reused from a verified prior turn"
            if dimension == "D" and discovery_na
            else None,
        }
    if any(result["status"] == "fail" for result in dimensions.values()):
        failures.append(
            {
                "category": "model_task_failure",
                "reason": "One or more applicable dimensions have a demonstrated failure",
                "phase": "scoring",
            }
        )
    if any(
        result["status"] == "unscored" for result in dimensions.values()
    ) and not any(
        failure["category"] in FAILURE_HIERARCHY[:-1] for failure in failures
    ):
        failures.append(
            {
                "category": "judge_failure",
                "reason": "Required dimension observations are incomplete",
                "phase": "scoring",
            }
        )
    categories = {str(failure["category"]) for failure in failures}
    primary = next(
        (category for category in FAILURE_HIERARCHY if category in categories), "passed"
    )
    secondary.update(categories - {primary})
    return {
        "schema_version": "1.1",
        "case_id": expectation["case_id"],
        "turn_index": expectation["turn_index"],
        "dimensions": dimensions,
        "identity_normalization": identity_audit,
        "presentation_normalization": presentation_audit,
        "unit_normalization": unit_audit,
        "designation_normalization": designation_audit,
        "selection_scope": scope_audit,
        "evaluation_revision": "measurement-contracts-4",
        "reference_version": expectation.get("schema_version"),
        "citation_policy": citation_policy,
        "visible_citations": {
            "required": bool(requirements) and citation_policy == "visible_required",
            "status": "not_extracted"
            if citations is None
            else "present"
            if citation_ids
            else "absent",
            "evidence_ids": citation_ids,
        },
        "primary_failure": primary,
        "classification": primary,
        "secondary_flags": sorted(secondary),
        "failures": failures,
        "scored": all(
            result["status"] in {"pass", "fail", "N/A"}
            for result in dimensions.values()
        ),
        "passed": primary == "passed",
        "reference_issues": expectation.get("reference_issues", []),
        "reference_corrections": expectation.get("reference_corrections", []),
    }
