"""Independent source-row oracles, never imports a GM calculation implementation.

Contracts describe standardized facts for an external answer/evidence extractor.
They are not prompts for the tested model and never imply that a model answered
correctly. Monetary arithmetic uses Decimal, with rounding only at the end.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any

from .catalog import CONTRACTS, DIMENSIONS, REFERENCE_VERSION, reference_corrections
from .seeds import FROZEN_CLOCK, build_seed
from .semantic_contracts import (
    ADJUDICATION_SCHEMA_VERSION,
    RANKING_KNOWN_VALUES_ONLY,
)

Seed = dict[str, list[dict[str, Any]]]
FULL_YEARS = (2023, 2024, 2025)
FUTURE_YEARS = (2027, 2028, 2029, 2030, 2031)
MANAGER_INTERFACES = {
    "Customer": "Database",
    "Project": "Database",
    "Shipment": "Database",
    "Material": "ReadOnly",
    "ProjectCommercial": "Calculation",
    "ShipmentPlan": "ReadOnly",
    "CustomerOutlook": "Calculation",
    "Part": "Database",
    "ProjectPart": "Database",
    "Supplier": "Database",
    "ShipmentReturn": "Database",
    "ProjectDependency": "Database",
    "ProjectCost": "Database",
    "WbsElement": "ReadOnly",
}


def _decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        message = "Oracle numbers must be finite"
        raise ValueError(message)
    return result


def _number(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rounded(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def linear_forecast(
    actuals: Mapping[int, Any], years: tuple[int, ...] = FUTURE_YEARS
) -> dict[str, Any]:
    """OLS from three complete years, no imputation; ±20% is a scenario band."""
    missing = [
        year for year in FULL_YEARS if year not in actuals or actuals[year] is None
    ]
    history = {
        str(year): None if year in missing else _number(_decimal(actuals[year]))
        for year in FULL_YEARS
    }
    common: dict[str, Any] = {
        "method": "OLS",
        "training_years": list(FULL_YEARS),
        "actuals": history,
        "unit": "pieces",
        "scenario_kind": "sensitivity_not_confidence",
        "scenario_fraction": "0.20",
        "missing_years": missing,
    }
    if missing:
        return {
            **common,
            "status": "insufficient_history",
            "slope": None,
            "intercept": None,
            "values": None,
            "scenario": None,
        }
    x = [Decimal(index) for index in range(3)]
    y = [_decimal(actuals[year]) for year in FULL_YEARS]
    mean_x, mean_y = sum(x) / Decimal(3), sum(y) / Decimal(3)
    slope = sum(
        ((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)), Decimal(0)
    ) / sum(((a - mean_x) * (a - mean_x) for a in x), Decimal(0))
    intercept = mean_y - slope * mean_x
    raw = {
        str(year): max(Decimal(0), intercept + slope * Decimal(year - FULL_YEARS[0]))
        for year in years
    }
    return {
        **common,
        "status": "available",
        "slope": _number(slope),
        "intercept": _number(intercept),
        "values": {year: _rounded(value) for year, value in raw.items()},
        "scenario": {
            year: [_rounded(value * Decimal("0.8")), _rounded(value * Decimal("1.2"))]
            for year, value in raw.items()
        },
    }


def _index(data: Seed, table: str) -> dict[str, dict[str, Any]]:
    rows = data[table]
    indexed = {str(row["code"]): row for row in rows}
    if len(indexed) != len(rows):
        message = f"Duplicate codes in seed table {table}"
        raise ValueError(message)
    return indexed


def _resolve(data: Seed, table: str, term: str) -> str:
    matches = [
        str(row["code"])
        for row in data[table]
        if term.casefold()
        in {
            str(value).casefold()
            for value in [row["code"], row["name"], *row.get("aliases", [])]
        }
    ]
    if len(matches) != 1:
        message = f"Seed reference {table}/{term} is not unique"
        raise ValueError(message)
    return matches[0]


def _projects(
    data: Seed, *, customer: str | None = None, materials: set[str] | None = None
) -> list[str]:
    return sorted(
        str(row["code"])
        for row in data["project"]
        if (customer is None or row["customer_code"] == customer)
        and (materials is None or materials <= set(row["material_codes"]))
    )


def _shipments(data: Seed, projects: list[str], year: int) -> list[dict[str, Any]]:
    return [
        row
        for row in data["shipment"]
        if row["project_code"] in projects
        and f"{year}-01-01" <= row["shipped_at"] < f"{year + 1}-01-01"
        and row["source"] == "actual"
        and row["unit"] == "pieces"
    ]


def _quantity(rows: list[dict[str, Any]]) -> Decimal:
    return sum((_decimal(row["quantity"]) for row in rows), Decimal(0))


def _history(data: Seed, customer: str) -> dict[int, str | None]:
    projects = _projects(data, customer=customer)
    result: dict[int, str | None] = {}
    for year in FULL_YEARS:
        rows = _shipments(data, projects, year)
        result[year] = _number(_quantity(rows)) if rows else None
    return result


def _plan(data: Seed, customer: str) -> dict[str, str | None]:
    projects = _projects(data, customer=customer)
    result: dict[str, str | None] = {}
    for year in FUTURE_YEARS:
        rows = [
            row
            for row in data["shipment_plan"]
            if row["project_code"] in projects
            and row["year"] == year
            and row["source"] == "existing_plan"
        ]
        result[str(year)] = _number(_quantity(rows)) if rows else None
    return result


def _revenues(
    data: Seed,
) -> tuple[dict[str, Decimal | None], dict[str, dict[str, Any]]]:
    revenues: dict[str, Decimal | None] = {}
    conversions: dict[str, dict[str, Any]] = {}
    for row in data["project"]:
        code = str(row["code"])
        if (
            row["plan_year"] != 2027
            or row["plan_units"] is None
            or row["net_price"] is None
        ):
            revenues[code] = None
            continue
        native = _decimal(row["plan_units"]) * _decimal(row["net_price"])
        rate = Decimal(1)
        if row["currency"] != "EUR":
            rates = [
                record
                for record in data["exchange_rate"]
                if record["base_currency"] == row["currency"]
                and record["quote_currency"] == "EUR"
                and record["as_of"] == "2026-10-03"
            ]
            if len(rates) != 1:
                message = f"Missing or ambiguous fixed exchange rate for {row['currency']}/EUR"
                raise ValueError(message)
            record = rates[0]
            rate = _decimal(record["rate"])
            conversions[code] = {
                "native_amount": _rounded(native),
                "native_currency": row["currency"],
                "quote_currency": "EUR",
                "rate": _number(rate),
                "as_of": record["as_of"],
                "source": record["source"],
            }
        revenues[code] = (native * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return revenues, conversions


def _citation_policy(case: Mapping[str, Any], turn: int) -> str:
    """Carry explicit source requests forward, without reading future turns."""
    text = " ".join(case["turns"][: turn + 1])
    explicit = case["contract"] == "provenance" or re.search(
        r"\b(?:quellen?|sources?|citations?|supporting customer evidence)\b",
        text,
        re.IGNORECASE,
    )
    return "visible_required" if explicit else "internal_grounding"


class _Contract:
    def __init__(self, case: dict[str, Any], turn: int, data: Seed) -> None:
        self.value: dict[str, Any] = {
            "schema_version": REFERENCE_VERSION,
            "adjudication_schema_version": ADJUDICATION_SCHEMA_VERSION,
            "case_id": case["id"],
            "core_id": case["core_id"],
            "turn_index": turn,
            "contract": case["contract"],
            "snapshot": case["snapshot"],
            "fixture_variant": case["fixture_variant"],
            "clock": FROZEN_CLOCK,
            "answer_kind": "answer",
            "contract_coverage": "deterministic_and_semantic",
            "facts": {},
            # Evaluator-only identity references use the complete source tables,
            # never the expected-result subset or assumed database row order.
            "entity_identity_reference": {
                "schema_version": "1.0",
                "scope": "verified_root_query_rows",
                "fields": {},
                "managers": {
                    manager: [
                        {"code": str(row["code"]), "name": row.get("name")}
                        for row in data[table]
                    ]
                    for manager, table in {
                        "Customer": "customer",
                        "Project": "project",
                        "Material": "material",
                        "Supplier": "supplier",
                        "WbsElement": "wbs_element",
                    }.items()
                },
            },
            "dimensions": {
                key: {"applicable": False, "checks": []} for key in DIMENSIONS
            },
            "source_requirements": [],
            "citation_policy": _citation_policy(case, turn),
            "unit_normalization": "unit-labels-v2",
            "manager_interfaces": dict(MANAGER_INTERFACES),
            "semantic_checks": [],
            "reference_issues": [],
            "reference_corrections": [
                row for row in reference_corrections() if row["case_id"] == case["id"]
            ],
        }

    def fact(
        self,
        dimension: str,
        key: str,
        value: Any,
        comparison: str = "exact",
        *,
        entity_manager: str | None = None,
    ) -> None:
        self.value["facts"][key] = value
        identities = self.value["entity_identity_reference"]
        identities["fields"].pop(key, None)
        if entity_manager is not None:
            if entity_manager not in identities["managers"] or not isinstance(
                value, (list, dict)
            ):
                message = "Entity facts need a declared manager and list/map shape"
                raise ValueError(message)
            identities["fields"][key] = {
                "manager": entity_manager,
                "shape": "mapping_keys" if isinstance(value, dict) else "list",
            }
        self.value["dimensions"][dimension]["applicable"] = True
        rules = self.value["dimensions"][dimension]["checks"]
        rules[:] = [rule for rule in rules if rule["field"] != key]
        rules.append({"field": key, "expected": value, "comparison": comparison})

    def semantic(self, dimension: str, identifier: str, instruction: str) -> None:
        self.value["dimensions"][dimension]["applicable"] = True
        self.value["semantic_checks"].append(
            {"id": identifier, "dimension": dimension, "instruction": instruction}
        )

    def source(
        self,
        role: str,
        *alternatives: list[str],
        required_fields: dict[str, list[str]] | None = None,
    ) -> None:
        self.value["source_requirements"].append(
            {
                "role": role,
                "alternatives": list(alternatives),
                "required_fields": required_fields or {},
            }
        )
        self.value["dimensions"]["D"]["applicable"] = True
        self.value["dimensions"]["I"]["applicable"] = True

    def selection(
        self, identifiers: list[str], *, manager: str, **constraints: Any
    ) -> None:
        self.fact("R", "result_ids", identifiers, "set", entity_manager=manager)
        if constraints:
            self.fact("R", "constraints", constraints)
            # Paths describe identity semantics, never the expected identity value.
            for name, entity in {
                "project": "Project",
                "owner_customer": "Customer",
                "bill_to_customer": "Customer",
                "exclude_project": "Project",
                "reachable_from": "Project",
            }.items():
                if name in constraints:
                    self.value["entity_identity_reference"].setdefault(
                        "paths", []
                    ).append(
                        {
                            "path": ["constraints", name],
                            "manager": entity,
                            "shape": "scalar",
                        }
                    )

            for name in ("all_materials", "any_materials", "excluded_materials"):
                if name in constraints:
                    self.value["entity_identity_reference"].setdefault(
                        "paths", []
                    ).append(
                        {
                            "path": ["constraints", name],
                            "manager": "Material",
                            "shape": "list",
                        }
                    )

    def measure(
        self, metric: str, unit: str, years: list[int], sources: list[str]
    ) -> None:
        self.fact("C", "metric", metric)
        self.fact("C", "unit", unit)
        self.fact("C", "years", years)
        self.fact("I", "source_kinds", sources, "set")


def _forecast_contract(contract: _Contract, data: Seed, customer: str) -> None:
    contract.source("actuals_or_derived_forecast", ["CustomerOutlook"], ["Shipment"])
    contract.fact("R", "customer_ids", [customer], "set", entity_manager="Customer")
    contract.measure(
        "shipped_quantity", "pieces", list(FUTURE_YEARS), ["actual", "new_forecast"]
    )
    forecast = linear_forecast(_history(data, customer))
    contract.fact("C", "forecast", forecast)
    contract.semantic(
        "A",
        "forecast_assumptions",
        "Explain linear continuation of complete 2023-2025 actuals, stable definitions and units, no external demand/capacity/price assumption; 2026 YTD is excluded.",
    )
    if forecast["status"] == "available":
        contract.semantic(
            "A",
            "scenario_not_confidence",
            "Label ±20% as a sensitivity scenario, not a statistical confidence interval or causal promise.",
        )
    else:
        contract.semantic(
            "A",
            "insufficient_history_disclosed",
            "State that the missing year prevents the agreed three-point forecast; do not impute zero or use plans as actuals.",
        )


def _recommendation(contract: _Contract, data: Seed) -> None:
    contract.source("customer_forecasts", ["CustomerOutlook"], ["Shipment"])
    contract.source(
        "existing_customer_plans",
        ["ShipmentPlan"],
        ["CustomerOutlook"],
        required_fields={
            "ShipmentPlan": ["quantity"],
            "CustomerOutlook": ["existing_plan"],
        },
    )
    gaps: dict[str, str | None] = {}
    evidence: dict[str, Any] = {}
    for row in data["customer"]:
        customer = str(row["code"])
        forecast = linear_forecast(_history(data, customer))
        plans = _plan(data, customer)
        value = forecast["values"]["2031"] if forecast["values"] is not None else None
        gap = (
            None
            if value is None or plans["2031"] is None
            else _rounded(_decimal(value) - _decimal(plans["2031"]))
        )
        gaps[customer] = gap
        evidence[customer] = {
            "actuals": forecast["actuals"],
            "forecast_2031": value,
            "plan_2031": plans["2031"],
            "gap": gap,
        }
    ranked = sorted(
        (
            customer
            for customer, value in gaps.items()
            if value is not None and _decimal(value) > 0
        ),
        key=lambda customer: (-_decimal(gaps[customer]), customer),
    )
    contract.fact("R", "recommendation_ids", ranked, "set", entity_manager="Customer")
    contract.fact("C", "ranked_ids", ranked, entity_manager="Customer")
    contract.fact("C", "gaps", gaps, entity_manager="Customer")
    contract.fact("C", "customer_evidence", evidence, entity_manager="Customer")
    contract.fact(
        "C", "recommendation_criterion", "positive_2031_forecast_minus_existing_plan"
    )
    contract.semantic(
        "A",
        "recommendation_supported",
        "Explain the positive 2031 forecast-minus-plan criterion, justify order with evidence for C01/C02/C03 and mark contact ideas as suggestions with uncertainty; invent no probabilities or causal promise.",
    )


def expected_turn(
    case: dict[str, Any], turn_index: int, seed_data: Seed | None = None
) -> dict[str, Any]:
    """Return an explicit, JSON-safe contract for one zero-based catalog turn.

    Optional seed data supports independent fixture validation and counterfactual
    tests. Source calculation implementations and model answers are never read.
    E097's first turn intentionally has only the safety/evidence obligation
    specified by the reference; it does not invent a numeric revenue oracle.
    """
    if not 0 <= turn_index < len(case["turns"]):
        raise IndexError(turn_index)
    kind = str(case["contract"])
    if kind not in CONTRACTS:
        message = f"Unknown oracle contract: {kind}"
        raise ValueError(message)
    data = (
        build_seed(case["snapshot"], case["fixture_variant"])
        if seed_data is None
        else seed_data
    )
    projects = _index(data, "project")
    _index(data, "customer")
    _index(data, "material")
    customer = _resolve(data, "customer", "XYZ")
    copper = _resolve(data, "material", "Copper")
    aurora = _resolve(data, "project", "Aurora")
    contract = _Contract(case, turn_index, data)
    contract.semantic(
        "A",
        "answer_supported",
        "The answer addresses this turn using available evidence, includes the requested results and does not invent facts or overstate coverage.",
    )
    clarify_topics = {
        "clarify_importance": ["criterion"],
        "clarify_development": ["metric", "horizon"],
        # A representative extraction shape; the open basis has alternatives below.
        "clarify_actuals": ["criterion", "horizon"],
        "ambiguous_customer": ["customer_identity"],
        "weight": ["unit"],
        "three_turn": ["criterion"],
        "final": ["metric", "horizon"],
    }
    if turn_index == 0 and kind in clarify_topics:
        contract.value["answer_kind"] = "clarification"
        contract.fact("Q", "response_mode", "clarification")
        contract.fact(
            "Q", "clarification_topics", clarify_topics[kind], "required_topics"
        )
        if kind == "clarify_actuals":
            # Only this open-outlook context permits either basis question.
            # Keep extracted labels and the independent suitability check intact.
            topic_rule = contract.value["dimensions"]["Q"]["checks"][-1]
            topic_rule.update(
                comparison="required_topic_groups",
                expected=[["criterion", "metric"], ["horizon"]],
            )
        contract.fact("Q", "result_assertion", "none")
        contract.semantic(
            "Q",
            "clarification_suitable",
            "Ask the missing decision needed to answer; any additional question must be relevant and unresolved. Do not supply an unqualified winner or complete result before clarification.",
        )
        if kind == "ambiguous_customer":
            contract.source("ambiguous_customer_lookup", ["Customer"])
            matches = sorted(
                str(row["code"]) for row in data["customer"] if row["name"] == "Atlas"
            )
            contract.fact(
                "R", "candidate_customer_ids", matches, "set", entity_manager="Customer"
            )
        return contract.value
    if kind == "metric_correction" and turn_index == 0:
        contract.value["answer_kind"] = "reference_unspecified"
        contract.value["contract_coverage"] = "semantic_only"
        contract.semantic(
            "A",
            "no_invented_revenue_forecast",
            "This reference specifies the corrected second turn only. On the first turn accept a supported response or suitable limitation/clarification; do not assume future prices or assert an unsupported revenue forecast.",
        )
        return contract.value

    contract.fact("A", "response_mode", "answer")
    if turn_index > 0:
        contract.semantic(
            "Q",
            "no_repeated_clarification",
            "Do not ask again for a choice already explicitly resolved by the current user question or visible user history. A genuinely new unresolved question is not repetition. Bind the Boolean judgment to the current answer and conversation; failures need verbatim question and resolving user-choice witnesses.",
        )
        contract.semantic(
            "Q",
            "carryover_respected",
            "Use the clarified/corrected metric, horizon, customer and filters from the persisted conversation; do not repeat already answered questions.",
        )
    if int(case["id"][1:]) >= 101 and kind != "steel_inventory_scope":
        contract.semantic(
            "Q",
            "unnecessary_clarification",
            "Answer the clear single-turn business question directly using the exposed fields and glossary defaults. Do not ask the user to repeat information already specified or available in the glossary.",
        )
    resolved = kind
    if kind == "clarify_importance":
        resolved = "rank_revenue"
    elif kind == "clarify_development":
        resolved = "plan"
    elif kind == "metric_correction":
        resolved = "forecast"
    if resolved == "steel_inventory_scope":
        # ReadOnly startup activates every row present in the seed payload.
        # The separate business active flag does not describe soft deletion.
        identifiers = sorted(
            str(row["code"]) for row in data["material"] if row["family"] == "steel"
        )
        contract.source("current_material_inventory", ["Material"])
        contract.selection(
            identifiers, manager="Material", family="steel", is_active=True
        )
        contract.semantic(
            "A",
            "model_specific_deletion_semantics",
            "Use this manager's real semantics: Material.is_active=False is framework soft deletion; Material.active is a separate business-availability flag. Do not classify business-inactive records as deleted, invent deleted rows/history, or invent an include-deleted chat tool parameter. An appropriate scope question is allowed before the follow-up.",
        )
        if turn_index == 0:
            contract.value["selection_scope_contract"] = {
                "version": "1",
                "manager": "Material",
                "result_field": "result_ids",
                "claims_field": "constraints",
                "row_fields": {"family": "family", "is_active": "isActive"},
            }
            contract.value["answer_kind"] = "answer_or_clarification"
            contract.value["semantic_checks"][0]["instruction"] = (
                "A useful scope question is a complete permitted first response. "
                "For a direct answer, require complete evidence-backed current inventory "
                "within the stated scope. For a clarification, assess its factual preface "
                "without demanding a terminal result list. Do not invent facts or present "
                "an unsupported complete inventory."
            )
            contract.semantic(
                "Q",
                "scope_response_suitable",
                "Either ask a useful question about current/deleted record scope, or provide a complete grounded current inventory with the scope clear. Do not require clarification when a direct answer is sufficient, silently narrow current inventory to business active=True, or ask an unrelated metric/horizon question.",
            )
            clarification = _Contract(case, turn_index, data)
            clarification.value["answer_kind"] = "clarification"
            clarification.fact("Q", "response_mode", "clarification")
            clarification.value["semantic_checks"] = list(
                contract.value["semantic_checks"]
            )
            for check in clarification.value["semantic_checks"]:
                clarification.value["dimensions"][check["dimension"]]["applicable"] = (
                    True
                )
            contract.value["response_alternatives"] = {
                "clarification": clarification.value
            }
    elif resolved in {"active_customers", "ongoing_projects"}:
        table, manager = {
            "active_customers": ("customer", "Customer"),
            "ongoing_projects": ("project", "Project"),
        }[resolved]
        active_constraints: dict[str, Any] = (
            {"status": "ongoing"}
            if resolved == "ongoing_projects"
            else {"active": True}
        )
        matching = [
            row
            for row in data[table]
            if all(row[key] == value for key, value in active_constraints.items())
        ]
        identifiers = sorted(str(row["code"]) for row in matching)
        contract.source("current_business_rows", [manager])
        contract.selection(identifiers, manager=manager, **active_constraints)
        if resolved == "ongoing_projects":
            contract.fact(
                "R",
                "project_names",
                {code: projects[code]["name"] for code in identifiers},
                entity_manager="Project",
            )
    elif resolved in {"project_costs_wbs", "project_costs_project"}:
        year = date.fromisoformat(FROZEN_CLOCK[:10]).year - 1
        rows = [
            row
            for row in data["project_cost"]
            if row["kind"] == "actual"
            and f"{year}-01-01" <= row["cost_date"] < f"{year + 1}-01-01"
            and (resolved == "project_costs_wbs" or row["project_code"] == aurora)
        ]
        if any(row["currency"] != "EUR" for row in rows):
            message = (
                "Project-cost oracle requires an explicit rate for non-EUR actuals"
            )
            raise ValueError(message)
        cost_values: dict[str, Decimal] = {}
        for row in rows:
            key = str(
                row["wbs_code"]
                if resolved == "project_costs_wbs"
                else row["project_code"]
            )
            cost_values[key] = cost_values.get(key, Decimal(0)) + _decimal(
                row["net_amount"]
            )
        contract.source("actual_cost_ledger", ["ProjectCost"])
        contract.measure("actual_net_project_cost", "EUR", [year], ["actual"])
        contract.fact(
            "C",
            "period",
            {"start": f"{year}-01-01", "end_exclusive": f"{year + 1}-01-01"},
        )
        cost_manager = "WbsElement" if resolved == "project_costs_wbs" else "Project"
        contract.fact(
            "C",
            "values",
            {key: _rounded(value) for key, value in sorted(cost_values.items())},
            entity_manager=cost_manager,
        )
        contract.fact("C", "total", _rounded(sum(cost_values.values(), Decimal(0))))
        contract.selection(
            sorted(cost_values),
            manager=cost_manager,
            kind="actual",
            **({"project": aurora} if resolved == "project_costs_project" else {}),
        )
        if resolved == "project_costs_wbs":
            contract.source("wbs_labels", ["WbsElement"])
            wbs = _index(data, "wbs_element")
            contract.fact(
                "R",
                "wbs_names",
                {code: wbs[code]["name"] for code in sorted(cost_values)},
                entity_manager="WbsElement",
            )
    elif resolved == "material_density":
        material_code = _resolve(data, "material", "CU-ETP")
        material = _index(data, "material")[material_code]
        contract.source("material_density", ["Material"])
        contract.selection([material_code], manager="Material")
        contract.fact("R", "designation", material["name"])
        contract.fact(
            "C",
            "values",
            {material_code: _number(_decimal(material["density_g_cm3"]))},
            entity_manager="Material",
        )
        contract.fact("C", "unit", "g/cm3")
        contract.fact("C", "metric", "density")
    elif resolved == "recent_shipments":
        end = date.fromisoformat(FROZEN_CLOCK[:10]).replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        rows = [
            row
            for row in data["shipment"]
            if row["project_code"] == aurora
            and start.isoformat() <= row["shipped_at"] < end.isoformat()
            and row["source"] == "actual"
            and row["unit"] == "pieces"
        ]
        contract.source("actual_shipments", ["Shipment"])
        contract.selection([aurora], manager="Project", project=aurora)
        contract.measure("gross_shipped_quantity", "pieces", [start.year], ["actual"])
        contract.fact(
            "C",
            "period",
            {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        )
        contract.fact(
            "C", "values", {aurora: _number(_quantity(rows))}, entity_manager="Project"
        )
    elif resolved == "customer":
        contract.source("customer", ["Customer"])
        contract.selection([customer], manager="Customer")
        contract.fact("R", "customer_ids", [customer], "set", entity_manager="Customer")
    elif resolved == "material":
        contract.source("material", ["Material"])
        contract.selection([copper], manager="Material")
        contract.fact("R", "material_ids", [copper], "set", entity_manager="Material")
    elif resolved in {"projects", "follow_filter", "ambiguous_customer"}:
        selected_customer = "C04" if resolved == "ambiguous_customer" else customer
        materials = {"M02"} if resolved == "follow_filter" and turn_index == 1 else None
        contract.source("customer_projects", ["Project"])
        contract.selection(
            _projects(data, customer=selected_customer, materials=materials),
            manager="Project",
            owner_customer=selected_customer,
            **({"all_materials": sorted(materials)} if materials else {}),
        )
        contract.fact(
            "R", "customer_ids", [selected_customer], "set", entity_manager="Customer"
        )
        if materials:
            contract.source(
                "material_relation",
                ["Material", "Project"],
                ["Project", "ProjectPart", "Part"],
            )
    elif resolved in {
        "empty",
        "brass",
        "copper_projects",
        "both_materials",
        "no_shipments",
        "shared_material",
        "disconnected",
        "owner_billto",
        "supplier_path",
        "cycle",
    }:
        constraints: dict[str, Any] = {}
        contract.source("project_relations", ["Project"])
        if resolved == "cycle":
            contract.source("dependency_relations", ["ProjectDependency"])
            visited = {aurora}
            pending = [aurora]
            while pending:
                current = pending.pop()
                for edge in data["project_dependency"]:
                    target = str(edge["target_project_code"])
                    if edge["source_project_code"] == current and target not in visited:
                        visited.add(target)
                        pending.append(target)
            identifiers = sorted(visited - {aurora})
            constraints = {"reachable_from": aurora, "exclude_self": True}
        elif resolved == "owner_billto":
            identifiers = sorted(
                code
                for code in _projects(data, customer=customer)
                if projects[code]["approved"] is True
            )
            constraints = {"owner_customer": customer, "approved": True}
        elif resolved == "supplier_path":
            suppliers = {
                row["code"]
                for row in data["supplier"]
                if row["name"] == "Nord" and row["approved"]
            }
            parts = {
                row["code"] for row in data["part"] if row["supplier_code"] in suppliers
            }
            supplied = {
                row["project_code"]
                for row in data["project_part"]
                if row["part_code"] in parts
            }
            identifiers = sorted(set(_projects(data, materials={copper})) & supplied)
            contract.source(
                "approved_supplier_parts", ["Supplier", "Part", "ProjectPart"]
            )
            constraints = {
                "supplier_ids": sorted(suppliers),
                "supplier_approved": True,
                "all_materials": [copper],
            }
        else:
            materials = {copper}
            selected_customer = None
            if resolved == "empty":
                selected_customer = "C03"
            elif resolved == "brass":
                materials = {_resolve(data, "material", "Brass")}
            elif resolved == "both_materials":
                materials.add(_resolve(data, "material", "Steel"))
            elif resolved == "shared_material":
                materials = set(projects[aurora]["material_codes"])
            elif resolved == "disconnected":
                materials = {_resolve(data, "material", "Zeta")}
                selected_customer = customer
            elif resolved == "no_shipments":
                selected_customer = customer
            elif case["id"] == "E074":
                materials = {_resolve(data, "material", "CU-ETP")}
            identifiers = _projects(
                data, customer=selected_customer, materials=materials
            )
            if resolved == "shared_material":
                identifiers = [code for code in identifiers if code != aurora]
                constraints["exclude_project"] = aurora
            if resolved == "no_shipments":
                identifiers = [
                    code for code in identifiers if not _shipments(data, [code], 2025)
                ]
                contract.source("absence_of_shipments", ["Shipment"])
                constraints["no_shipments_year"] = 2025
            constraints["all_materials"] = sorted(materials)
            if selected_customer is not None:
                constraints["owner_customer"] = selected_customer
                contract.fact(
                    "R",
                    "customer_ids",
                    [selected_customer],
                    "set",
                    entity_manager="Customer",
                )
            contract.fact(
                "R", "material_ids", sorted(materials), "set", entity_manager="Material"
            )
        contract.selection(identifiers, manager="Project", **constraints)
        if resolved == "copper_projects":
            contract.fact("C", "ranked_ids", identifiers, entity_manager="Project")
    elif resolved in {"shipments", "weight", "returns", "mixed", "provenance"}:
        rows = _shipments(data, [aurora], 2025)
        gross = _quantity(rows)
        contract.source("actual_shipments", ["Shipment"])
        contract.selection([aurora], manager="Project", project=aurora)
        metric, unit = "gross_shipped_quantity", "pieces"
        values: dict[str, Any] = {aurora: _number(gross)}
        if resolved == "weight":
            metric, unit = "shipped_weight", "kg"
            values = {
                aurora: _number(gross * _decimal(projects[aurora]["kg_per_piece"]))
            }
            contract.source("project_unit_weight", ["Project"])
        elif resolved == "returns":
            returned = _quantity(
                [
                    row
                    for row in data["shipment_return"]
                    if row["project_code"] == aurora
                    and "2025-01-01" <= row["returned_at"] < "2026-01-01"
                ]
            )
            values = {
                "gross": _number(gross),
                "returns": _number(returned),
                "net": _number(gross - returned),
            }
            metric = "net_shipped_quantity"
            contract.source("separate_returns", ["ShipmentReturn"])
        contract.measure(metric, unit, [2025], ["actual"])
        contract.fact(
            "C", "period", {"start": "2025-01-01", "end_exclusive": "2026-01-01"}
        )
        contract.fact(
            "C",
            "values",
            values,
            entity_manager=None if resolved == "returns" else "Project",
        )
        if resolved in {"mixed", "provenance"}:
            contract.source(
                "project_material",
                ["Project", "Material"],
                ["Project", "Part", "ProjectPart", "Material"],
            )
            contract.fact(
                "R",
                "material_ids",
                sorted(projects[aurora]["material_codes"]),
                "set",
                entity_manager="Material",
            )
        if resolved == "provenance":
            revenues, _ = _revenues(data)
            contract.source("planned_net_revenue", ["ProjectCommercial"])
            contract.fact(
                "C",
                "planned_revenue",
                {
                    "project": aurora,
                    "year": 2027,
                    "unit": "EUR",
                    "value": None
                    if revenues[aurora] is None
                    else _rounded(_decimal(revenues[aurora])),
                },
            )
            contract.fact(
                "I",
                "provenance",
                {
                    "shipments": "Database",
                    "material": "ReadOnly",
                    "planned_revenue": "Calculation",
                },
            )
            contract.value["entity_identity_reference"].setdefault("paths", []).append(
                {
                    "path": ["planned_revenue", "project"],
                    "manager": "Project",
                    "shape": "scalar",
                }
            )
    elif resolved in {
        "revenue",
        "rank_revenue",
        "fx_rank",
        "missing_revenue",
        "zero_missing",
    }:
        revenues, conversions = _revenues(data)
        known = sorted(
            (code for code, value in revenues.items() if value is not None),
            key=lambda code: (-_decimal(revenues[code]), code),
        )
        missing = sorted(code for code, value in revenues.items() if value is None)
        contract.source("planned_net_revenue", ["ProjectCommercial"])
        contract.measure("planned_net_revenue", "EUR", [2027], ["existing_plan"])
        if resolved == "zero_missing":
            zeros = sorted(
                code for code, value in revenues.items() if value == Decimal(0)
            )
            contract.fact("R", "zero_ids", zeros, "set", entity_manager="Project")
            contract.fact("R", "missing_ids", missing, "set", entity_manager="Project")
            contract.selection(sorted([*zeros, *missing]), manager="Project")
        else:
            identifiers = (
                [aurora]
                if resolved == "revenue"
                else known
                if kind == "clarify_importance"
                else known[:1]
            )
            contract.selection(identifiers, manager="Project")
            contract.fact(
                "C",
                "values",
                {
                    code: None
                    if revenues[code] is None
                    else _rounded(_decimal(revenues[code]))
                    for code in identifiers
                },
                entity_manager="Project",
            )
            if resolved != "revenue":
                contract.fact("C", "ranked_ids", identifiers, entity_manager="Project")
                contract.value["ranking_reference"] = [
                    {"id": code, "value": _rounded(_decimal(revenues[code]))}
                    for code in known
                ]
            if resolved == "missing_revenue":
                contract.fact(
                    "R", "missing_ids", missing, "set", entity_manager="Project"
                )
                contract.fact("C", "ranking_coverage", RANKING_KNOWN_VALUES_ONLY)
                contract.semantic(
                    "A",
                    "incomplete_coverage_qualified",
                    "Identify missing revenue separately and state only the highest known value; do not claim an unconditional overall winner.",
                )
            if resolved == "fx_rank":
                contract.fact("C", "fx", conversions[identifiers[0]])
                contract.source(
                    "fixed_exchange_rate",
                    ["Project"],
                    required_fields={"Project": ["fx_rate", "fx_as_of", "fx_source"]},
                )
    elif resolved in {"top_material", "three_turn"}:
        eligible = (
            _projects(data, materials={copper})
            if resolved == "top_material" or turn_index == 2
            else sorted(projects)
        )
        quantities = {
            code: _quantity(_shipments(data, [code], 2025)) for code in eligible
        }
        ranked = sorted(eligible, key=lambda code: (-quantities[code], code))[
            : 5 if resolved == "top_material" else 1
        ]
        contract.source("shipment_ranking", ["Shipment", "Project"])
        contract.selection(
            ranked,
            manager="Project",
            **(
                {"all_materials": [copper]}
                if resolved == "top_material" or turn_index == 2
                else {}
            ),
        )
        contract.measure("gross_shipped_quantity", "pieces", [2025], ["actual"])
        contract.fact("C", "ranked_ids", ranked, entity_manager="Project")
        contract.fact(
            "C",
            "values",
            {code: _number(quantities[code]) for code in ranked},
            entity_manager="Project",
        )
    elif resolved in {
        "plan",
        "clarify_actuals",
        "actual_plan",
        "forecast",
        "forecast_boreal",
        "insufficient",
        "recommend",
        "final",
    }:
        selected_customer = {"forecast_boreal": "C02", "insufficient": "C03"}.get(
            resolved, customer
        )
        contract.fact(
            "R", "customer_ids", [selected_customer], "set", entity_manager="Customer"
        )
        if resolved in {"plan", "actual_plan", "final"}:
            contract.source(
                "existing_shipment_plan",
                ["ShipmentPlan"],
                ["CustomerOutlook"],
                required_fields={
                    "ShipmentPlan": ["quantity"],
                    "CustomerOutlook": ["existing_plan"],
                },
            )
            contract.fact("C", "plan", _plan(data, selected_customer))
            contract.semantic(
                "A",
                "additional_breakdowns_supported",
                "If the answer adds component/project breakdowns beside aggregate totals, verify every additional claim against eligible source evidence and its stated unit/year/population. No breakdown is required. Unsupported or contradictory extra breakdowns fail this check.",
            )
            contract.measure(
                "shipped_quantity", "pieces", list(FUTURE_YEARS), ["existing_plan"]
            )
        if resolved in {"clarify_actuals", "actual_plan", "final"}:
            contract.source("complete_year_actuals", ["Shipment"], ["CustomerOutlook"])
            contract.fact(
                "C",
                "actuals",
                {
                    str(year): value
                    for year, value in _history(data, selected_customer).items()
                },
            )
            contract.fact("C", "excluded_incomplete_years", [2026])
            if resolved == "clarify_actuals":
                contract.measure(
                    "shipped_quantity", "pieces", list(FULL_YEARS), ["actual"]
                )
            elif resolved == "actual_plan":
                contract.measure(
                    "shipped_quantity",
                    "pieces",
                    [*FULL_YEARS, *FUTURE_YEARS],
                    ["actual", "existing_plan"],
                )
        if resolved in {"forecast", "forecast_boreal", "insufficient", "final"}:
            _forecast_contract(contract, data, selected_customer)
        if resolved in {"recommend", "final"}:
            _recommendation(contract, data)
            contract.measure(
                "shipped_quantity",
                "pieces",
                list(FUTURE_YEARS),
                ["actual", "existing_plan", "new_forecast"],
            )
        if resolved == "final":
            contract.semantic(
                "A",
                "distinct_actual_plan_forecast_action",
                "Clearly distinguish historical actuals, authoritative existing plans, newly calculated forecasts and proposed customer actions.",
            )
    else:
        message = f"No explicit implementation for {kind} turn {turn_index}"
        raise ValueError(message)
    if turn_index > 0:
        contract.semantic(
            "Q",
            "visible_context_carryover",
            "Carry forward only explicit user choices actually visible in conversation history and still applicable to the current request. Verify any contextual customer/metric/unit/period reference against eligible source evidence; changed, ambiguous or unsupported prior scope must not be silently reused. Do not require restatement of a clearly unchanged scope, or copy omitted numeric results from history.",
        )
        context_keys = ("customer_ids", "metric", "unit", "years", "constraints")
        contract.fact(
            "Q",
            "context",
            {
                key: contract.value["facts"][key]
                for key in context_keys
                if key in contract.value["facts"]
            },
        )
        identities = contract.value["entity_identity_reference"]
        for name, descriptor in identities["fields"].items():
            if name in context_keys:
                identities.setdefault("paths", []).append(
                    {**descriptor, "path": ["context", name]}
                )
        for path in list(contract.value["entity_identity_reference"].get("paths", [])):
            if path["path"][0] in context_keys:
                contract.value["entity_identity_reference"]["paths"].append(
                    {**path, "path": ["context", *path["path"]]}
                )
    return contract.value
