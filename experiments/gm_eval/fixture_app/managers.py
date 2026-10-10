"""Real Database, ReadOnly and Calculation managers for synthetic fixtures.

The factory only declares ordinary interfaces. GeneralManager's app loader,
metaclass, startup hooks and GraphQL builder perform their usual work.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any

from django.conf import settings
from django.db import models

from general_manager.api.property import graph_ql_property
from general_manager.interface import (
    CalculationInterface,
    DatabaseInterface,
    ReadOnlyInterface,
)
from general_manager.manager import GeneralManager, Input
from general_manager.interface.unit_contract import FieldUnitContract

from experiments.gm_eval.seeds import build_seed

CONFIG = settings.GM_EVAL_FIXTURE
SEED_DATA = build_seed(CONFIG["snapshot"], CONFIG["variant"])
MANAGERS: dict[str, Any] = {}
MANAGER_CATALOG: dict[str, dict[str, Any]] = {}
DISTRACTOR_ROWS: dict[str, list[dict[str, Any]]] = {}
READONLY_ROWS: dict[str, list[dict[str, Any]]] = {}
BLOCKS: list[dict[str, Any]] = []


def _money() -> models.DecimalField[Any]:
    return models.DecimalField(max_digits=18, decimal_places=4, null=True)


def _relation(
    target: str, *, name: str = "+", nullable: bool = False
) -> models.ForeignKey[Any]:
    if nullable:
        return models.ForeignKey(
            f"gm_eval_fixture.{target}",
            on_delete=models.PROTECT,
            related_name=name,
            null=True,
        )
    return models.ForeignKey(
        f"gm_eval_fixture.{target}",
        on_delete=models.PROTECT,
        related_name=name,
        null=False,
    )


def _position(name: str) -> bytes:
    """Stable seeded ordering without altering normal framework registries."""
    return sha256(f"{CONFIG['seed']}:{name}".encode()).digest()


def _declare(
    name: str,
    kind: str,
    fields: dict[str, Any],
    description: str,
    *,
    aliases: tuple[str, ...] = (),
    domain: str = "commercial operations",
    properties: dict[str, Any] | None = None,
    unit_contracts: dict[str, FieldUnitContract] | None = None,
) -> Any:
    base = {
        "database": DatabaseInterface,
        "readonly": ReadOnlyInterface,
        "calculation": CalculationInterface,
    }[kind]
    interface_attrs = dict(fields)
    interface_attrs["__module__"] = __name__
    if unit_contracts:
        interface_attrs["field_unit_contracts"] = unit_contracts
    if kind != "calculation":
        interface_attrs["Meta"] = type(
            "Meta", (), {"app_label": "gm_eval_fixture", "ordering": ["code"]}
        )
    interface = type("Interface", (base,), interface_attrs)
    attrs: dict[str, Any] = {
        "__module__": __name__,
        "__doc__": description,
        "Interface": interface,
        "chat_exposed": True,
        "fixture_interface": kind,
    }
    if kind == "readonly":
        attrs["_data"] = READONLY_ROWS[name]
    attrs.update(properties or {})
    manager = type(name, (GeneralManager,), attrs)
    MANAGERS[name] = manager
    globals()[name] = manager
    MANAGER_CATALOG[name] = {
        "domain": domain,
        "aliases": list(aliases),
        "use_when": description,
        "distinguish_from": [],
    }
    return manager


def _identity() -> dict[str, Any]:
    return {
        "code": models.CharField(max_length=80, unique=True),
        "name": models.CharField(max_length=160),
        "aliases": models.CharField(max_length=300, blank=True),
    }


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@graph_ql_property(filterable=True)
def _planned_revenue(self: Any) -> Decimal | None:
    project = self.project
    if (
        project.plan_year != self.year
        or project.plan_units is None
        or project.net_price is None
    ):
        return None
    return _round(project.plan_units * project.net_price)


@graph_ql_property(filterable=True)
def _revenue_eur(self: Any) -> Decimal | None:
    project = self.project
    if (
        project.plan_year != self.year
        or project.plan_units is None
        or project.net_price is None
        or project.fx_rate is None
    ):
        return None
    return _round(project.plan_units * project.net_price * project.fx_rate)


@graph_ql_property
def _currency(self: Any) -> str:
    return str(self.project.currency)


def _actuals(customer: Any) -> dict[int, Decimal]:
    """Use persisted Shipment rows, retaining absent years as absent."""
    values: dict[int, Decimal] = {}
    for row in MANAGERS["Shipment"].filter(project__customer=customer):
        if 2023 <= row.shipped_at.year <= 2025:
            year = row.shipped_at.year
            values[year] = values.get(year, Decimal(0)) + Decimal(row.quantity)
    return values


@graph_ql_property
def _history_complete(self: Any) -> bool:
    return set(_actuals(self.customer)) == {2023, 2024, 2025}


@graph_ql_property(filterable=True)
def _forecast(self: Any) -> Decimal | None:
    actuals = _actuals(self.customer)
    if set(actuals) != {2023, 2024, 2025}:
        return None
    values = [actuals[year] for year in (2023, 2024, 2025)]
    slope = (values[2] - values[0]) / Decimal(2)
    intercept = sum(values) / Decimal(3) - slope
    return _round(max(Decimal(0), intercept + slope * Decimal(self.year - 2023)))


@graph_ql_property
def _scenario_low(self: Any) -> Decimal | None:
    return None if self.forecast is None else _round(self.forecast * Decimal("0.8"))


@graph_ql_property
def _scenario_high(self: Any) -> Decimal | None:
    return None if self.forecast is None else _round(self.forecast * Decimal("1.2"))


@graph_ql_property
def _existing_plan(self: Any) -> int | None:
    rows = list(
        MANAGERS["ShipmentPlan"].filter(project__customer=self.customer, year=self.year)
    )
    return sum(row.quantity for row in rows) if rows else None


@graph_ql_property
def _gap(self: Any) -> Decimal | None:
    if self.forecast is None or self.existing_plan is None:
        return None
    return _round(self.forecast - Decimal(self.existing_plan))


@graph_ql_property
def _derived_value(self: Any) -> Decimal:
    return _round(Decimal(self.record.quantity) * self.record.unit_value)


def _create_fixture() -> None:
    count, variant = CONFIG["manager_count"], CONFIG["variant"]
    READONLY_ROWS["Material"] = [
        {**row, "aliases": " | ".join(row["aliases"])} for row in SEED_DATA["material"]
    ]
    definitions: list[tuple[str, str, dict[str, Any], str, tuple[str, ...], str]] = [
        (
            "Customer",
            "database",
            {**_identity(), "active": models.BooleanField()},
            "Customers owning commercial projects, with explicit active status. XYZ is an alias of XYZ Industrie; owner differs from invoice recipient.",
            ("customer", "Kunde", "client", "XYZ"),
            "sales",
        ),
        (
            "Material",
            "readonly",
            {
                **_identity(),
                "family": models.CharField(max_length=30),
                "active": models.BooleanField(),
                "density_g_cm3": _money(),
            },
            "Authoritative material reference with family, active status, designation aliases and density in g/cm3: Copper/Kupfer/cuivre/red metal/CU-ETP, Steel, StainlessSteel, LegacySteel and Brass. Names do not imply project material membership.",
            ("Werkstoff", "material", "métal rouge"),
            "materials",
        ),
        (
            "Project",
            "database",
            {
                **_identity(),
                "customer": _relation("Customer", name="owned_projects"),
                "bill_to": _relation("Customer", name="billed_projects"),
                "materials": models.ManyToManyField(
                    "gm_eval_fixture.Material", related_name="projects"
                ),
                "approved": models.BooleanField(default=True),
                "status": models.CharField(max_length=30),
                "plan_year": models.IntegerField(),
                "plan_units": _money(),
                "net_price": _money(),
                "currency": models.CharField(max_length=3),
                "kg_per_piece": _money(),
                "fx_rate": _money(),
                "fx_as_of": models.DateField(null=True),
                "fx_source": models.CharField(max_length=120),
            },
            "Commercial projects with owning customer, bill-to customer, material relations and declared net revenue plan inputs. ProjectCommercial computes revenue; Shipment records actual pieces.",
            ("Projekt", "projet", "Auftrag", "commercial project"),
            "sales",
        ),
        (
            "Shipment",
            "database",
            {
                "code": models.CharField(max_length=80, unique=True),
                "project": _relation("Project", name="shipments"),
                "shipped_at": models.DateField(),
                "quantity": models.IntegerField(),
                "unit": models.CharField(max_length=20),
                "source": models.CharField(max_length=30),
            },
            "Actual shipment events measured in integer pieces by project and UTC Gregorian date. Use complete calendar years [January 1, next January 1); 2026 is incomplete YTD. Returns are separate events.",
            ("Ist-Lieferungen", "pièces livrées", "actual shipped pieces"),
            "logistics",
        ),
    ]
    if count >= 10:
        READONLY_ROWS["ShipmentPlan"] = []
        definitions.extend(
            [
                (
                    "ShipmentPlan",
                    "readonly",
                    {
                        "code": models.CharField(max_length=80, unique=True),
                        "project": _relation("Project", name="shipment_plans"),
                        "year": models.IntegerField(),
                        "quantity": models.IntegerField(),
                        "source": models.CharField(max_length=30),
                    },
                    "Existing authoritative shipment plan in pieces for 2027-2031 by project. This is an existing plan, distinct from actual shipments and the new CustomerOutlook forecast.",
                    ("Lieferplan", "plan existant", "planned shipments"),
                    "planning",
                ),
                (
                    "Part",
                    "database",
                    {
                        **_identity(),
                        "material": _relation("Material", name="parts"),
                        **(
                            {
                                "supplier": _relation(
                                    "Supplier", name="parts", nullable=True
                                )
                            }
                            if variant == "supplier"
                            else {}
                        ),
                    },
                    "Physical component with authoritative material reference and optional supplying company; project membership uses ProjectPart.",
                    ("Teil", "article", "component"),
                    "manufacturing",
                ),
                (
                    "ProjectPart",
                    "database",
                    {
                        "code": models.CharField(max_length=80, unique=True),
                        "project": _relation("Project", name="project_parts"),
                        "part": _relation("Part", name="project_parts"),
                    },
                    "Explicit project-to-component assignment; follows ProjectPart to Part to Material or Supplier.",
                    ("Projektteil", "affectation article"),
                    "manufacturing",
                ),
                (
                    "Invoice",
                    "database",
                    {
                        "code": models.CharField(max_length=80, unique=True),
                        "project": _relation("Project", name="invoices"),
                        "year": models.IntegerField(),
                        "net_amount": _money(),
                        "currency": models.CharField(max_length=3),
                    },
                    "Historical issued net invoices, not planned project revenue or physical shipment quantities.",
                    ("Rechnung", "facture", "billed amount"),
                    "finance",
                ),
            ]
        )

    prefixes = (
        "Purchase",
        "Service",
        "Production",
        "Quality",
        "Freight",
        "Treasury",
        "Sales",
        "Maintenance",
        "Packaging",
        "Warehouse",
        "Export",
        "Import",
        "Repair",
        "Inspection",
        "Assembly",
        "Distribution",
        "Rental",
        "Warranty",
        "Tooling",
        "Procurement",
        "Transport",
        "Refurbishment",
        "Engineering",
        "Aftermarket",
    )
    for block_index, prefix in enumerate(prefixes[: max(0, (count - 10) // 10)]):
        category, plan = f"{prefix}Category", f"{prefix}RevenuePlanSnapshot"
        db_names = [
            f"{prefix}{suffix}"
            for suffix in (
                "Order",
                "Line",
                "Customer",
                "Project",
                "Delivery",
                "Receipt",
            )
        ]
        replacement = {
            "supplier": "Supplier",
            "cycle": "ProjectDependency",
            "returns": "ShipmentReturn",
            "project_costs": "ProjectCost",
        }.get(variant)
        if block_index == 0 and replacement:
            db_names[-1] = replacement
        if block_index == 0 and variant == "project_costs":
            plan = "WbsElement"
        BLOCKS.append(
            {
                "domain": prefix.lower(),
                "database": db_names,
                "readonly": [category, plan],
                "calculation": [f"{prefix}OrderValue", f"{prefix}DeliveryOutlook"],
            }
        )
        for ro_name, label in (
            (category, "classification"),
            (plan, "archived commercial planning snapshot"),
        ):
            if ro_name == "WbsElement":
                READONLY_ROWS[ro_name] = SEED_DATA["wbs_element"]
                definitions.append(
                    (
                        ro_name,
                        "readonly",
                        _identity(),
                        "Authoritative work breakdown structure (WBS) cost classification: W01 Engineering and W02 ProjectManagement. Project costs last year means actual net costs in EUR during [2025-01-01,2026-01-01), grouped by WBS when requested. Exclude plans; no currency conversion is needed. ProjectCost supplies dated ledger entries.",
                        (
                            "WBS",
                            "PSP-Element",
                            "Projektstrukturplan",
                            "work breakdown structure",
                        ),
                        "project finance",
                    )
                )
                continue
            READONLY_ROWS[ro_name] = [
                {
                    "code": f"{prefix.upper()}-REF",
                    "name": f"{prefix} {label}",
                    "aliases": f"{prefix.lower()} reference",
                    "year": 2027,
                    "quantity": 12,
                }
            ]
            definitions.append(
                (
                    ro_name,
                    "readonly",
                    {
                        **_identity(),
                        "year": models.IntegerField(),
                        "quantity": models.IntegerField(),
                    },
                    f"{prefix} {label}; local domain reference distinct from authoritative customer shipment plans and core project materials.",
                    (f"{prefix.lower()} plan", f"{prefix.lower()} reference"),
                    prefix.lower(),
                )
            )
        for index, db_name in enumerate(db_names):
            if db_name == "ProjectCost":
                definitions.append(
                    (
                        db_name,
                        "database",
                        {
                            "code": models.CharField(max_length=80, unique=True),
                            "project": _relation("Project", name="project_costs"),
                            "wbs": _relation("WbsElement", name="project_costs"),
                            "cost_date": models.DateField(),
                            "kind": models.CharField(max_length=20),
                            "net_amount": _money(),
                            "currency": models.CharField(max_length=3),
                            "source": models.CharField(max_length=80),
                        },
                        "Project cost ledger with actual/plan kind, date, project and WBS. Fixture convention: project costs last year means actual net costs in EUR during [2025-01-01,2026-01-01), grouped by WBS W01 Engineering and W02 ProjectManagement when requested. Frozen clock 2026-10-03. Exclude plan and out-of-period rows; no exchange rate is needed or may be invented.",
                        (
                            "Projektkosten",
                            "project costs",
                            "coûts de projet",
                            "Istkosten",
                        ),
                        "project finance",
                    )
                )
                continue
            if db_name == "Supplier":
                definitions.append(
                    (
                        db_name,
                        "database",
                        {**_identity(), "approved": models.BooleanField()},
                        "Supplier company linked to components. Nord and Sud are suppliers, not customers; select approved suppliers when requested.",
                        ("Lieferant", "fournisseur", "Nord"),
                        "procurement",
                    )
                )
                continue
            if db_name == "ProjectDependency":
                definitions.append(
                    (
                        db_name,
                        "database",
                        {
                            "code": models.CharField(max_length=80, unique=True),
                            "source_project": _relation(
                                "Project", name="outgoing_dependencies"
                            ),
                            "target_project": _relation(
                                "Project", name="incoming_dependencies"
                            ),
                        },
                        "Directed project dependency edge from prerequisite source project to dependent target project. Cycles require visited-code deduplication.",
                        ("Abhängigkeit", "dependency", "dépendance"),
                        "engineering",
                    )
                )
                continue
            if db_name == "ShipmentReturn":
                definitions.append(
                    (
                        db_name,
                        "database",
                        {
                            "code": models.CharField(max_length=80, unique=True),
                            "project": _relation("Project", name="returns"),
                            "returned_at": models.DateField(),
                            "quantity": models.IntegerField(),
                        },
                        "Separate returned pieces; deduct only when the requested metric explicitly requires net shipments.",
                        ("retour", "Rücksendung", "returns"),
                        "logistics",
                    )
                )
                continue
            fields = {
                **_identity(),
                "customer": _relation("Customer"),
                "project": _relation("Project"),
                "category": _relation(category),
                "quantity": models.IntegerField(),
                "unit_value": _money(),
            }
            if index > 0:
                fields["order"] = _relation(db_names[0])
            definitions.append(
                (
                    db_name,
                    "database",
                    fields,
                    f"{prefix} {db_name[len(prefix) :].lower()} with its own quantity, commercial value and local classification, connected to a commercial project and customer. Use for {prefix.lower()} operations; do not substitute for Project, Shipment or Customer.",
                    (
                        f"{prefix.lower()} Auftrag",
                        f"{prefix.lower()} article",
                        f"{prefix.lower()} commercial value",
                    ),
                    prefix.lower(),
                )
            )
            DISTRACTOR_ROWS[db_name] = [
                {
                    "code": f"{prefix.upper()}-{index + 1}",
                    "name": f"{prefix} {index + 1}",
                    "aliases": f"{prefix.lower()} reference order",
                    "customer_code": "C01",
                    "project_code": "P01",
                    "category_code": f"{prefix.upper()}-REF",
                    "quantity": index + 2,
                    "unit_value": "17.50",
                    **({"order_code": f"{prefix.upper()}-1"} if index > 0 else {}),
                }
            ]

    # String model references allow normal Django lazy relation resolution while
    # a seeded registration order varies target positions without registry edits.
    definitions.sort(key=lambda definition: _position(definition[0]))
    # Explicit application contract migration, independent of seeded records and
    # cases. Numeric field names/descriptions themselves confer no dimensions.
    declared_units = {
        "Shipment": {
            "quantity": FieldUnitContract.quantity(
                unit_field="unit", units={"pieces": "count"}
            )
        },
        "Project": {
            "kg_per_piece": FieldUnitContract.factor(
                source_unit="count", target_unit="kg"
            )
        },
        "Material": {
            "density_g_cm3": FieldUnitContract.factor(
                source_unit="cm**3", target_unit="g"
            )
        },
    }
    for name, kind, fields, description, aliases, domain in definitions:
        _declare(
            name,
            kind,
            fields,
            description,
            aliases=aliases,
            domain=domain,
            unit_contracts=declared_units.get(name),
        )
    _declare(
        "ProjectCommercial",
        "calculation",
        {
            "project": Input(
                MANAGERS["Project"], possible_values=lambda: MANAGERS["Project"].all()
            ),
            "year": Input(int, possible_values=[2027]),
        },
        "Calculated net planned revenue: project plan_units times net_price for 2027, missing inputs propagate null; currency is explicit. revenue_eur uses only the declared fixture exchange rate dated 2026-10-03. Decimal rounding to 0.01 happens at the final result.",
        aliases=("Planumsatz", "revenu net prévu", "planned net revenue"),
        domain="finance",
        properties={
            "planned_revenue": _planned_revenue,
            "revenue_eur": _revenue_eur,
            "currency": _currency,
        },
    )
    if count >= 10:
        _declare(
            "CustomerOutlook",
            "calculation",
            {
                "customer": Input(
                    MANAGERS["Customer"],
                    possible_values=lambda: MANAGERS["Customer"].all(),
                ),
                "year": Input(int, possible_values=list(range(2027, 2032))),
            },
            "New linear shipment forecast from complete 2023-2025 actual Shipment rows, OLS x=[0,1,2], nonnegative clamp. Missing year means no forecast. Scenario band is ±20%, not statistical confidence. Existing plan stays separate; opportunity gap is forecast minus existing plan. Frozen clock 2026-10-03: next year is 2027.",
            aliases=(
                "Prognose",
                "prévision",
                "customer development",
                "shipment forecast",
            ),
            domain="planning",
            properties={
                "history_complete": _history_complete,
                "forecast": _forecast,
                "scenario_low": _scenario_low,
                "scenario_high": _scenario_high,
                "existing_plan": _existing_plan,
                "opportunity_gap": _gap,
            },
        )
    for block in BLOCKS:
        for name, source in zip(
            block["calculation"],
            (block["database"][0], block["database"][4]),
            strict=True,
        ):
            source_manager = MANAGERS[source]
            _declare(
                name,
                "calculation",
                {"record": Input(source_manager, possible_values=source_manager.all)},
                f"Calculated {block['domain']} record value from stored quantity times unit_value. Distinct from core project net plan and customer forecast.",
                aliases=(f"{block['domain']} forecast", f"{block['domain']} value"),
                domain=block["domain"],
                properties={"derived_value": _derived_value},
            )
    catalog_names = list(MANAGER_CATALOG)
    catalog_names.sort(key=_position)
    for name, entry in MANAGER_CATALOG.items():
        entry["aliases"].sort(key=_position)
        if name not in {
            "Customer",
            "Material",
            "Project",
            "Shipment",
            "ProjectCommercial",
            "ShipmentPlan",
            "CustomerOutlook",
            "Part",
            "ProjectPart",
            "Invoice",
        }:
            entry["distinguish_from"] = ["Project", "Customer", "Shipment"]
    ordered = {name: MANAGER_CATALOG[name] for name in catalog_names}
    MANAGER_CATALOG.clear()
    MANAGER_CATALOG.update(ordered)


_create_fixture()
