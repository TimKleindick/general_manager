"""Selective schema work binds once to observed task-local names and freshness."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from general_manager.chat.planned import validation
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    SchemaBinding,
)
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_tool_feedback import (
    _runner,
    _record_rounds,
    _reference,
)

A = "a" * 64
B = "b" * 64


def pending_task():
    req = EvidenceRequirement(
        "details",
        "schema",
        "Observe required native types",
        None,
        schema=SchemaBinding("Part", "detail", (), "current"),
    )
    return PlannedTask("t", "Read selected fields", (), (req,), ("details",), ())


def observe(store, *, owner="t", manager="Part", snapshot=A):
    value = {
        "manager": manager,
        "schema_view": "overview",
        "snapshot": snapshot,
        "schema_complete": False,
        "type_manifest": {"PartFilter": {"kind": "input"}, "State": {"kind": "enum"}},
    }
    item = EvidenceRecord.create(
        "overview", owner, "schema", "read-overview", {}, value
    )
    store.observe_schema(owner, value)
    store.add(item)
    return item


def test_pending_detail_never_accepts_even_an_explicit_full_observation():
    task = pending_task()
    store = EvidenceStore()
    item = observe(store)
    full = EvidenceRecord.create(
        "full",
        "t",
        "schema",
        "full",
        {},
        {
            **item.payload(),
            "schema_view": "full",
            "schema_complete": True,
            "types": {"PartFilter": {"kind": "input"}},
        },
    )
    assert not store.can_link(task.requirements[0], item)
    assert not store.can_link(task.requirements[0], full)
    assert not store.for_requirement("t", task.requirements[0])


def test_binding_freezes_only_requested_observed_types_and_does_not_complete():
    task = pending_task()
    store = EvidenceStore()
    observe(store)
    before = deepcopy(task)
    records = store.records
    bound = validation.bind_schema_requirement(
        task, "details", ("PartFilter",), A, store
    )
    assert bound.requirements[0].schema == SchemaBinding(
        "Part", "detail", ("PartFilter",), A
    )
    assert task == before and store.records == records
    assert not store.for_requirement("t", bound.requirements[0])
    with pytest.raises(validation.PlanValidationError):
        validation.bind_schema_requirement(bound, "details", ("State",), A, store)


@pytest.mark.parametrize(
    "damage",
    [
        "absent",
        "foreign_task",
        "wrong_manager",
        "stale",
        "unknown",
        "duplicate",
        "empty",
    ],
)
def test_invalid_binding_leaves_task_and_evidence_unchanged(damage):
    task = pending_task()
    store = EvidenceStore()
    if damage != "absent":
        observe(
            store,
            owner="other" if damage == "foreign_task" else "t",
            manager="Other" if damage == "wrong_manager" else "Part",
        )
    if damage == "stale":
        store.observe_schema(
            "other",
            {
                "manager": "Part",
                "schema_view": "overview",
                "snapshot": B,
                "schema_complete": False,
            },
        )
    names = {
        "unknown": ("HiddenType",),
        "duplicate": ("State", "State"),
        "empty": (),
    }.get(damage, ("PartFilter",))
    records = store.records
    with pytest.raises(validation.PlanValidationError):
        validation.bind_schema_requirement(task, "details", names, A, store)
    assert store.records == records
    assert task.requirements[0].schema.types == ()


def test_legacy_full_and_already_declared_scopes_cannot_be_rebound():
    task = pending_task()
    store = EvidenceStore()
    observe(store)
    for schema in [
        None,
        SchemaBinding("Part", "overview", (), "current"),
        SchemaBinding("Part", "detail", ("State",), "current"),
    ]:
        fixed = replace(
            task, requirements=(replace(task.requirements[0], schema=schema),)
        )
        with pytest.raises(validation.PlanValidationError):
            validation.bind_schema_requirement(
                fixed, "details", ("PartFilter",), A, store
            )


def test_scheduler_binds_observed_names_then_requires_real_detail_read(monkeypatch):
    task = pending_task()
    payload = {
        "manager": "Part",
        "schema_view": "detail",
        "snapshot": A,
        "schema_complete": False,
        "types": {"PartFilter": {"kind": "input"}},
        "type_manifest": {"PartFilter": {"kind": "input"}},
    }
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    observe(runner.evidence)
    calls = _record_rounds(
        monkeypatch,
        [
            {
                "action": "bind_schema",
                "requirement_id": "details",
                "types": ["PartFilter"],
                "snapshot": A,
            },
            ToolCallEvent(
                "detail",
                "get_manager_schema",
                {
                    "manager": "Part",
                    "view": "detail",
                    "types": ["PartFilter"],
                    "snapshot": A,
                    "requirement_id": "details",
                },
            ),
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.task.requirements[0].schema.snapshot == A
    assert not runner.requirements_satisfied(runtime)
    ref = _reference(calls[0][1])
    pending = ref["pending_schema_bindings"][0]
    assert pending["manager"] == "Part" and pending["requirement_id"] == "details"
    assert pending["observed_sources"][0]["snapshot"] == A
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runner.requirements_satisfied(runtime)


def test_pending_detail_requires_current_until_explicitly_bound():
    with pytest.raises(ValueError):
        SchemaBinding("Part", "detail", (), A)


def test_source_manager_reference_does_not_authorize_target_manager_binding():
    task = pending_task()
    target = replace(
        task,
        requirements=(
            replace(
                task.requirements[0],
                schema=SchemaBinding("Target", "detail", (), "current"),
            ),
        ),
    )
    store = EvidenceStore()
    source = observe(store)
    payload = source.payload()
    payload["type_manifest"]["TargetType"] = {"kind": "reference", "manager": "Target"}
    store.add(
        EvidenceRecord.create("source-ref", "t", "schema", "source-ref", {}, payload)
    )
    with pytest.raises(validation.PlanValidationError):
        validation.bind_schema_requirement(target, "details", ("TargetType",), A, store)


def test_next_hop_names_are_discovered_before_binding_complete_selection():
    task = pending_task()
    store = EvidenceStore()
    observe(store)
    next_hop = {
        "manager": "Part",
        "schema_view": "detail",
        "snapshot": A,
        "schema_complete": False,
        "types": {
            "PartFilter": {"kind": "input", "fields": {"state": {"type": "NextState"}}}
        },
        "type_manifest": {
            "PartFilter": {"kind": "input"},
            "NextState": {"kind": "enum"},
        },
    }
    fragment = EvidenceRecord.create(
        "discovered", "t", "schema", "detail", {}, next_hop
    )
    store.add(fragment)
    assert not store.can_link(task.requirements[0], fragment)
    bound = validation.bind_schema_requirement(
        task, "details", ("PartFilter", "NextState"), A, store
    )
    store.link("t", bound.requirements[0], "discovered")
    assert not store.for_requirement("t", bound.requirements[0])
    enum = EvidenceRecord.create(
        "enum",
        "t",
        "schema",
        "enum",
        {},
        {
            **next_hop,
            "types": {"NextState": {"kind": "enum", "values": ["OPEN", "CLOSED"]}},
        },
    )
    store.add(enum, requirement=bound.requirements[0])
    assert {
        x.evidence_id for x in store.for_requirement("t", bound.requirements[0])
    } == {"discovered", "enum"}
    store.observe_schema("other", {**next_hop, "snapshot": B})
    assert not store.for_requirement("t", bound.requirements[0])
