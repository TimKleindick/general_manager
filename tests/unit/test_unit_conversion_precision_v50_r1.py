"""Products retain exact residuals through GraphQL, evidence and derivation."""

from decimal import Decimal, localcontext
from itertools import permutations
import json
import os
from pathlib import Path
from typing import Any

import pytest
from graphql import graphql_sync

from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import EvidenceRequirement, SchemaBinding
from tests.unit.test_graphql_unit_contract_v50 import (
    unit_schema as _unit_schema_fixture,
)
from tests.unit.test_unit_conversion_v50 import _setup

unit_schema = _unit_schema_fixture
ORDERS = list(permutations(range(3)))
PAIRS = [(1e20, 1e20), (1e-20, 1e-20), (-1e20, 1e20)]


@pytest.mark.parametrize("order", ORDERS)
def test_all_product_orders_preserve_small_residual(order: tuple[int, ...]) -> None:
    values = [value for index in order for value in PAIRS[index]]
    assert calculate("sum_products", values) == Decimal("1E-40")


@pytest.mark.parametrize("precision", [2, 28, 80])
@pytest.mark.parametrize(
    "large,small,residual",
    [
        ("1E20", "1E-20", "1E-40"),
        ("1.23456789E100", "1E-100", "1E-200"),
        ("99999E250", "3E-250", "9E-500"),
    ],
)
def test_product_coefficients_and_span_are_independent_of_ambient_precision(
    large: str, small: str, residual: str, precision: int
) -> None:
    with localcontext() as context:
        context.prec = precision
        assert calculate(
            "sum_products", [large, large, small, small, "-" + large, large]
        ) == Decimal(residual)


def _native_population(fixture: Any, order: tuple[int, ...]) -> Any:
    old, binding, _ = _setup(fixture)
    rows = [
        {
            "id": f"q{index}",
            "amount": PAIRS[index][0],
            "unitLabel": "widgets",
            "other": {"id": f"o{index}", "rate": PAIRS[index][1]},
        }
        for index in order
    ]
    fixture[0].query_type.fields["itemList"].resolve = lambda _root, _info: {
        "items": rows,
        "pageInfo": {"totalCount": 3},
    }
    query = "{ itemList { items { id amount unitLabel other { id rate } } pageInfo { totalCount } } }"
    native = graphql_sync(fixture[0], query)
    assert native.errors is None and native.data is not None
    observed = native.data["itemList"]
    assert all(
        type(row["amount"]) is float and type(row["other"]["rate"]) is float
        for row in observed["items"]
    )
    store = EvidenceStore()
    for eid, rid, manager in [
        ("sq", "quantity_schema", "Item"),
        ("sf", "factor_schema", "Other"),
    ]:
        record = old.get(eid)
        assert record is not None
        store.observe_schema("t", record.payload())
        store.add(
            record,
            requirement=EvidenceRequirement(
                rid,
                "schema",
                "Actual public dimensions and identities",
                None,
                schema=SchemaBinding(manager, "overview", (), "current"),
            ),
        )
    args = {
        "manager": "Item",
        "fields": ["id", "amount", "unitLabel", {"other": ["id", "rate"]}],
    }
    payload = {
        "data": observed["items"],
        "complete": True,
        "has_more": False,
        "total_count": observed["pageInfo"]["totalCount"],
    }
    if directory := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / ("precision-native-" + "".join(map(str, order)) + ".json")).write_text(
            json.dumps(
                {
                    "graphql_request": query,
                    "graphql_response": native.data,
                    "canonical_query_args": args,
                    "normalized_complete_population": payload,
                    "schema_sources": [
                        {
                            "evidence_id": record.evidence_id,
                            "task_id": record.task_id,
                            "call_identity": record.call_identity,
                            "provenance": dict(record.provenance),
                            "payload_json": record.payload_json,
                        }
                        for record in store.for_task("t")
                    ],
                    "binding": binding.as_mapping(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    record = EvidenceRecord.create(
        "q",
        "t",
        "query",
        canonical_call_identity("query", args),
        {"manager": "Item", "tool": "query"},
        payload,
    )
    store.add(
        record,
        requirement=EvidenceRequirement(
            "population", "query", "Complete actual GraphQL Float population", None
        ),
    )
    operands = [
        CalculationOperand("q", ("data", index, *path))
        for index in range(3)
        for path in [("amount",), ("other", "rate")]
    ]
    return store, binding, operands


@pytest.mark.parametrize("order", ORDERS)
def test_native_float_evidence_and_transitive_recheck_preserve_residual(
    unit_schema: Any, order: tuple[int, ...]
) -> None:
    store, binding, operands = _native_population(unit_schema, order)
    result = calculate_evidence(
        "converted",
        "t",
        "sum_products",
        operands,
        store,
        binding=binding,
        require_linked=True,
    )
    payload = result.payload()
    assert Decimal(str(payload["value"])) == Decimal("1E-40")
    assert payload["scope"]["unit"] == "gram"
    assert payload["scope"]["population_complete"] is True
    assert len(payload["scope"]["conversion"]["population_ids"]) == 3
    store.add(
        result,
        requirement=EvidenceRequirement(
            "converted",
            "calculation",
            "Exact conversion",
            "sum_products",
            binding=binding,
        ),
    )
    derived = calculate_evidence(
        "derived",
        "t",
        "sum",
        [CalculationOperand("converted", ("value",))],
        store,
        require_linked=True,
    )
    assert Decimal(str(derived.payload()["value"])) == Decimal("1E-40")


def test_previously_rounded_zero_cannot_pass_transitive_recomputation(
    unit_schema: Any,
) -> None:
    store, binding, operands = _native_population(unit_schema, (0, 1, 2))
    result = calculate_evidence(
        "converted",
        "t",
        "sum_products",
        operands,
        store,
        binding=binding,
        require_linked=True,
    )
    payload = result.payload()
    payload["value"] = 0
    stale = EvidenceRecord.create(
        result.evidence_id,
        result.task_id,
        result.kind,
        result.call_identity,
        result.provenance,
        payload,
    )
    store.add(
        stale,
        requirement=EvidenceRequirement(
            "converted",
            "calculation",
            "Stored result must recompute exactly",
            "sum_products",
            binding=binding,
        ),
    )
    with pytest.raises(CalculationError):
        calculate_evidence(
            "derived",
            "t",
            "sum",
            [CalculationOperand("converted", ("value",))],
            store,
            require_linked=True,
        )
