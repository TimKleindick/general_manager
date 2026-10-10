"""Narrow presentation projections, backed by existing selected query proofs.

Every remaining fact still receives the scorer's exact comparison. Unknown keys
and labels are retained, so they cannot disappear through subset matching.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from graphql import GraphQLError, parse_value, value_from_ast_untyped

from .evidence_rows import _schemas_before, _selections
from .query_completeness import complete_identity_queries

Resolver = Callable[[str], Mapping[str, Mapping[str, Any]]]
Matcher = Callable[[Any, Any], bool]


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _key(value: Any) -> str | None:
    return (
        str(value) if type(value) is int or (isinstance(value, str) and value) else None
    )


class _Evidence:
    def __init__(
        self,
        observation: Mapping[str, Any],
        evidence: Mapping[str, dict[str, Any]],
        resolve: Resolver,
    ) -> None:
        self.observation = observation
        self.evidence = evidence
        self.resolve = resolve
        self.calls = {
            record["root_query"]["id"]: record["root_query"]
            for record in evidence.values()
            if isinstance(record.get("root_query"), Mapping)
        }
        roles: dict[str, set[tuple[str | int, ...]]] = {}
        for record in evidence.values():
            for proof in record["identity_proofs"]:
                roles.setdefault(proof["query_call_id"], set()).add(
                    tuple(proof["row_path"])
                )
        self.complete = complete_identity_queries(self.calls, roles)
        self.schemas: dict[str, dict[str, Any] | None] = {}

    def root_schema(self, call: Mapping[str, Any]) -> dict[str, Any] | None:
        identifier = call["id"]
        if identifier not in self.schemas:
            calls = self.observation.get("trace", {}).get("tool_calls", [])
            bound = _schemas_before(call, calls).get(call["root_manager"])
            self.schemas[identifier] = bound[0] if bound is not None else None
        return self.schemas[identifier]

    def bound(self, field: str) -> list[dict[str, Any]]:
        support = self.observation.get("fact_support")
        item = support.get(field) if isinstance(support, Mapping) else None
        refs = item.get("evidence_ids") if isinstance(item, Mapping) else None
        quotes = item.get("answer_quotes") if isinstance(item, Mapping) else None
        extraction = self.observation.get("extraction", {})
        if not (
            isinstance(item, Mapping)
            and item.get("status") == "present"
            and isinstance(refs, list)
            and refs
            and all(
                isinstance(ref, str)
                and ref in self.evidence
                and ref in extraction.get("evidence_ids", [])
                for ref in refs
            )
            and len(set(refs)) == len(refs)
            and isinstance(quotes, list)
            and quotes
            and all(
                isinstance(quote, str)
                and quote.strip()
                and quote in self.observation.get("answer", "")
                for quote in quotes
            )
        ):
            return []
        return [self.evidence[ref] for ref in refs]

    def rows(
        self,
        records: list[dict[str, Any]],
        manager: str,
        *,
        root_only: bool = False,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        selected: dict[
            tuple[str, tuple[str | int, ...]], tuple[dict[str, Any], dict[str, Any]]
        ] = {}
        for record in records:
            if record["manager"] != manager:
                continue
            for row, proof in zip(
                record["identity_rows"], record["identity_proofs"], strict=True
            ):
                call_id, path = proof["query_call_id"], proof["row_path"]
                if call_id in self.complete and (
                    not root_only
                    or (
                        len(path) == 2
                        and self.calls[call_id]["root_manager"] == manager
                    )
                ):
                    selected[(call_id, tuple(path))] = (row, proof)
        return list(selected.values())

    def code(self, value: Any, manager: str) -> str | None:
        mappings = self.resolve(manager)
        key = _key(value)
        if key is not None and key in mappings:
            return str(mappings[key]["canonical_code"])
        if isinstance(value, str) and any(
            item["canonical_code"] == value for item in mappings.values()
        ):
            return value
        return None

    def related(
        self, manager: str, proof: Mapping[str, Any], relation: str
    ) -> list[dict[str, Any]]:
        return [
            row
            for row, child in self.rows(list(self.evidence.values()), manager)
            if child["query_call_id"] == proof["query_call_id"]
            and child["row_path"][:3] == [*proof["row_path"], relation]
        ]


def _empty_filter(value: Any) -> bool:
    return value is None or (isinstance(value, Mapping) and not value)


def _same_json(left: Any, right: Any) -> bool:
    """Captured JSON must preserve scalar types, especially bool versus numeric."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(value, other) for value, other in zip(left, right, strict=True)
        )
    return isinstance(left, (str, int, float, bool, type(None))) and bool(left == right)


def _valid_default(definition: Mapping[str, Any]) -> bool:
    """Native captures pair JSON defaults with their public GraphQL AST spelling."""
    if "default_graphql" not in definition:
        return True
    if "default" not in definition or not isinstance(
        definition["default_graphql"], str
    ):
        return False
    try:
        parsed = value_from_ast_untyped(parse_value(definition["default_graphql"]))
    except GraphQLError:
        return False
    return _same_json(definition["default"], parsed)


def _effective_filter(
    value: Any,
    signature: Any,
    schema: Mapping[str, Any],
    visited: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Expand only loaded input-field defaults; unavailable definitions prove nothing."""
    if not isinstance(value, Mapping) or not isinstance(signature, str):
        return None
    typename = signature.replace("!", "")
    definition = schema.get("types", {}).get(typename)
    fields = definition.get("fields") if isinstance(definition, Mapping) else None
    if (
        not isinstance(definition, Mapping)
        or definition.get("kind") != "input"
        or not isinstance(fields, Mapping)
        or typename in visited
        or not set(value) <= set(fields)
    ):
        return None
    effective = dict(value)
    for name, field in fields.items():
        if (
            not isinstance(field, Mapping)
            or not isinstance(field.get("type"), str)
            or not _valid_default(field)
        ):
            return None
        if name not in effective and "default" in field:
            effective[name] = deepcopy(field["default"])
        if name in effective and isinstance(effective[name], Mapping):
            nested = _effective_filter(
                effective[name], field["type"], schema, visited | {typename}
            )
            if nested is None:
                return None
            effective[name] = nested
    return effective


def _effective_arguments(
    field: Mapping[str, Any], explicit: Any, schema: Mapping[str, Any]
) -> dict[str, Any] | None:
    definitions = field.get("arguments", {})
    if (
        not isinstance(definitions, Mapping)
        or not isinstance(explicit, Mapping)
        or not set(explicit) <= set(definitions)
    ):
        return None
    effective = dict(explicit)
    for name, definition in definitions.items():
        if (
            not isinstance(definition, Mapping)
            or not isinstance(definition.get("type"), str)
            or not _valid_default(definition)
        ):
            return None
        if name not in effective and "default" in definition:
            effective[name] = deepcopy(definition["default"])
        if (
            name in {"filter", "exclude"}
            and name in effective
            and effective[name] is not None
        ):
            expanded = _effective_filter(effective[name], definition["type"], schema)
            if expanded is None:
                return None
            effective[name] = expanded
    return effective


def _unfiltered_relation(
    call: Mapping[str, Any], name: str, schema: Mapping[str, Any] | None
) -> bool:
    if schema is None:
        return False
    fields = call["arguments"]["fields"]
    chosen = _selections(fields, name)
    if len(chosen) != 1 or not isinstance(chosen[0], list):
        return False
    owner = schema.get("types", {}).get(schema.get("type"), {})
    declarations = schema.get("output_fields", owner.get("fields", {}))
    definition = declarations.get(name) if isinstance(declarations, Mapping) else None
    if not isinstance(definition, Mapping):
        return False
    arguments: Any = next(
        (
            field.get("arguments", {})
            for field in fields
            if isinstance(field, Mapping) and field.get("field") == name
        ),
        {},
    )
    effective = _effective_arguments(definition, arguments, schema)
    return (
        effective is not None
        and set(effective) <= {"page", "pageSize", "orderBy", "filter", "exclude"}
        and all(
            _empty_filter(effective.get(argument)) for argument in ("filter", "exclude")
        )
    )


def _exclusion_supported(
    actual: Mapping[str, Any], facts: Mapping[str, Any], sources: _Evidence
) -> bool:
    exclusions = actual.get("none_materials")
    if not isinstance(exclusions, list) or not exclusions:
        return False
    excluded = [sources.code(value, "Material") for value in exclusions]
    included = actual.get("all_materials")
    result_ids = facts.get("result_ids")
    if (
        any(code is None for code in excluded)
        or len(set(excluded)) != len(excluded)
        or not isinstance(included, list)
        or not all(isinstance(code, str) for code in included)
        or set(excluded) & set(included)
        or not isinstance(result_ids, list)
        or not all(isinstance(code, str) for code in result_ids)
    ):
        return False
    projects = sources.rows(sources.bound("constraints"), "Project", root_only=True)
    if not projects or len(projects) != len(result_ids):
        return False
    returned = []
    for project, proof in projects:
        code = sources.code(project.get("id"), "Project")
        materials = sources.related("Material", proof, "materialsList")
        call = sources.calls[proof["query_call_id"]]
        if (
            code is None
            or not materials
            or not _unfiltered_relation(
                call, "materialsList", sources.root_schema(call)
            )
        ):
            return False
        codes = [sources.code(material.get("id"), "Material") for material in materials]
        if (
            any(value is None for value in codes)
            or set(codes) & set(excluded)
            or not set(included) <= set(codes)
        ):
            return False
        returned.append(code)
    return len(set(returned)) == len(returned) and set(returned) == set(result_ids)


def _in_period(value: Any, period: Mapping[str, Any]) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(
            period["start"]
        ) <= date.fromisoformat(value) < date.fromisoformat(period["end_exclusive"])
    except (KeyError, TypeError, ValueError):
        return False


def _cost_supported(
    facts: Mapping[str, Any],
    expectation: Mapping[str, Any],
    sources: _Evidence,
    matches: Matcher,
) -> bool:
    rows = sources.rows(sources.bound("metric"), "ProjectCost", root_only=True)
    period, values = facts.get("period"), facts.get("values")
    descriptor = (
        expectation.get("entity_identity_reference", {})
        .get("fields", {})
        .get("values", {})
    )
    manager = descriptor.get("manager")
    if (
        not rows
        or not isinstance(period, Mapping)
        or not isinstance(values, Mapping)
        or manager not in {"Project", "WbsElement"}
        or not sources.bound("values")
    ):
        return False
    # An identity-only WBS record cannot supply an amount or total, even when
    # a ledger happens to appear under another fact's independent support.
    coordinates = {
        (proof["query_call_id"], tuple(proof["row_path"])) for _, proof in rows
    }
    for field in ("values", "total"):
        supported = sources.rows(sources.bound(field), "ProjectCost", root_only=True)
        if {
            (proof["query_call_id"], tuple(proof["row_path"])) for _, proof in supported
        } != coordinates:
            return False
    amounts: dict[str, Decimal] = {}
    identities: set[str] = set()
    constraints = facts.get("constraints", {})
    for row, proof in rows:
        if not _cost_scope(sources.calls[proof["query_call_id"]], facts, sources):
            return False
        amount, identifier = _number(row.get("netAmount")), _key(row.get("id"))
        if (
            amount is None
            or identifier is None
            or identifier in identities
            or row.get("kind") != "actual"
            or row.get("currency") != facts.get("unit")
            or not _in_period(row.get("costDate"), period)
        ):
            return False
        identities.add(identifier)
        if manager == "WbsElement":
            related = sources.related("WbsElement", proof, "wbs")
            if len(related) != 1 or _key(related[0].get("id")) != _key(
                row.get("wbsId")
            ):
                return False
            code = sources.code(row.get("wbsId"), manager)
        else:
            code = sources.code(row.get("projectId"), manager)
        if code is None:
            return False
        if isinstance(constraints, Mapping) and "project" in constraints:
            project = sources.code(row.get("projectId"), "Project")
            if project is None or project != constraints["project"]:
                return False
        amounts[code] = amounts.get(code, Decimal(0)) + amount
    return matches(dict(values), amounts) and matches(
        facts.get("total"), sum(amounts.values(), Decimal(0))
    )


def _collection_arguments(
    call: Mapping[str, Any], sources: _Evidence
) -> dict[str, Any] | None:
    """Bind all effective native arguments before accepting collection scope."""
    schema = sources.root_schema(call)
    roots = schema.get("root_fields") if schema is not None else None
    if not isinstance(roots, Mapping) or len(roots) != 1 or schema is None:
        return None
    root = next(iter(roots.values()))
    if not isinstance(root, Mapping):
        return None
    effective = _effective_arguments(
        root, call["arguments"].get("arguments", {}), schema
    )
    if (
        effective is None
        or not set(effective) <= {"page", "pageSize", "orderBy", "filter", "exclude"}
        or not _empty_filter(effective.get("exclude"))
    ):
        return None
    return effective


def _cost_scope(
    call: Mapping[str, Any], facts: Mapping[str, Any], sources: _Evidence
) -> bool:
    """A complete filtered collection still needs proof of the full requested scope."""
    effective = _collection_arguments(call, sources)
    if effective is None:
        return False
    filters, period = effective.get("filter"), facts.get("period")
    if (
        not isinstance(filters, Mapping)
        or not isinstance(period, Mapping)
        or not set(filters)
        <= {
            "costDate_Gte",
            "costDate_Lt",
            "currency_Exact",
            "kind_Exact",
            "projectId_Exact",
            "project",
        }
        or filters.get("costDate_Gte") != period.get("start")
        or filters.get("costDate_Lt") != period.get("end_exclusive")
        or filters.get("kind_Exact") != "actual"
        or (
            "currency_Exact" in filters
            and filters["currency_Exact"] != facts.get("unit")
        )
    ):
        return False
    constraints = facts.get("constraints", {})
    requested_project = (
        constraints.get("project") if isinstance(constraints, Mapping) else None
    )
    supplied = "projectId_Exact" in filters or "project" in filters
    if requested_project is None:
        return not supplied
    if (
        not isinstance(requested_project, str)
        or not supplied
        or ("projectId_Exact" in filters and "project" in filters)
    ):
        return False
    identifier = filters.get("projectId_Exact")
    if "project" in filters:
        project = filters["project"]
        if not isinstance(project, Mapping) or set(project) != {"id_Exact"}:
            return False
        identifier = project["id_Exact"]
    resolved_project = sources.code(identifier, "Project")
    return resolved_project is not None and resolved_project == requested_project


def _shipment_history(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    forecast: Mapping[str, Any],
    facts: Mapping[str, Any],
    sources: _Evidence,
    matches: Matcher,
) -> bool:
    training = forecast.get("training_years")
    if (
        not isinstance(training, list)
        or not training
        or not all(type(year) is int for year in training)
    ):
        return False
    history: dict[str, Decimal] = {}
    for row, proof in rows:
        quantity = _number(row.get("quantity"))
        try:
            year = date.fromisoformat(row["shippedAt"]).year
        except (KeyError, TypeError, ValueError):
            return False
        call = sources.calls[proof["query_call_id"]]
        effective = _collection_arguments(call, sources)
        filters = effective.get("filter", {}) if effective is not None else {}
        customer = filters.get("project", {}) if isinstance(filters, Mapping) else {}
        customer_code = (
            sources.code(customer.get("customerId_Exact"), "Customer")
            if isinstance(customer, Mapping)
            else None
        )
        if (
            quantity is None
            or year not in training
            or row.get("source") != "actual"
            or row.get("unit") != forecast.get("unit")
            or customer_code is None
            or facts.get("customer_ids") != [customer_code]
            or not isinstance(filters, Mapping)
            or set(filters) != {"project", "shippedAt_Gte", "shippedAt_Lt"}
            or not isinstance(customer, Mapping)
            or set(customer) != {"customerId_Exact"}
            or filters.get("shippedAt_Gte") != f"{min(training)}-01-01"
            or filters.get("shippedAt_Lt") != f"{max(training) + 1}-01-01"
            or effective is None
        ):
            return False
        history[str(year)] = history.get(str(year), Decimal(0)) + quantity
    return matches(forecast.get("actuals"), history)


def _forecast_supported(
    forecast: Mapping[str, Any],
    facts: Mapping[str, Any],
    sources: _Evidence,
    matches: Matcher,
) -> bool:
    records = sources.bound("forecast")
    outlook = sources.rows(records, "CustomerOutlook", root_only=True)
    shipments = sources.rows(records, "Shipment", root_only=True)
    if not outlook and not shipments:
        return False
    if shipments and not _shipment_history(
        shipments, forecast, facts, sources, matches
    ):
        return False
    if outlook:
        values, scenario = forecast.get("values"), forecast.get("scenario")
        if (
            not isinstance(values, Mapping)
            or not isinstance(scenario, Mapping)
            or len(outlook) != len(values)
        ):
            return False
        years: set[str] = set()
        for row, proof in outlook:
            year = str(row.get("year"))
            related = sources.related("Customer", proof, "customer")
            code = (
                sources.code(related[0].get("id"), "Customer")
                if len(related) == 1
                else None
            )
            if (
                type(row.get("year")) is not int
                or year in years
                or year not in values
                or row.get("historyComplete") is not True
                or code is None
                or facts.get("customer_ids") != [code]
                or not matches(values[year], row.get("forecast"))
                or not matches(
                    scenario.get(year),
                    [row.get("scenarioLow"), row.get("scenarioHigh")],
                )
            ):
                return False
            years.add(year)
    return True


def normalize_presentations(
    expectation: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
    observation: Mapping[str, Any],
    evidence: Mapping[str, dict[str, Any]],
    judgment: Mapping[str, Any] | None,
    *,
    extracted: bool,
    resolve: Resolver,
    matches: Matcher,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Project only enumerated, truthful differences; preserve raw extraction."""
    normalized = deepcopy(dict(facts)) if facts is not None else None
    audit: dict[str, Any] = {"version": "evidence-bound-presentations-1", "changes": []}
    if not extracted or normalized is None:
        return normalized, audit
    targets = expectation.get("facts", {})
    metric = normalized.get("metric")
    metric_candidate = (
        targets.get("metric") == "actual_net_project_cost"
        and isinstance(metric, str)
        and metric in {"actual_net_cost", "actual_net_costs"}
    )
    constraints, constraint_target = (
        normalized.get("constraints"),
        targets.get("constraints"),
    )
    exclusion_candidate = (
        isinstance(constraints, Mapping)
        and isinstance(constraint_target, Mapping)
        and "all_materials" in constraint_target
        and set(constraints) == set(constraint_target) | {"none_materials"}
        and "none_materials" not in constraint_target
    )
    forecast, forecast_target = normalized.get("forecast"), targets.get("forecast")
    forecast_candidate = (
        isinstance(forecast, Mapping)
        and isinstance(forecast_target, Mapping)
        and forecast_target.get("status") == "available"
        and forecast_target.get("method") == "OLS"
        and (
            forecast.get("status") == "complete"
            or forecast.get("scenario_kind") == "sensitivity"
            or ("total" in forecast and "total" not in forecast_target)
            or (
                "nonnegative_clamp" in forecast
                and "nonnegative_clamp" not in forecast_target
            )
        )
    )
    if not (metric_candidate or exclusion_candidate or forecast_candidate):
        return normalized, audit
    sources = _Evidence(observation, evidence, resolve)
    if metric_candidate and _cost_supported(normalized, expectation, sources, matches):
        normalized["metric"] = "actual_net_project_cost"
        audit["changes"].append(
            {
                "field": "metric",
                "raw_observed": metric,
                "normalized_observed": normalized["metric"],
            }
        )
    if (
        exclusion_candidate
        and isinstance(constraints, Mapping)
        and _exclusion_supported(constraints, normalized, sources)
    ):
        normalized["constraints"] = {
            key: value for key, value in constraints.items() if key != "none_materials"
        }
        audit["changes"].append(
            {
                "field": "constraints",
                "raw_observed": deepcopy(constraints),
                "normalized_observed": deepcopy(normalized["constraints"]),
                "supported_extra_claims": {
                    "none_materials": deepcopy(constraints["none_materials"])
                },
            }
        )
    if (
        forecast_candidate
        and isinstance(forecast, Mapping)
        and isinstance(forecast_target, Mapping)
        and _forecast_supported(forecast, normalized, sources, matches)
    ):
        projected = dict(forecast)
        if projected.get("status") == "complete":
            projected["status"] = "available"
        checks = (judgment or {}).get("checks")
        semantic = (
            checks.get("scenario_not_confidence")
            if isinstance(checks, Mapping)
            else None
        )
        if (
            projected.get("scenario_kind") == "sensitivity"
            and forecast_target.get("scenario_kind") == "sensitivity_not_confidence"
            and isinstance(semantic, Mapping)
            and semantic.get("passed") is True
        ):
            projected["scenario_kind"] = "sensitivity_not_confidence"
        values = projected.get("values")
        numbers = (
            [_number(value) for value in values.values()]
            if isinstance(values, Mapping)
            else []
        )
        if numbers and all(value is not None for value in numbers):
            total = sum((value for value in numbers if value is not None), Decimal(0))
            if (
                "total" not in forecast_target
                and "total" in projected
                and matches(projected["total"], total)
            ):
                projected.pop("total")
            if (
                "nonnegative_clamp" not in forecast_target
                and projected.get("nonnegative_clamp") is True
                and all(value is not None and value >= 0 for value in numbers)
            ):
                projected.pop("nonnegative_clamp")
        if projected != forecast:
            normalized["forecast"] = projected
            audit["changes"].append(
                {
                    "field": "forecast",
                    "raw_observed": deepcopy(forecast),
                    "normalized_observed": deepcopy(projected),
                }
            )
    return normalized, audit
