"""One-process, one-startup runtime using ordinary Django and GM machinery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from typing import Any

from experiments.gm_eval.seeds import FROZEN_CLOCK, build_seed

MANAGER_COUNTS = (5, 10, 50, 100, 250)


class FixtureInvalidError(RuntimeError):
    """The installed fixture violates its declared census or source data."""

    def __init__(self, reason: str, evidence: Any = None) -> None:
        super().__init__(f"Invalid fixture ({reason}): {evidence}")


class FixtureConfigurationError(ValueError):
    """Reject unsupported scale and source combinations before startup."""

    def __init__(self, field: str, value: Any, expected: Any) -> None:
        super().__init__(f"Invalid {field}: {value}; expected {expected}")


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _run_startup_hooks(managers: list[Any]) -> list[str]:
    """Invoke the same registered hooks and dependency ordering as GM startup.

    The offline fixture has two phases: reference rows first, then RO plans
    after their persisted project references have been installed.
    """
    from general_manager.interface.infrastructure.startup_hooks import (
        order_interfaces_by_dependency,
        registered_startup_hook_entries,
    )

    registry = registered_startup_hook_entries()
    selected = {manager.Interface: manager.__name__ for manager in managers}
    groups: list[tuple[Any, list[Any]]] = []
    for interface, entries in registry.items():
        if interface not in selected:
            continue
        for entry in entries:
            group = next(
                (
                    items
                    for resolver, items in groups
                    if resolver is entry.dependency_resolver
                ),
                None,
            )
            if group is None:
                group = []
                groups.append((entry.dependency_resolver, group))
            if interface not in group:
                group.append(interface)
    completed = []
    for resolver, interfaces in groups:
        for interface in order_interfaces_by_dependency(interfaces, resolver):
            for entry in registry[interface]:
                if entry.dependency_resolver is resolver:
                    entry.hook()
            completed.append(selected[interface])
    return completed


def _seed_database(fixture: Any) -> list[str]:
    managers, data = fixture.MANAGERS, fixture.SEED_DATA
    readonly = [
        manager
        for name, manager in managers.items()
        if manager.fixture_interface == "readonly" and name != "ShipmentPlan"
    ]
    startup = _run_startup_hooks(readonly)
    objects: dict[str, dict[str, Any]] = {}

    def install(
        manager_name: str,
        rows: list[dict[str, Any]],
        relations: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        if manager_name not in managers:
            return
        model = managers[manager_name].Interface._model
        objects[manager_name] = {}
        field_names = {item.name for item in model._meta.concrete_fields}
        for source in rows:
            row = {key: value for key, value in source.items() if key in field_names}
            if "aliases" in row and isinstance(row["aliases"], list):
                row["aliases"] = " | ".join(row["aliases"])
            for source_field, (target_field, target_manager) in (
                relations or {}
            ).items():
                code = source.get(source_field)
                row[target_field] = (
                    objects[target_manager][code] if code is not None else None
                )
            obj = model.objects.create(**row)
            objects[manager_name][source["code"]] = obj

    objects["Material"] = {
        row.code: row for row in managers["Material"].Interface._model.objects.all()
    }
    if "WbsElement" in managers:
        objects["WbsElement"] = {
            row.code: row
            for row in managers["WbsElement"].Interface._model.objects.all()
        }
    install("Customer", data["customer"])
    install(
        "Project",
        data["project"],
        {
            "customer_code": ("customer", "Customer"),
            "bill_to_code": ("bill_to", "Customer"),
        },
    )
    for row in data["project"]:
        objects["Project"][row["code"]].materials.set(
            [objects["Material"][code] for code in row["material_codes"]]
        )
    install("Shipment", data["shipment"], {"project_code": ("project", "Project")})
    install("Supplier", data["supplier"])
    install(
        "ProjectCost",
        data["project_cost"],
        {"project_code": ("project", "Project"), "wbs_code": ("wbs", "WbsElement")},
    )
    part_relations = {"material_code": ("material", "Material")}
    if "Supplier" in managers:
        part_relations["supplier_code"] = ("supplier", "Supplier")
    install("Part", data["part"], part_relations)
    install(
        "ProjectPart",
        data["project_part"],
        {"project_code": ("project", "Project"), "part_code": ("part", "Part")},
    )
    install("Invoice", data["invoice"], {"project_code": ("project", "Project")})
    install(
        "ShipmentReturn",
        data["shipment_return"],
        {"project_code": ("project", "Project")},
    )
    install(
        "ProjectDependency",
        data["project_dependency"],
        {
            "source_project_code": ("source_project", "Project"),
            "target_project_code": ("target_project", "Project"),
        },
    )
    for block in fixture.BLOCKS:
        category = block["readonly"][0]
        objects[category] = {
            row.code: row for row in managers[category].Interface._model.objects.all()
        }
        for name in block["database"]:
            if name not in fixture.DISTRACTOR_ROWS:
                continue
            relations = {
                "customer_code": ("customer", "Customer"),
                "project_code": ("project", "Project"),
                "category_code": ("category", category),
            }
            if name != block["database"][0]:
                relations["order_code"] = ("order", block["database"][0])
            install(name, fixture.DISTRACTOR_ROWS[name], relations)
    if "ShipmentPlan" in managers:
        managers["ShipmentPlan"]._data = [
            {
                **{key: value for key, value in row.items() if key != "project_code"},
                "project": {"code": row["project_code"]},
            }
            for row in data["shipment_plan"]
        ]
        startup.extend(_run_startup_hooks([managers["ShipmentPlan"]]))
    return startup


@dataclass
class FixtureRuntime:
    """State and real tool access for one isolated synthetic application."""

    manager_count: int
    snapshot: str
    variant: str
    seed: int
    database_path: str
    managers: dict[str, Any]
    schema_index: dict[str, Any]
    scope: dict[str, Any]
    catalog: dict[str, Any]
    seed_data: dict[str, list[dict[str, Any]]]
    startup_managers: list[str]
    blocks: list[dict[str, Any]]
    _temporary_directory: TemporaryDirectory[str] | None = field(
        default=None, repr=False
    )

    @property
    def schema(self) -> Any:
        from general_manager.api.graphql import GraphQL

        return GraphQL.get_schema()

    @property
    def glossary(self) -> str:
        """Model-visible business definitions, without precomputed case answers."""
        text = (
            "Synthetic fixture clock: 2026-10-03T12:00:00Z; business calendar UTC. "
            "Next year is 2027. Last calendar year is [2025-01-01, 2026-01-01); "
            "last complete month is [2026-09-01, 2026-10-01). "
            "Complete actual years are 2023-2025; 2026 is YTD and is never silently annualized. "
            "Shipment quantities are gross integer pieces; returns and kg are separate. "
            "Materials expose family and density in g/cm3. "
            "Material.isActive=False means soft-deleted in this manager. "
            "Default Material queries exclude soft-deleted rows. "
            "Material.active describes business availability; "
            "active=False alone does not mark a material deleted. "
            "A request for non-deleted materials includes business-inactive rows "
            "whose isActive remains True. Inspect the actual root arguments for any includeInactive control; do not infer it from a business active field. "
            "Customer.active and Project.status are independent of material status. "
            "Revenue is net, excludes VAT, and is calculated from declared plan inputs; "
            "do not substitute cash receipts or invoices. Currency conversions require "
            "the exposed rate, reference date and source; round only the final Decimal value to 0.01. "
            "Rank descending by the requested metric, then ascending by project code; "
            "report missing values separately from zero. An existing plan is not a new forecast. "
            "The linear forecast uses OLS on complete 2023-2025 shipment actuals, "
            "clamped at zero; missing years prevent the reference forecast. "
            "Its +/-20% range is a scenario band, not a statistical confidence interval. "
        )
        if "ProjectCost" in self.managers:
            text += (
                "In this fixture, project costs last year means actual net costs in EUR "
                "dated in [2025-01-01, 2026-01-01), grouped by the linked WBS element "
                "when a breakdown is requested: W01 Engineering, W02 ProjectManagement. "
                "Exclude plan entries and out-of-period rows. No exchange rate is needed "
                "and no exchange rate may be invented. This convention is explicit, "
                "so those already-defined dimensions do not require clarification."
            )
        return text

    def tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Call the production tool dispatcher without changing its inputs/results."""
        from general_manager.chat.tools import ScopeChatContext, execute_chat_tool

        return execute_chat_tool(
            name, arguments, ScopeChatContext.from_scope(self.scope)
        )

    def census(self) -> dict[str, Any]:
        """Compare independent GM, GraphQL and indexed-schema exposed counts."""
        from general_manager.api.graphql import GraphQL
        from general_manager.chat.schema_index import build_schema_index
        from general_manager.interface import (
            CalculationInterface,
            DatabaseInterface,
            ReadOnlyInterface,
        )
        from general_manager.manager.meta import GeneralManagerMeta

        registered = [
            manager
            for manager in GeneralManagerMeta.all_classes
            if getattr(manager, "chat_exposed", False)
        ]
        index = build_schema_index()
        graphql_names = {
            name
            for name, manager in GraphQL.manager_registry.items()
            if getattr(manager, "chat_exposed", False)
        }
        names = {manager.__name__ for manager in registered}
        if (
            len(registered) != self.manager_count
            or names != set(index)
            or names != graphql_names
        ):
            raise FixtureInvalidError(
                "census_mismatch",
                {
                    "registered": len(registered),
                    "index": len(index),
                    "graphql": len(graphql_names),
                    "expected": self.manager_count,
                },
            )
        counts: Counter[str] = Counter()
        for manager in registered:
            interface = manager.Interface
            if issubclass(interface, ReadOnlyInterface):
                counts["readonly"] += 1
            elif issubclass(interface, DatabaseInterface):
                counts["database"] += 1
            elif issubclass(interface, CalculationInterface):
                counts["calculation"] += 1
            else:
                raise FixtureInvalidError("unexpected_interface", manager.__name__)
        expected = {
            "database": self.manager_count * 3 // 5,
            "readonly": self.manager_count // 5,
            "calculation": self.manager_count // 5,
        }
        if dict(counts) != expected:
            raise FixtureInvalidError(
                "interface_ratio_mismatch",
                {"actual": dict(counts), "expected": expected},
            )
        return {
            "registry_count": len(registered),
            "schema_count": len(index),
            "graphql_count": len(graphql_names),
            "interfaces": dict(counts),
            "manager_names": sorted(names),
            "registration_order": [manager.__name__ for manager in registered],
            "registry_hash": _hash(
                {name: self.managers[name].fixture_interface for name in sorted(names)}
            ),
        }

    def verify(self) -> dict[str, Any]:
        """Capture real tool evidence; expose unsupported routes as explicit gaps."""
        census = self.census()
        source_checks = self.source_integrity()
        probes: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        requests: list[tuple[str, str, dict[str, Any]]] = [
            (
                "customer",
                "query",
                {
                    "manager": "Customer",
                    "filters": {"code": "C01"},
                    "fields": ["code", "name", "aliases"],
                },
            ),
            (
                "database_relation",
                "query",
                {
                    "manager": "Project",
                    "filters": {"code": "P01"},
                    "fields": ["code", {"customer": ["code"]}],
                },
            ),
            (
                "readonly",
                "query",
                {
                    "manager": "Material",
                    "filters": {"code": "M01"},
                    "fields": ["code", "name", "aliases"],
                },
            ),
            (
                "calculation_relation",
                "query",
                {
                    "manager": "ProjectCommercial",
                    "filters": {"project": {"code": "P01"}},
                    "fields": ["year", "plannedRevenue", {"project": ["code"]}],
                },
            ),
            (
                "multiword_manager",
                "query",
                {
                    "manager": "ProjectCommercial",
                    "filters": {},
                    "fields": ["year", {"project": ["code"]}],
                },
            ),
            (
                "scalar_field_spelling",
                "query",
                {
                    "manager": "Material",
                    "filters": {"code": "M01"},
                    "fields": ["code", "densityGCm3"],
                },
            ),
            (
                "shipment_scalar_spelling",
                "query",
                {
                    "manager": "Shipment",
                    "filters": {
                        "shippedAt_Gte": "2026-09-01",
                        "shippedAt_Lt": "2026-10-01",
                    },
                    "fields": ["code", "shippedAt", "quantity", "source"],
                },
            ),
            (
                "material_path",
                "find_path",
                {"from_manager": "Project", "to_manager": "Material"},
            ),
            (
                "material_filter",
                "query",
                {
                    "manager": "Project",
                    "filters": {
                        "customer": {"code": "C01"},
                        "materialsList": {"any": {"code": "M01"}},
                    },
                    "fields": ["code"],
                },
            ),
            (
                "material_collection",
                "query",
                {
                    "manager": "Project",
                    "filters": {"code": "P01"},
                    "fields": [
                        "code",
                        {"materialsList": [{"items": ["code", "name"]}]},
                    ],
                },
            ),
        ]
        if "ShipmentPlan" in self.managers:
            requests.append(
                (
                    "readonly_database_relation",
                    "query",
                    {
                        "manager": "ShipmentPlan",
                        "filters": {
                            "project": {"code": "P01"},
                            "year": 2031,
                        },
                        "fields": ["year", "quantity", {"project": ["code"]}],
                    },
                )
            )
        if "ProjectCost" in self.managers:
            requests.append(
                (
                    "project_costs",
                    "query",
                    {
                        "manager": "ProjectCost",
                        "filters": {
                            "kind": "actual",
                            "costDate_Gte": "2025-01-01",
                            "costDate_Lt": "2026-01-01",
                        },
                        "fields": [
                            "code",
                            "kind",
                            "costDate",
                            "netAmount",
                            "currency",
                            {"project": ["code"]},
                            {"wbs": ["code", "name"]},
                        ],
                    },
                )
            )
        if "CustomerOutlook" in self.managers:
            requests.append(
                (
                    "forecast_relation",
                    "query",
                    {
                        "manager": "CustomerOutlook",
                        "filters": {"customer": {"code": "C01"}, "year": 2031},
                        "fields": [
                            "year",
                            "forecast",
                            "existingPlan",
                            "historyComplete",
                            {"customer": ["code"]},
                        ],
                    },
                )
            )
        if self.variant == "fx":
            requests.append(
                (
                    "fx_scalar_spelling",
                    "query",
                    {
                        "manager": "Project",
                        "filters": {"code": "P03"},
                        "fields": [
                            "code",
                            "currency",
                            "fxRate",
                            "fxAsOf",
                            "fxSource",
                        ],
                    },
                )
            )
        for label, tool_name, arguments in requests:
            record: dict[str, Any] = {
                "probe": label,
                "tool": tool_name,
                "arguments": arguments,
            }
            try:
                record["result"] = self.tool(tool_name, arguments)
                record["status"] = "ok"
                if tool_name == "find_path" and record["result"] is None:
                    record["status"] = "interface_capability_gap"
                    gaps.append(record.copy())
            except (ValueError, TypeError, NotImplementedError) as exc:
                record.update(
                    status="interface_capability_gap",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                gaps.append(record.copy())
            probes.append(record)
        from general_manager.chat.tools import ScopeChatContext

        query_text = '{ projectCommercialList(filter: {project: {code: "P01"}}) {items {year plannedRevenue project {code}}} projectList(filter: {customer: {code: "C01"}, materialsList: {any: {code: "M01"}}}) {items {code materialsList {items {code}}}} materialList(filter: {code: "M01"}) {items {code densityGCm3}} shipmentList(filter: {shippedAt_Gte: "2026-09-01", shippedAt_Lt: "2026-10-01"}) {items {code shippedAt quantity source}} }'
        if "ProjectCost" in self.managers:
            query_text = (
                query_text[:-1]
                + ' projectCostList(filter: {kind: "actual", costDate_Gte: "2025-01-01", costDate_Lt: "2026-01-01"}) {items {code costDate kind netAmount currency project {code} wbs {code name}}} }'
            )
        if "ShipmentPlan" in self.managers:
            query_text = (
                query_text[:-1]
                + ' shipmentPlanList(filter: {year: 2031}) {items {year quantity project {code customer {code}}}} customerOutlookList(filter: {customer: {code: "C01"}, year: 2031}) {items {year forecast existingPlan scenarioLow scenarioHigh historyComplete customer {code}}} }'
            )
        if self.variant == "fx":
            query_text = (
                query_text[:-1]
                + ' fxReference: projectList(filter: {code: "P03"}) {items {code currency fxRate fxAsOf fxSource}} }'
            )
        generated = self.schema.execute(
            query_text, context_value=ScopeChatContext.from_scope(self.scope)
        ).formatted
        if generated.get("errors"):
            raise FixtureInvalidError(
                "generated_schema_probe_failed", generated["errors"]
            )
        return {
            "census": census,
            "source_checks": source_checks,
            "probes": probes,
            "gaps": gaps,
            "generated_schema_probe": {"query": query_text, "result": generated},
            "seed_hash": _hash(self.seed_data),
            "schema_hash": _hash(self.schema_index),
            "catalog_hash": _hash(self.catalog),
            "snapshot": self.snapshot,
            "variant": self.variant,
            "seed": self.seed,
            "clock": FROZEN_CLOCK,
            "glossary": self.glossary,
            "startup_managers": self.startup_managers,
        }

    def source_integrity(self) -> dict[str, Any]:
        """Compare installed rows and real relation identities with source tables."""
        names = {
            "customer": "Customer",
            "project": "Project",
            "material": "Material",
            "shipment": "Shipment",
            "shipment_plan": "ShipmentPlan",
            "part": "Part",
            "project_part": "ProjectPart",
            "invoice": "Invoice",
            "supplier": "Supplier",
            "shipment_return": "ShipmentReturn",
            "project_dependency": "ProjectDependency",
            "wbs_element": "WbsElement",
            "project_cost": "ProjectCost",
        }
        money_fields = {
            "plan_units",
            "net_price",
            "kg_per_piece",
            "fx_rate",
            "net_amount",
            "density_g_cm3",
        }
        row_counts: dict[str, int] = {}
        for table, manager_name in names.items():
            if manager_name not in self.managers:
                continue
            expected = {row["code"]: row for row in self.seed_data[table]}
            actual = {row.code: row for row in self.managers[manager_name].all()}
            if actual.keys() != expected.keys():
                raise FixtureInvalidError(
                    "source_codes_mismatch",
                    {
                        "table": table,
                        "actual": sorted(actual),
                        "expected": sorted(expected),
                    },
                )
            row_counts[manager_name] = len(actual)
            for code, source in expected.items():
                instance = actual[code]
                for key, wanted in source.items():
                    observed: Any
                    if key == "supplier_code" and "Supplier" not in self.managers:
                        continue
                    if key == "material_codes":
                        observed = sorted(row.code for row in instance.materials_list)
                        wanted = sorted(wanted)
                    elif key.endswith("_code"):
                        relation = key.removesuffix("_code")
                        related = getattr(instance, relation)
                        observed = related.code if related is not None else None
                    else:
                        observed = getattr(instance, key)
                        if key == "aliases":
                            observed = observed.split(" | ") if observed else []
                        elif key in money_fields:
                            wanted = Decimal(wanted) if wanted is not None else None
                        elif isinstance(observed, date):
                            observed = observed.isoformat()
                    if observed != wanted:
                        raise FixtureInvalidError(
                            "source_value_mismatch",
                            {
                                "table": table,
                                "code": code,
                                "field": key,
                                "actual": str(observed),
                                "expected": str(wanted),
                            },
                        )
        bounded = {"ProjectCommercial": len(self.seed_data["project"])}
        if "CustomerOutlook" in self.managers:
            bounded["CustomerOutlook"] = len(self.seed_data["customer"]) * 5
        for block in self.blocks:
            for name in block["readonly"]:
                expected_rows = (
                    len(self.seed_data["wbs_element"]) if name == "WbsElement" else 1
                )
                if self.managers[name].all().count() != expected_rows:
                    raise FixtureInvalidError("distractor_reference_missing", name)
            for name in block["database"]:
                rows = list(self.managers[name].all())
                if not rows:
                    raise FixtureInvalidError("distractor_data_missing", name)
                if name in {
                    "Supplier",
                    "ProjectDependency",
                    "ShipmentReturn",
                    "ProjectCost",
                }:
                    continue
                if any(
                    not row.category.code
                    or row.project.code != "P01"
                    or row.customer.code != "C01"
                    for row in rows
                ):
                    raise FixtureInvalidError("distractor_anchor_mismatch", name)
            bounded.update({name: 1 for name in block["calculation"]})
        for name, expected_size in bounded.items():
            actual_size = len(list(self.managers[name].all()))
            if actual_size != expected_size:
                raise FixtureInvalidError(
                    "calculation_domain_mismatch",
                    {"manager": name, "actual": actual_size, "expected": expected_size},
                )
        return {
            "row_counts": row_counts,
            "calculation_domain_sizes": bounded,
            "distractor_blocks": len(self.blocks),
        }

    def close(self) -> None:
        """Close all local DB connections and remove only an owned temp database."""
        from django.db import connections

        connections.close_all()
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None


def bootstrap(
    manager_count: int = 10,
    snapshot: str = "RANK",
    variant: str = "base",
    seed: int = 17,
    database_path: str | Path | None = None,
) -> FixtureRuntime:
    """Start a fresh local fixture through normal Django app discovery.

    Run each bootstrap in its own subprocess. An already configured Django
    process is rejected instead of resetting shared registries or settings.
    """
    from django.conf import settings

    if manager_count not in MANAGER_COUNTS:
        raise FixtureConfigurationError("manager_count", manager_count, MANAGER_COUNTS)
    build_seed(snapshot, variant)
    if (
        variant in {"supplier", "cycle", "returns", "project_costs"}
        and manager_count < 50
    ):
        raise FixtureConfigurationError(
            "variant", variant, "manager_count >= 50 with a replacement distractor slot"
        )
    if settings.configured:
        raise FixtureInvalidError(
            "already_configured", "bootstrap requires a fresh subprocess"
        )
    temporary = TemporaryDirectory(prefix="gm-eval-") if database_path is None else None
    path = (
        Path(temporary.name) / "fixture.sqlite3"
        if temporary is not None
        else Path(str(database_path))
    )
    if path.exists():
        raise FixtureInvalidError("existing_database", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    settings.configure(
        SECRET_KEY=token_hex(32),
        DEBUG=False,
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver", "localhost"],
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(path)}
        },
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        },
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "general_manager.apps.GeneralmanagerConfig",
            "experiments.gm_eval.fixture_app.apps.FixtureAppConfig",
        ],
        ROOT_URLCONF="experiments.gm_eval.fixture_app.urls",
        MIDDLEWARE=[],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        MIGRATION_MODULES={"gm_eval_fixture": None},
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        GM_EVAL_FIXTURE={
            "manager_count": manager_count,
            "snapshot": snapshot,
            "variant": variant,
            "seed": seed,
        },
        GENERAL_MANAGER={
            "AUTOCREATE_GRAPHQL": True,
            "VALIDATE_INPUT_VALUES": True,
            "CHAT": {
                "enabled": False,
                "allowed_mutations": [],
                "max_results": 2 if variant == "page2" else 200,
            },
        },
    )
    import django

    django.setup()
    from django.contrib.auth.models import AnonymousUser
    from django.core.management import call_command
    from general_manager.chat.schema_index import build_schema_index
    from experiments.gm_eval.fixture_app import managers as fixture

    call_command("migrate", run_syncdb=True, verbosity=0, interactive=False)
    startup = _seed_database(fixture)
    runtime = FixtureRuntime(
        manager_count=manager_count,
        snapshot=snapshot,
        variant=variant,
        seed=seed,
        database_path=str(path),
        managers=fixture.MANAGERS,
        schema_index=build_schema_index(),
        scope={
            "user": AnonymousUser(),
            "session_key": f"gm-eval-{seed}",
            "type": "websocket",
        },
        catalog=fixture.MANAGER_CATALOG,
        seed_data=fixture.SEED_DATA,
        startup_managers=startup,
        blocks=fixture.BLOCKS,
        _temporary_directory=temporary,
    )
    runtime.census()
    return runtime
