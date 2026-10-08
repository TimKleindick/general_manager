"""Real fixture checks run Django once per subprocess, never patch its schema."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

import pytest


def _seed(
    snapshot: str = "RANK", variant: str = "base"
) -> dict[str, list[dict[str, Any]]]:
    module = importlib.util.find_spec("experiments.gm_eval.seeds")
    assert module is not None, "The independent source seed tables must exist"
    return cast(
        dict[str, list[dict[str, Any]]],
        importlib.import_module("experiments.gm_eval.seeds").build_seed(
            snapshot, variant
        ),
    )


def _subprocess(code: str) -> dict[str, Any]:
    stage_root = str(Path(__file__).resolve().parents[2])
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [stage_root, environment.get("PYTHONPATH", "")]
    )
    process = subprocess.run(  # noqa: S603 - fixed test-owned Python snippets, no shell.
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return cast(
        dict[str, Any], json.loads(process.stdout.rsplit("GM_EVAL_RESULT=", 1)[1])
    )


def test_source_seeds_keep_rank_and_trend_snapshots_distinct() -> None:
    rank = _seed()
    trend = _seed("TREND")
    rank_totals = {
        code: sum(
            row["quantity"]
            for row in rank["shipment"]
            if row["project_code"] == code and row["shipped_at"].startswith("2025")
        )
        for code in [f"P{number:02}" for number in range(1, 9)]
    }
    assert list(rank_totals.values()) == [120, 500, 200, 150, 80, 60, 60, 70]
    ownership = {row["code"]: row["customer_code"] for row in trend["project"]}
    for customer, expected in {
        "C01": [100, 120, 140],
        "C02": [300, 280, 260],
        "C03": [50, 55, 60],
    }.items():
        assert [
            sum(
                row["quantity"]
                for row in trend["shipment"]
                if ownership[row["project_code"]] == customer
                and row["shipped_at"].startswith(str(year))
            )
            for year in (2023, 2024, 2025)
        ] == expected
    assert all(isinstance(row["net_price"], str) for row in rank["project"])
    assert any(row["code"] == "P11" for row in rank["project"])
    assert [
        row["code"] for row in rank["project"] if row["customer_code"] == "C03"
    ] == ["P08"]


def test_seed_variants_preserve_missing_zero_and_counterexamples() -> None:
    missing = _seed(variant="missing_revenue")
    projects = {row["code"]: row for row in missing["project"]}
    assert projects["P02"]["plan_units"] is None
    assert projects["P08"]["plan_units"] == "0"
    disconnected = _seed(variant="disconnected")
    unrelated = next(row for row in disconnected["project"] if row["code"] == "P99")
    assert unrelated["customer_code"] == "C02"
    assert unrelated["material_codes"] == ["M99"]
    history = _seed("TREND", "missing_history")
    nova = {row["code"] for row in history["project"] if row["customer_code"] == "C03"}
    assert not any(
        row["project_code"] in nova and row["shipped_at"].startswith("2024")
        for row in history["shipment"]
    )
    assert _seed() == _seed()
    with pytest.raises(ValueError):
        _seed("unknown")
    with pytest.raises(ValueError):
        _seed(variant="unknown")
    fx_project = next(
        row for row in _seed(variant="fx")["project"] if row["code"] == "P03"
    )
    assert "fx_source" in fx_project
    assert fx_project["fx_source"] == "frozen synthetic USD/EUR reference"


@pytest.mark.parametrize("manager_count", [5, 10, 50, 100, 250])
def test_normal_registry_schema_counts_and_mixed_query(manager_count: int) -> None:
    result = _subprocess(
        f"""
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(manager_count={manager_count})
payload = r.tool('query', {{'manager': 'Project', 'filters': {{'code': 'P01'}},
    'fields': ['code', {{'customer': ['code', 'name']}}]}})
from general_manager.chat.tools import ScopeChatContext
calculated = r.schema.execute('''{{ projectCommercialList(filter: {{project: {{code: "P01"}}}}) {{
    items {{ year plannedRevenue project {{code}} }} }} }}''',
    context_value=ScopeChatContext.from_scope(r.scope)).formatted
print('GM_EVAL_RESULT=' + json.dumps({{'census': r.census(), 'query': payload,
    'calculated': calculated, 'schema': r.schema_index}}))
r.close()
"""
    )
    census = result["census"]
    assert census["registry_count"] == census["schema_count"] == manager_count
    assert census["interfaces"] == {
        "database": manager_count * 3 // 5,
        "readonly": manager_count // 5,
        "calculation": manager_count // 5,
    }
    assert "errors" not in result["query"], result["query"]
    assert result["query"]["data"][0]["customer"]["code"] == "C01"
    assert "errors" not in result["calculated"], result["calculated"]
    assert (
        result["calculated"]["data"]["projectCommercialList"]["items"][0][
            "plannedRevenue"
        ]
        == 12000.0
    )


def test_native_chat_tools_execute_generated_schema_without_replacement() -> None:
    result = _subprocess(
        """
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap()
print('GM_EVAL_RESULT=' + json.dumps(r.verify()))
r.close()
"""
    )
    assert any(
        row["probe"] == "database_relation" and row["status"] == "ok"
        for row in result["probes"]
    )
    assert result["gaps"] == []
    assert all(row["status"] == "ok" for row in result["probes"])


def test_real_readonly_startup_protection_and_calculation_domains() -> None:
    result = _subprocess(
        """
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(snapshot='TREND')
material = r.managers['Material'].filter(code='M01').first()
blocked = False
try:
    material.update(name='changed', ignore_permission=True)
except NotImplementedError:
    blocked = True
customer = r.managers['Customer'].filter(code='C01').first()
rows = list(r.managers['CustomerOutlook'].filter(customer=customer))
print('GM_EVAL_RESULT=' + json.dumps({'blocked': blocked,
    'materials': r.managers['Material'].all().count(),
    'years': [row.year for row in rows],
    'forecast': [str(row.forecast) for row in rows],
    'plans': r.managers['ShipmentPlan'].all().count()}))
r.close()
"""
    )
    assert result["blocked"]
    assert result["materials"] == 5
    assert result["years"] == [2027, 2028, 2029, 2030, 2031]
    assert result["forecast"] == ["180.00", "200.00", "220.00", "240.00", "260.00"]
    assert result["plans"] >= 15


def test_material_current_scope_and_glossary_preserve_business_inactive_rows() -> None:
    result = _subprocess(
        """
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap()
from general_manager.interface.capabilities.orm.support import is_soft_delete_enabled
material = r.managers['Material']
model = material.Interface._model
steel = r.tool('query', {'manager': 'Material', 'filters': {'family': 'steel'},
    'fields': ['code', 'name', 'active']})
available = r.tool('query', {'manager': 'Material',
    'filters': {'family': 'steel', 'active': True}, 'fields': ['code']})
legacy = model.all_objects.get(code='M05')
print('GM_EVAL_RESULT=' + json.dumps({'steel': steel, 'available': available,
    'legacy': {'active': legacy.active, 'is_active': legacy.is_active},
    'soft_delete_enabled': is_soft_delete_enabled(material.Interface),
    'deleted_count': model.all_objects.filter(is_active=False).count(),
    'deleted_history_count': model.history.filter(is_active=False).count(),
    'glossary': r.glossary}))
r.close()
"""
    )
    assert result["soft_delete_enabled"]
    assert [row["code"] for row in result["steel"]["data"]] == [
        "M02",
        "M04",
        "M05",
    ]
    assert result["steel"]["has_more"] is False
    assert [row["code"] for row in result["available"]["data"]] == ["M02", "M04"]
    assert result["legacy"] == {"active": False, "is_active": True}
    assert result["deleted_count"] == result["deleted_history_count"] == 0
    assert "Material.isActive=False means soft-deleted" in result["glossary"]
    assert "Default Material queries exclude soft-deleted rows" in result["glossary"]
    assert "Material.active describes business availability" in result["glossary"]
    assert "active=False alone does not mark a material deleted" in result["glossary"]
    assert (
        "Inspect the actual root arguments for any includeInactive control"
        in result["glossary"]
    )


def test_material_soft_delete_uses_existing_readonly_sync_in_disposable_database() -> (
    None
):
    result = _subprocess(
        """
import copy
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap()
from general_manager.chat.tools import ScopeChatContext
from general_manager.utils.testing import run_registered_startup_hooks
material = r.managers['Material']
model = material.Interface._model
original = copy.deepcopy(material._data)
source_before = copy.deepcopy(r.seed_data)
material._data = [row for row in original if row['code'] != 'M05']
run_registered_startup_hooks(interfaces=[material.Interface])
legacy = model.all_objects.get(code='M05')
current = r.tool('query', {'manager': 'Material', 'filters': {'family': 'steel'},
    'fields': ['code', 'active']})
schema_rows = r.schema.execute('''{materialList(includeInactive: true,
    filter: {family: "steel"}) {items {code active isActive}}}''',
    context_value=ScopeChatContext.from_scope(r.scope)).formatted
try:
    r.tool('query', {'manager': 'Material',
        'filters': {'family': 'steel', 'include_inactive': True}, 'fields': ['code']})
except ValueError as error:
    chat_include_error = str(error)
else:
    chat_include_error = None
out = {'current': current, 'schema_rows': schema_rows,
    'legacy': {'active': legacy.active, 'is_active': legacy.is_active},
    'python_include': [row.code for row in material.filter(include_inactive=True, family='steel')],
    'soft_deleted_history_count': model.history.filter(code='M05', is_active=False).count(),
    'chat_include_error': chat_include_error}
material._data = original
run_registered_startup_hooks(interfaces=[material.Interface])
out['restored'] = r.tool('query', {'manager': 'Material', 'filters': {'family': 'steel'},
    'fields': ['code', 'active']})
out['source_unchanged'] = source_before == r.seed_data
print('GM_EVAL_RESULT=' + json.dumps(out))
r.close()
"""
    )
    assert result["legacy"] == {"active": False, "is_active": False}
    assert result["soft_deleted_history_count"] > 0
    assert [row["code"] for row in result["current"]["data"]] == ["M02", "M04"]
    assert result["python_include"] == ["M02", "M04", "M05"]
    assert "errors" not in result["schema_rows"], result["schema_rows"]
    assert result["schema_rows"]["data"]["materialList"]["items"] == [
        {"code": "M02", "active": True, "isActive": True},
        {"code": "M04", "active": True, "isActive": True},
        {"code": "M05", "active": False, "isActive": False},
    ]
    assert "include_inactive" in result["chat_include_error"]
    assert [row["code"] for row in result["restored"]["data"]] == [
        "M02",
        "M04",
        "M05",
    ]
    assert result["source_unchanged"]


@pytest.mark.parametrize(
    "variant",
    [
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
    ],
)
def test_all_variants_keep_counts_and_real_relation_integrity(variant: str) -> None:
    snapshot = "TREND" if variant == "missing_history" else "RANK"
    result = _subprocess(
        f"""
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(manager_count=50, snapshot={snapshot!r}, variant={variant!r})
for row in r.managers['Project'].all():
    assert row.customer.code
for row in r.managers['Part'].all():
    assert row.material.code
for row in r.managers['ProjectPart'].all():
    assert row.project.code and row.part.code
for block in r.blocks:
    assert all(r.managers[name].all().count() >= 1 for name in block['readonly'])
    for name in block['database']:
        rows = list(r.managers[name].all())
        assert rows
        if hasattr(rows[0], 'category'):
            assert rows[0].category.code
            assert rows[0].project.code and rows[0].customer.code
    assert all(len(list(r.managers[name].all())) == 1 for name in block['calculation'])
out = {{'census': r.census()}}
if {variant!r} == 'supplier':
    out['nord_parts'] = [row.code for row in r.managers['Part'].filter(supplier__name='Nord')]
if {variant!r} == 'cycle':
    out['edges'] = sorted((row.source_project.code, row.target_project.code) for row in r.managers['ProjectDependency'].all())
if {variant!r} == 'missing_history':
    customer = r.managers['Customer'].filter(code='C03').first()
    out['missing'] = [row.forecast for row in r.managers['CustomerOutlook'].filter(customer=customer)]
print('GM_EVAL_RESULT=' + json.dumps(out))
r.close()
"""
    )
    assert result["census"]["registry_count"] == result["census"]["schema_count"] == 50
    if variant == "supplier":
        assert result["nord_parts"] == ["N1", "N2"]
    elif variant == "cycle":
        assert result["edges"] == [
            ["P01", "P02"],
            ["P02", "P03"],
            ["P02", "P04"],
            ["P03", "P01"],
        ]
    elif variant == "missing_history":
        assert result["missing"] == [None] * 5


def test_currency_rounding_happens_after_conversion_and_tools_cannot_write() -> None:
    result = _subprocess(
        """
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(manager_count=5)
project_model = r.managers['Project'].Interface._model
project_model.objects.filter(code='P01').update(plan_units='1', net_price='0.016', fx_rate='0.3', currency='USD')
p = r.managers['Project'].filter(code='P01').first()
c = r.managers['ProjectCommercial'](project=p, year=2027)
blocked = False
try:
    r.tool('mutate', {'mutation':'createCustomer', 'input':{'name':'not allowed'}})
except ValueError:
    blocked = True
bounded = False
try:
    r.managers['ProjectCommercial'](project=p, year=2032)
except ValueError:
    bounded = True
print('GM_EVAL_RESULT=' + json.dumps({'eur':str(c.revenue_eur), 'blocked':blocked, 'bounded':bounded}))
r.close()
"""
    )
    assert result == {"eur": "0.00", "blocked": True, "bounded": True}


def test_page_cap_and_seeded_positions_are_reproducible() -> None:
    outputs = []
    for seed in (17, 17, 42):
        outputs.append(
            _subprocess(
                f"""
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(manager_count=10, variant='page2', seed={seed})
first = r.tool('query', {{'manager':'Project', 'fields':['code'], 'limit':50}})
second = r.tool('query', {{'manager':'Project', 'fields':['code'], 'offset':2, 'limit':50}})
print('GM_EVAL_RESULT=' + json.dumps({{'first':first, 'second':second, 'order':r.census()['registration_order'], 'catalog':list(r.catalog)}}))
r.close()
"""
            )
        )
    assert outputs[0] == outputs[1]
    assert outputs[0]["order"] != outputs[2]["order"]
    assert outputs[0]["catalog"] != outputs[2]["catalog"]
    assert len(outputs[0]["first"]["data"]) == len(outputs[0]["second"]["data"]) == 2
    assert outputs[0]["first"]["has_more"]
    assert outputs[0]["first"]["data"] != outputs[0]["second"]["data"]


def test_verification_rejects_installed_source_corruption() -> None:
    result = _subprocess(
        """
import json
from experiments.gm_eval.runtime import bootstrap, FixtureInvalidError
r = bootstrap()
r.managers['Shipment'].Interface._model.objects.filter(code='SH0001').update(quantity=9999)
rejected = False
try:
    r.verify()
except FixtureInvalidError:
    rejected = True
print('GM_EVAL_RESULT=' + json.dumps({'rejected': rejected}))
r.close()
"""
    )
    assert result["rejected"]


def test_real_forecast_decline_scenarios_and_opportunity_gaps() -> None:
    result = _subprocess(
        """
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(snapshot='TREND')
rows = list(r.managers['CustomerOutlook'].filter(year=2031))
result = {row.customer.code: {'forecast':str(row.forecast), 'low':str(row.scenario_low),
    'high':str(row.scenario_high), 'plan':row.existing_plan, 'gap':str(row.opportunity_gap)} for row in rows}
print('GM_EVAL_RESULT=' + json.dumps(result))
r.close()
"""
    )
    assert result == {
        "C01": {
            "forecast": "260.00",
            "low": "208.00",
            "high": "312.00",
            "plan": 190,
            "gap": "70.00",
        },
        "C02": {
            "forecast": "140.00",
            "low": "112.00",
            "high": "168.00",
            "plan": 240,
            "gap": "-100.00",
        },
        "C03": {
            "forecast": "90.00",
            "low": "72.00",
            "high": "108.00",
            "plan": 70,
            "gap": "20.00",
        },
    }


def test_expanded_source_masterdata_and_cost_periods() -> None:
    seed = _seed()
    assert all("active" in row for row in seed["customer"])
    assert [row["code"] for row in seed["customer"] if row["active"]] == ["C01", "C02"]
    assert [
        row["code"]
        for row in seed["material"]
        if row["family"] == "steel" and row["active"]
    ] == ["M02", "M04"]
    assert [row["code"] for row in seed["project"] if row["status"] == "ongoing"] == [
        "P01",
        "P03",
        "P05",
    ]
    costs = _seed(variant="project_costs")
    from decimal import Decimal

    actual = [
        row
        for row in costs["project_cost"]
        if row["kind"] == "actual" and "2025-01-01" <= row["cost_date"] < "2026-01-01"
    ]
    assert sum(
        Decimal(row["net_amount"]) for row in actual if row["wbs_code"] == "W01"
    ) == Decimal("3500")
    assert sum(
        Decimal(row["net_amount"]) for row in actual if row["wbs_code"] == "W02"
    ) == Decimal("1200")
    assert sum(
        Decimal(row["net_amount"]) for row in actual if row["project_code"] == "P01"
    ) == Decimal("2500")
    recent = _seed(variant="recent_shipments")
    assert (
        sum(
            row["quantity"]
            for row in recent["shipment"]
            if row["project_code"] == "P01"
            and "2026-09-01" <= row["shipped_at"] < "2026-10-01"
        )
        == 30
    )


@pytest.mark.parametrize(
    "variant, count", [("project_costs", 50), ("recent_shipments", 10)]
)
def test_expanded_fixtures_real_exposure_and_counts(variant: str, count: int) -> None:
    result = _subprocess(f"""
import json
from experiments.gm_eval.runtime import bootstrap
r = bootstrap(manager_count={count}, variant={variant!r})
from general_manager.chat.tools import ScopeChatContext
queries = {{
    'project_costs': '{{projectCostList(filter: {{kind: "actual", costDate_Gte: "2025-01-01", costDate_Lt: "2026-01-01"}}) {{items {{code netAmount wbs {{code name}} project {{code}}}}}}}}',
    'recent_shipments': '{{shipmentList(filter: {{shippedAt_Gte: "2026-09-01", shippedAt_Lt: "2026-10-01"}}) {{items {{quantity project {{code}}}}}}}}',
}}
data = r.schema.execute(queries[{variant!r}], context_value=ScopeChatContext.from_scope(r.scope)).formatted
steel = r.tool('query', {{'manager':'Material', 'filters':{{'family':'steel', 'active':True}}, 'fields':['code','name','active']}})
print('GM_EVAL_RESULT=' + json.dumps({{'census':r.census(), 'data':data, 'steel':steel, 'integrity':r.source_integrity(), 'glossary':r.glossary}}))
r.close()
""")
    assert (
        result["census"]["registry_count"] == result["census"]["schema_count"] == count
    )
    assert "errors" not in result["data"], result["data"]
    assert [row["code"] for row in result["steel"]["data"]] == ["M02", "M04"]
    assert "2026-10-03" in result["glossary"]
    if variant == "project_costs":
        assert "actual net costs in EUR" in result["glossary"]
        assert (
            sum(
                row["netAmount"]
                for row in result["data"]["data"]["projectCostList"]["items"]
            )
            == 4700
        )
    else:
        assert result["data"]["data"]["shipmentList"]["items"] == [
            {"quantity": 30, "project": {"code": "P01"}}
        ]
