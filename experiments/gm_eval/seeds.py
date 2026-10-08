"""Independent, exact source rows for the synthetic GeneralManager evaluation.

Tables use singular snake_case names, stable business codes, ISO dates and
decimal strings. Derived revenues and forecasts are deliberately absent: both
the real calculation managers and the independent oracle must derive them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

FROZEN_CLOCK = "2026-10-03T12:00:00Z"
SNAPSHOTS = ("RANK", "TREND")
VARIANTS = (
    "base",
    "duplicate_customer",
    "brass",
    "date_edges",
    "weight",
    "returns",
    "fx",
    "missing_revenue",
    "page2",
    "both",
    "no_shipments",
    "supplier",
    "owner_billto",
    "cycle",
    "disconnected",
    "missing_history",
    "project_costs",
    "recent_shipments",
)


class SeedConfigurationError(ValueError):
    """Invalid source snapshot selection."""

    def __init__(self, field: str, value: str, required: str | None = None) -> None:
        detail = f"; requires {required}" if required else ""
        super().__init__(f"Invalid fixture {field}: {value}{detail}")


def _project(
    code: str, name: str, customer: str, material: str, revenue: int = 0
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "customer_code": customer,
        "bill_to_code": customer,
        "approved": True,
        "status": "ongoing"
        if code in {"P01", "P03", "P05"}
        else ("completed" if code in {"P02", "P04", "P11"} else "planned"),
        "material_codes": [material],
        "plan_year": 2027,
        "plan_units": str(revenue // 100),
        "net_price": "100",
        "currency": "EUR",
        "kg_per_piece": "1",
        "fx_rate": "1",
        "fx_as_of": "2026-10-03",
        "fx_source": "synthetic EUR identity reference",
    }


def build_seed(
    snapshot: str = "RANK", variant: str = "base"
) -> dict[str, list[dict[str, Any]]]:
    """Return a fresh deterministic source snapshot, rejecting unknown variants."""
    if snapshot not in SNAPSHOTS:
        raise SeedConfigurationError("snapshot", snapshot)
    if variant not in VARIANTS:
        raise SeedConfigurationError("variant", variant)
    if variant == "missing_history" and snapshot != "TREND":
        raise SeedConfigurationError("variant", variant, "TREND snapshot")
    if variant in {"project_costs", "recent_shipments"} and snapshot != "RANK":
        raise SeedConfigurationError("variant", variant, "RANK snapshot")
    data: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "customer",
            "material",
            "project",
            "shipment",
            "shipment_plan",
            "part",
            "project_part",
            "invoice",
            "shipment_return",
            "supplier",
            "project_dependency",
            "exchange_rate",
            "wbs_element",
            "project_cost",
        )
    }
    data["customer"] = [
        {"code": "C01", "name": "XYZ Industrie", "aliases": ["XYZ"], "active": True},
        {"code": "C02", "name": "Boreal SA", "aliases": ["Boreal"], "active": True},
        {"code": "C03", "name": "Nova", "aliases": [], "active": False},
    ]
    data["material"] = [
        {
            "code": "M01",
            "name": "Copper",
            "family": "copper",
            "active": True,
            "density_g_cm3": "8.96",
            "aliases": [
                "Kupfer",
                "cuivre",
                "red metal",
                "rotes Metall",
                "CU-ETP",
                "métal rouge",
            ],
        },
        {
            "code": "M02",
            "name": "Steel",
            "aliases": ["Stahl", "acier"],
            "family": "steel",
            "active": True,
            "density_g_cm3": "7.85",
        },
        {
            "code": "M03",
            "name": "Brass",
            "aliases": ["Messing", "laiton"],
            "family": "brass",
            "active": True,
            "density_g_cm3": "8.50",
        },
        {
            "code": "M04",
            "name": "StainlessSteel",
            "aliases": ["stainless steel", "Edelstahl", "acier inoxydable"],
            "family": "steel",
            "active": True,
            "density_g_cm3": "7.90",
        },
        {
            "code": "M05",
            "name": "LegacySteel",
            "aliases": ["legacy steel", "Altstahl", "acier historique"],
            "family": "steel",
            "active": False,
            "density_g_cm3": "7.85",
        },
    ]
    for number, name, customer, material, revenue in [
        (1, "Aurora", "C01", "M01", 12000),
        (2, "Borealis", "C01", "M02", 30000),
        (3, "Cobalt", "C02", "M01", 24000),
        (4, "Delta", "C02", "M01", 10000),
        (5, "Echo", "C02", "M01", 8000),
        (6, "Fjord", "C02", "M01", 18000),
        (7, "Gaia", "C02", "M01", 8000),
        (8, "Helios", "C03", "M02", 5000),
        (11, "Iris Brass", "C02", "M03", 0),
    ]:
        data["project"].append(
            _project(f"P{number:02}", name, customer, material, revenue)
        )
    projects = {row["code"]: row for row in data["project"]}

    def shipment(project: str, day: str, quantity: int) -> None:
        data["shipment"].append(
            {
                "code": f"SH{len(data['shipment']) + 1:04}",
                "project_code": project,
                "shipped_at": day,
                "quantity": quantity,
                "unit": "pieces",
                "source": "actual",
            }
        )

    if snapshot == "RANK":
        for number, quantity in enumerate(
            (120, 500, 200, 150, 80, 60, 60, 70), start=1
        ):
            shipment(f"P{number:02}", "2025-06-15", quantity)
        shipment("P11", "2025-06-15", 0)
    else:
        for customer, values in {
            "C01": (100, 120, 140),
            "C02": (300, 280, 260),
            "C03": (50, 55, 60),
        }.items():
            customer_projects = [
                row["code"]
                for row in data["project"]
                if row["customer_code"] == customer and row["code"] != "P11"
            ]
            for year, total in zip((2023, 2024, 2025), values, strict=True):
                if variant == "missing_history" and customer == "C03" and year == 2024:
                    continue
                quotient, remainder = divmod(total, len(customer_projects))
                for index, project in enumerate(customer_projects):
                    shipment(project, f"{year}-06-15", quotient + (index < remainder))
            shipment(
                customer_projects[0],
                "2026-09-30",
                {"C01": 90, "C02": 150, "C03": 40}[customer],
            )
        for customer, quantities in {
            "C01": (150, 160, 170, 180, 190),
            "C02": (250, 245, 240, 235, 240),
            "C03": (60, 62, 65, 68, 70),
        }.items():
            customer_projects = [
                row["code"]
                for row in data["project"]
                if row["customer_code"] == customer and row["code"] != "P11"
            ]
            for year, total in zip(range(2027, 2032), quantities, strict=True):
                quotient, remainder = divmod(total, len(customer_projects))
                for index, project in enumerate(customer_projects):
                    data["shipment_plan"].append(
                        {
                            "code": f"PLAN-{project}-{year}",
                            "project_code": project,
                            "year": year,
                            "quantity": quotient + (index < remainder),
                            "source": "existing_plan",
                        }
                    )

    if variant == "duplicate_customer":
        for customer, project in (("C04", "P09"), ("C05", "P10")):
            data["customer"].append(
                {"code": customer, "name": "Atlas", "aliases": [], "active": True}
            )
            data["project"].append(
                _project(project, f"Atlas {customer}", customer, "M02")
            )
    elif variant == "date_edges":
        data["shipment"] = [
            row for row in data["shipment"] if row["project_code"] != "P01"
        ]
        for day, quantity in (
            ("2025-01-01", 50),
            ("2025-12-31", 70),
            ("2024-12-31", 999),
            ("2026-01-01", 999),
        ):
            shipment("P01", day, quantity)
        for index, row in enumerate(data["shipment"], start=1):
            row["code"] = f"SH{index:04}"
    elif variant == "weight":
        projects["P01"]["kg_per_piece"] = "2"
    elif variant == "returns":
        data["shipment_return"] = [
            {
                "code": "RET01",
                "project_code": "P01",
                "returned_at": "2025-10-01",
                "quantity": 10,
            }
        ]
    elif variant == "fx":
        projects["P03"].update(
            plan_units="400",
            currency="USD",
            fx_rate="0.8",
            fx_source="frozen synthetic USD/EUR reference",
        )
        data["exchange_rate"] = [
            {
                "code": "USD-EUR",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "rate": "0.8",
                "as_of": "2026-10-03",
                "source": "frozen synthetic USD/EUR reference",
            }
        ]
    elif variant == "missing_revenue":
        projects["P02"]["plan_units"] = None
        projects["P08"]["plan_units"] = "0"
    elif variant == "both":
        projects["P01"]["material_codes"].append("M02")
    elif variant == "no_shipments":
        data["project"].append(_project("P12", "Juniper", "C01", "M01"))
    elif variant == "supplier":
        data["supplier"] = [
            {"code": "SUP-N", "name": "Nord", "approved": True},
            {"code": "SUP-S", "name": "Sud", "approved": True},
        ]
    elif variant == "owner_billto":
        for row in data["project"]:
            row["approved"] = row["code"] in ("P01", "P03")
        projects["P03"]["bill_to_code"] = "C01"
    elif variant == "cycle":
        data["project_dependency"] = [
            {
                "code": f"DEP{index}",
                "source_project_code": source,
                "target_project_code": target,
            }
            for index, (source, target) in enumerate(
                (("P01", "P02"), ("P02", "P03"), ("P03", "P01"), ("P02", "P04")),
                start=1,
            )
        ]
    elif variant == "disconnected":
        data["material"].append(
            {
                "code": "M99",
                "name": "Zeta",
                "aliases": ["experimental Zeta"],
                "family": "experimental",
                "active": True,
                "density_g_cm3": None,
            }
        )
        data["project"].append(_project("P99", "Zeta Pilot", "C02", "M99"))
    elif variant == "recent_shipments":
        for day, quantity in (
            ("2026-08-31", 999),
            ("2026-09-15", 30),
            ("2026-10-01", 999),
        ):
            shipment("P01", day, quantity)
    elif variant == "project_costs":
        data["wbs_element"] = [
            {"code": "W01", "name": "Engineering"},
            {"code": "W02", "name": "ProjectManagement"},
        ]
        data["project_cost"] = [
            {
                "code": f"COST{number:02}",
                "project_code": project,
                "wbs_code": wbs,
                "cost_date": day,
                "kind": kind,
                "net_amount": amount,
                "currency": "EUR",
                "source": "synthetic cost ledger",
            }
            for number, (project, wbs, day, kind, amount) in enumerate(
                (
                    ("P01", "W01", "2025-02-01", "actual", "1200.00"),
                    ("P01", "W01", "2025-08-01", "actual", "800.00"),
                    ("P02", "W01", "2025-04-01", "actual", "1500.00"),
                    ("P01", "W02", "2025-05-01", "actual", "500.00"),
                    ("P02", "W02", "2025-09-01", "actual", "700.00"),
                    ("P01", "W01", "2025-06-01", "plan", "9000.00"),
                    ("P02", "W02", "2025-06-01", "plan", "7000.00"),
                    ("P01", "W01", "2024-12-31", "actual", "999.00"),
                    ("P02", "W02", "2026-01-01", "actual", "999.00"),
                ),
                start=1,
            )
        ]

    for project in data["project"]:
        for index, material in enumerate(project["material_codes"], start=1):
            part_code = f"PART-{project['code']}-{index}"
            supplier = None
            if variant == "supplier" and project["code"] in ("P01", "P02", "P03"):
                part_code, supplier = {
                    "P01": ("N1", "SUP-N"),
                    "P02": ("N2", "SUP-N"),
                    "P03": ("S1", "SUP-S"),
                }[project["code"]]
            data["part"].append(
                {
                    "code": part_code,
                    "name": f"{project['name']} component {index}",
                    "material_code": material,
                    "supplier_code": supplier,
                }
            )
            data["project_part"].append(
                {
                    "code": f"PP-{part_code}",
                    "project_code": project["code"],
                    "part_code": part_code,
                }
            )
        data["invoice"].append(
            {
                "code": f"INV-{project['code']}",
                "project_code": project["code"],
                "year": 2025,
                "net_amount": str(Decimal("250.00")),
                "currency": "EUR",
            }
        )
    return data
