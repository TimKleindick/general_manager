"""Machine-checkable schema scope and fragment/snapshot completion controls."""

import pytest

from general_manager.chat.planned.models import EvidenceRequirement
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    IncompatibleEvidenceError,
)
from general_manager.chat.planned.validation import validate_plan, PlanValidationError

A = "a" * 64
B = "b" * 64


def requirement(view="detail", names=None, snapshot="current", manager="Part"):
    payload = {
        "intent": "read",
        "tasks": [
            {
                "task_id": "t",
                "objective": "Inspect relevant schema",
                "depends_on": [],
                "requirements": [
                    {
                        "requirement_id": "s",
                        "kind": "schema",
                        "description": "Not an authority hint",
                        "operation": None,
                        "schema": {
                            "manager": manager,
                            "view": view,
                            "types": ["Filter", "State"] if names is None else names,
                            "snapshot": snapshot,
                        },
                    }
                ],
                "completion_criteria": ["s"],
                "routing_features": [],
            }
        ],
    }
    return validate_plan(payload).tasks[0].requirements[0]


def record(eid, view="detail", names=("Filter",), snapshot=A, manager="Part"):
    return EvidenceRecord.create(
        eid,
        "t",
        "schema",
        "same-call",
        {"manager": manager, "schema_view": view, "snapshot": snapshot},
        {
            "manager": manager,
            "schema_view": view,
            "snapshot": snapshot,
            "schema_complete": view == "full",
            "types": {name: {"kind": "input"} for name in names},
        },
    )


def add_observed(store, item, *, requirement=None):
    store.observe_schema(item.task_id, item.payload())
    return store.add(item, requirement=requirement)


def test_legacy_requirement_cannot_be_satisfied_by_overview_or_detail():
    req = EvidenceRequirement("s", "schema", "Inspect Part filters", None)
    store = EvidenceStore()
    for view in ("overview", "detail"):
        with pytest.raises(IncompatibleEvidenceError):
            add_observed(store, record(view, view), requirement=req)
    add_observed(store, record("full", "full"), requirement=req)
    assert store.for_requirement("t", req)
    legacy = EvidenceRecord.create(
        "legacy", "t", "schema", "old-call", {}, {"fields": ["code"]}
    )
    add_observed(store, legacy, requirement=req)
    assert legacy in store.for_requirement("t", req)


def test_detail_fragments_require_complete_coverage_at_current_snapshot():
    req = requirement()
    assert req.schema.as_mapping() == {
        "manager": "Part",
        "view": "detail",
        "types": ["Filter", "State"],
        "snapshot": "current",
    }
    store = EvidenceStore()
    add_observed(store, record("f"), requirement=req)
    assert store.for_requirement("t", req) == ()
    add_observed(store, record("e", names=("State",)), requirement=req)
    assert {r.evidence_id for r in store.for_requirement("t", req)} == {"f", "e"}
    add_observed(store, record("changed", "overview", snapshot=B))
    assert store.for_requirement("t", req) == ()
    add_observed(store, record("new-filter", snapshot=B), requirement=req)
    assert store.for_requirement("t", req) == ()
    add_observed(
        store, record("new-enum", names=("State",), snapshot=B), requirement=req
    )
    assert {r.evidence_id for r in store.for_requirement("t", req)} == {
        "new-filter",
        "new-enum",
    }
    store.invalidate_schema("t", "Part")
    assert store.for_requirement("t", req) == ()
    store.observe_schema("t", record("observed", "overview", snapshot=B).payload())
    assert store.for_requirement("t", req)


def test_manager_view_and_literal_snapshot_are_binding_constraints():
    req = requirement("overview", [], A)
    store = EvidenceStore()
    for item in (
        record("other", "overview", manager="Other"),
        record("detail"),
        record("wrong", "overview", snapshot=B),
    ):
        with pytest.raises(IncompatibleEvidenceError):
            add_observed(store, item, requirement=req)
    add_observed(store, record("ok", "overview"), requirement=req)
    assert store.for_requirement("t", req)
    add_observed(store, record("new", "overview", snapshot=B))
    assert store.for_requirement("t", req) == ()
    full_req = requirement("detail", ["State"], B)
    add_observed(
        store, record("full", "full", ("Filter", "State"), B), requirement=full_req
    )
    assert store.for_requirement("t", full_req)
    with pytest.raises(IncompatibleEvidenceError):
        add_observed(
            store,
            record("unrelated", names=("Unrelated",), snapshot=B),
            requirement=full_req,
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"types": []},
        {"types": ["State", "State"]},
        {"types": [True]},
        {"types": "State"},
        {"view": "overview"},
        {"view": "union"},
        {"snapshot": "stale"},
        {"manager": ""},
        {"extra": 1},
    ],
)
def test_invalid_binding_shapes_are_rejected(patch):
    from general_manager.chat.planned.models import SchemaBinding

    with pytest.raises(ValueError):
        SchemaBinding.from_mapping(
            {
                "manager": "Part",
                "view": "detail",
                "types": ["State"],
                "snapshot": "current",
                **patch,
            }
        )


def test_schema_bindings_are_not_accepted_on_query_requirements():
    with pytest.raises(PlanValidationError):
        validate_plan(
            {
                "intent": "read",
                "tasks": [
                    {
                        "task_id": "t",
                        "objective": "Read",
                        "depends_on": [],
                        "requirements": [
                            {
                                "requirement_id": "q",
                                "kind": "query",
                                "operation": None,
                                "description": "Read",
                                "schema": {
                                    "manager": "Part",
                                    "view": "overview",
                                    "types": [],
                                    "snapshot": "current",
                                },
                            }
                        ],
                        "completion_criteria": ["q"],
                        "routing_features": [],
                    }
                ],
            }
        )


def test_schema_freshness_is_shared_across_tasks_and_managers_remain_independent():
    from dataclasses import replace

    req = requirement("overview", [])
    store = EvidenceStore()
    old = record("old", "overview")
    add_observed(store, old, requirement=req)
    add_observed(
        store,
        replace(
            record("other-manager", "overview", snapshot=B, manager="Other"),
            task_id="child",
        ),
    )
    assert store.for_requirement("t", req) == (old,)
    add_observed(
        store,
        replace(record("changed-in-child", "overview", snapshot=B), task_id="child"),
    )
    assert store.for_requirement("t", req) == ()
    assert not store.schema_current(old)
    store.invalidate_schema("child", "Part")
    assert not store.schema_current(store.get("changed-in-child"))
