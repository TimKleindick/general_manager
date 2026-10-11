"""Bounded differences prove two independent public query populations."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from types import SimpleNamespace
from typing import ClassVar

import graphene
import pytest
from django.contrib.auth.models import AnonymousUser

from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract
from general_manager.chat.planned.calculation_scope import (
    validate_calculation_binding_evidence,
)
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.evidence_selection import selected_evidence
from general_manager.chat.planned.models import (
    CalculationBinding,
    EvidenceRequirement,
    PlannedTask,
    SchemaBinding,
)
from general_manager.chat.providers.base import ToolCallEvent
from general_manager.chat.schema_inspection import inspect_manager_schema
from general_manager.chat.tools import execute_chat_tool
from general_manager.interface.unit_contract import FieldUnitContract
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from tests.unit.test_chat_planned_tool_feedback import _runner, _record_rounds
from tests.utils.simple_manager_interface import BaseTestInterface


def _cross_mapping(ids=("gross", "returns")):
    return {
        "source_requirement_ids": ["gross_total", "returns_total"],
        "value_path": None,
        "group_by": [],
        "unit_path": None,
        "cross_query": {
            "sources": [
                {
                    "source_requirement_id": requirement,
                    "calculation_evidence_id": evidence_id,
                    "entity_path": ["subject", "id"],
                    "period_path": ["eventDate"],
                    "entity_filter_path": ["subject", "id_Exact"],
                    "period_start_filter_path": ["eventDate_Gte"],
                    "period_end_filter_path": ["eventDate_Lt"],
                }
                for requirement, evidence_id in zip(
                    ("gross_total", "returns_total"), ids, strict=False
                )
            ],
            "schema_requirement_ids": [
                "gross_schema",
                "returns_schema",
                "subject_schema",
            ],
            "schema_evidence_ids": ["sg", "sr", "ss"],
        },
    }


def test_fixed_quantity_has_validated_executable_term_without_row_unit():
    assert hasattr(FieldUnitContract, "fixed_quantity"), (
        "fixed quantity authority is missing"
    )
    declaration = FieldUnitContract.fixed_quantity(unit="count", label="pieces")
    assert declaration.as_mapping() == {
        "kind": "fixed_quantity",
        "label": "pieces",
        "term": {"unit": "count", "dimension": {"[piece_count]": 1}},
    }
    damaged = declaration.as_mapping()
    damaged["term"]["dimension"] = {"[mass]": 1}
    with pytest.raises(ValueError):
        FieldUnitContract.from_mapping(damaged)


def test_cross_query_binding_is_opt_in_ordered_and_legacy_mapping_unchanged():
    mapping = _cross_mapping()
    binding = CalculationBinding.from_mapping(mapping)
    assert binding.as_mapping() == mapping
    assert CalculationBinding(("q",), ("amount",)).as_mapping() == {
        "source_requirement_ids": ["q"],
        "value_path": ["amount"],
        "group_by": [],
        "unit_path": None,
    }
    for damaged in [
        {**mapping, "source_requirement_ids": ["returns_total", "gross_total"]},
        {**mapping, "value_path": ["amount"]},
    ]:
        with pytest.raises(ValueError):
            CalculationBinding.from_mapping(damaged)


@pytest.mark.parametrize(
    "value, scalar, constructor",
    [
        ("01", "ID", "int"),
        ("+1", "ID", "int"),
        ("-0", "ID", "int"),
        ("1.0", "ID", "int"),
        (" 1", "ID", "int"),
        ("\u0661", "ID", "int"),
        (True, "ID", "int"),
        (1.0, "ID", "int"),
        ("17", "Int", "int"),
        (17, "String", "str"),
        (True, "ID", "str"),
    ],
)
def test_cross_query_native_id_transport_rejects_unproved_type_normalization(
    value, scalar, constructor
):
    from general_manager.chat.planned.cross_query import _typed_id

    with pytest.raises(CalculationError, match="explicit constructor type"):
        _typed_id(
            value,
            {"type": scalar + "!", "identity_contract": {"input_type": constructor}},
        )


def test_cross_query_string_constructor_keeps_exact_id_text():
    from general_manager.chat.planned.cross_query import _typed_id

    definition = {"type": "ID!", "identity_contract": {"input_type": "str"}}
    assert _typed_id("01", definition) == {"type": "str", "value": "01"}
    assert _typed_id(17, definition) == {"type": "str", "value": "17"}


@pytest.fixture
def public_population(monkeypatch, request):
    """Use common generated filters and the actual public structured query tool."""
    native_integer_id = getattr(request, "param", None) == "native_integer_id"
    identity_type = int if native_integer_id else str
    identity_metadata = {"type": identity_type}
    if native_integer_id:
        identity_metadata.update(is_identifier=True, is_primary_key=True)
    identity_field = graphene.ID if native_integer_id else graphene.String

    class SubjectInterface(BaseTestInterface):
        input_fields: ClassVar = {"id": Input(identity_type)}

        @classmethod
        def get_attribute_types(cls):
            return {"id": dict(identity_metadata), "name": {"type": str}}

        @classmethod
        def all(cls):
            return PopulationBucket(
                cls._parent_class,
                [SimpleNamespace(**row) for row in rows[cls._parent_class.__name__]],
            )

        @classmethod
        def filter(cls, **kwargs):
            return cls.all().filter(**kwargs) if kwargs else cls.all()

    class Subject(GeneralManager):
        Interface = SubjectInterface
        chat_exposed = True

    class OtherSubjectInterface(SubjectInterface):
        pass

    class OtherSubject(GeneralManager):
        Interface = OtherSubjectInterface
        chat_exposed = True

    different_namespace = getattr(request, "param", None) == "different_namespace"

    class GrossInterface(SubjectInterface):
        field_unit_contracts: ClassVar = {
            "amount": FieldUnitContract.quantity(
                unit_field="unit_label", units={"pieces": "count"}
            )
        }

        @classmethod
        def get_attribute_types(cls):
            return {
                "id": dict(identity_metadata),
                "amount": {"type": int},
                "unit_label": {"type": str},
                "event_date": {"type": date},
                "subject": {
                    "type": Subject,
                    "relation_kind": "direct",
                    "filter_lookup": "subject",
                },
                "source": {"type": str},
            }

    class ReturnInterface(GrossInterface):
        field_unit_contracts: ClassVar = {
            "amount": FieldUnitContract.fixed_quantity(unit="count", label="pieces")
        }

        @classmethod
        def get_attribute_types(cls):
            attributes = super().get_attribute_types()
            if different_namespace:
                attributes["subject"] = {
                    "type": OtherSubject,
                    "relation_kind": "direct",
                    "filter_lookup": "subject",
                }
            return attributes

    class Gross(GeneralManager):
        Interface = GrossInterface
        chat_exposed = True

    class Returned(GeneralManager):
        Interface = ReturnInterface
        chat_exposed = True

    class SubjectType(graphene.ObjectType):
        id = identity_field(required=True)
        name = graphene.String()

    class OtherSubjectType(graphene.ObjectType):
        id = identity_field(required=True)
        name = graphene.String()

    class GrossType(graphene.ObjectType):
        id = identity_field(required=True)
        amount = graphene.Int()
        unit_label = graphene.String()
        event_date = graphene.Date()
        subject = graphene.Field(SubjectType)
        source = graphene.String()

    class ReturnedType(graphene.ObjectType):
        id = identity_field(required=True)
        amount = graphene.Int()
        unit_label = graphene.String()
        event_date = graphene.Date()
        subject = graphene.Field(
            OtherSubjectType if different_namespace else SubjectType
        )
        source = graphene.String()

    managers = {"Gross": Gross, "Returned": Returned, "Subject": Subject}
    types = {"Gross": GrossType, "Returned": ReturnedType, "Subject": SubjectType}
    if different_namespace:
        managers["OtherSubject"] = OtherSubject
        types["OtherSubject"] = OtherSubjectType
    monkeypatch.setattr(GraphQL, "manager_registry", managers)
    monkeypatch.setattr(GraphQL, "graphql_type_registry", types)
    from general_manager.api.graphql_search import create_filter_options
    from general_manager.api.graphql_resolvers import create_root_list_resolver
    from general_manager.api.graphql_units import attach_unit_contracts
    from general_manager.utils.filter_parser import create_filter_function
    from tests.utils.simple_manager_interface import SimpleBucket

    class PopulationBucket(SimpleBucket):
        def filter(self, **kwargs):
            return PopulationBucket(
                self._manager_class,
                [
                    row
                    for row in self
                    if all(
                        create_filter_function(key, value)(row)
                        for key, value in kwargs.items()
                    )
                ],
            )

        def exclude(self, **kwargs):
            return PopulationBucket(
                self._manager_class,
                [
                    row
                    for row in self
                    if not all(
                        create_filter_function(key, value)(row)
                        for key, value in kwargs.items()
                    )
                ],
            )

    filters = {}
    rows = {
        "Gross": [
            {
                "id": "g1",
                "amount": 120,
                "unit_label": "pieces",
                "event_date": date(2025, 6, 15),
                "subject": SimpleNamespace(id="p1", name="Alpha"),
                "source": "a",
            }
        ],
        "Returned": [
            {
                "id": "r1",
                "amount": 10,
                "event_date": date(2025, 10, 1),
                "subject": SimpleNamespace(id="p1", name="Alpha"),
                "source": "b",
            }
        ],
        "Subject": [{"id": "p1", "name": "Alpha"}],
        "OtherSubject": [{"id": "p1", "name": "Alpha"}],
    }
    if native_integer_id:
        rows["Subject"][0]["id"] = rows["OtherSubject"][0]["id"] = 7
        for index, name in enumerate(("Gross", "Returned")):
            rows[name][0]["id"] = 2**53 + 1 + index
            rows[name][0]["subject"] = SimpleNamespace(id=7, name="Alpha")

    class PageInfo(graphene.ObjectType):
        total_count = graphene.Int(required=True)

    roots = {}
    for name, manager in managers.items():
        input_type = create_filter_options(
            manager, filters, GraphQL._map_field_to_graphene_read
        )
        page = type(
            name + "Page",
            (graphene.ObjectType,),
            {
                "items": graphene.List(types[name]),
                "pageInfo": graphene.Field(PageInfo),
            },
        )

        roots[name.lower() + "_list"] = graphene.Field(
            page,
            filter=graphene.Argument(input_type),
            page=graphene.Int(),
            page_size=graphene.Int(),
            resolver=create_root_list_resolver(manager),
        )
    schema = graphene.Schema(query=type("Query", (graphene.ObjectType,), roots))
    attach_unit_contracts(schema.graphql_schema, managers, types)
    monkeypatch.setattr(GraphQL, "get_schema", lambda: schema)
    graphql_contract.clear_contract_cache()
    yield schema, managers, rows
    graphql_contract.clear_contract_cache()


def _task():
    requirements = (
        *tuple(
            EvidenceRequirement(
                req,
                "schema",
                "Current public definitions",
                None,
                schema=SchemaBinding(manager, "full", (), "current"),
            )
            for req, manager in [
                ("gross_schema", "Gross"),
                ("returns_schema", "Returned"),
                ("subject_schema", "Subject"),
            ]
        ),
        EvidenceRequirement(
            "gross_query", "query", "Complete selected gross population", None
        ),
        EvidenceRequirement(
            "returns_query", "query", "Complete selected return population", None
        ),
        EvidenceRequirement(
            "gross_total",
            "calculation",
            "Gross sum",
            "sum",
            CalculationBinding(("gross_query",), ("amount",), unit_path=("unitLabel",)),
        ),
        EvidenceRequirement(
            "returns_total",
            "calculation",
            "Return sum",
            "sum",
            CalculationBinding(("returns_query",), ("amount",)),
        ),
        EvidenceRequirement(
            "net", "calculation", "Difference", "difference", binding_required=True
        ),
    )
    return PlannedTask(
        "t",
        "Compute a bounded net",
        (),
        requirements,
        tuple(req.requirement_id for req in requirements),
        ("requires_calculation", "multiple_queries"),
    )


def _setup(public_population):
    task = _task()
    schema_selectors = [
        ("Gross", "gross_schema"),
        ("Returned", "returns_schema"),
        ("Subject", "subject_schema"),
    ]
    if "OtherSubject" in public_population[1]:
        extra = EvidenceRequirement(
            "other_subject_schema",
            "schema",
            "Current alternate namespace",
            None,
            schema=SchemaBinding("OtherSubject", "full", (), "current"),
        )
        task = replace(
            task,
            requirements=(*task.requirements[:-1], extra, task.requirements[-1]),
            completion_criteria=(
                *task.completion_criteria[:-1],
                extra.requirement_id,
                "net",
            ),
        )
        schema_selectors.append(("OtherSubject", "other_subject_schema"))
    runner = _runner(execute_chat_tool, tasks=[task])
    runner.scope = {"user": AnonymousUser()}
    runtime = runner.runtimes["t"]
    for manager, req in schema_selectors:
        asyncio.run(
            runner.execute_tool(
                runtime,
                ToolCallEvent(
                    "s" + manager,
                    "get_manager_schema",
                    {"manager": manager, "view": "full", "requirement_id": req},
                ),
            )
        )
    for manager, req in [("Gross", "gross_query"), ("Returned", "returns_query")]:
        fields = ["id", "amount", "eventDate", {"subject": ["id", "name"]}]
        if manager == "Gross":
            fields.append("unitLabel")
        result, _ = asyncio.run(
            runner.execute_tool(
                runtime,
                ToolCallEvent(
                    "q" + manager,
                    "query",
                    {
                        "manager": manager,
                        "filters": {
                            "subject": {
                                "id_Exact": public_population[2]["Subject"][0]["id"]
                            },
                            "eventDate_Gte": "2025-01-01",
                            "eventDate_Lt": "2026-01-01",
                        },
                        "fields": fields,
                        "requirement_id": req,
                    },
                ),
            )
        )
        assert result.get("complete") is True and result["data"][0]["amount"] == (
            120 if manager == "Gross" else 10
        ), result
    store = runner.evidence
    query_ids = [
        store.for_requirement("t", req)[0].evidence_id for req in task.requirements[3:5]
    ]
    for index, req in enumerate(task.requirements[5:7]):
        record = calculate_evidence(
            ("gross", "returns")[index],
            "t",
            "sum",
            [
                CalculationOperand(query_ids[index], ("data", row_index, "amount"))
                for row_index in range(
                    len(store.get(query_ids[index]).payload()["data"])
                )
            ],
            store,
            require_linked=True,
            binding=req.binding,
        )
        store.add(record, requirement=req)
    mapping = _cross_mapping()
    mapping["cross_query"]["schema_evidence_ids"] = [
        store.for_requirement("t", req)[0].evidence_id for req in task.requirements[:3]
    ]
    if "OtherSubject" in public_population[1]:
        mapping["cross_query"]["schema_requirement_ids"].append("other_subject_schema")
        mapping["cross_query"]["schema_evidence_ids"].append(
            store.for_requirement("t", extra)[0].evidence_id
        )
    return runner, runtime, CalculationBinding.from_mapping(mapping)


@pytest.mark.parametrize(
    "public_population", [None, "native_integer_id"], indirect=True
)
def test_public_query_difference_admission_execution_and_completion(
    public_population, monkeypatch
):
    runner, runtime, binding = _setup(public_population)
    original = runner.evidence.records
    validate_calculation_binding_evidence(runtime.task, "net", binding, runner.evidence)
    assert runner.evidence.records == original
    _record_rounds(
        monkeypatch,
        [
            {
                "action": "bind_calculation",
                "requirement_id": "net",
                "binding": binding.as_mapping(),
            },
            {
                "action": "calculate",
                "requirement_id": "net",
                "operation": "difference",
                "operands": [
                    {"evidence_id": id, "path": ["value"]}
                    for id in ("gross", "returns")
                ],
            },
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    result = runner.evidence.records[-1]
    assert result.payload()["value"] == 110
    scope = result.payload()["scope"]
    assert scope["unit"] == "pieces" and scope["population_complete"] is True
    assert (
        len({source["query_evidence_id"] for source in scope["cross_query"]["sources"]})
        == 2
    )
    assert scope["cross_query"]["sources"][1]["original_scope"]["unit"] is None
    if type(public_population[2]["Gross"][0]["id"]) is int:
        assert scope["cross_query"]["entity"]["identity"] == {"type": "int", "value": 7}
        for index, source in enumerate(scope["cross_query"]["sources"]):
            native_id = 2**53 + 1 + index
            query = runner.evidence.get(source["query_evidence_id"]).payload()
            assert query["data"][0]["id"] == str(native_id)
            assert source["population_ids"] == [{"type": "int", "value": native_id}]
    assert runner.evidence.records[: len(original)] == original
    chosen = selected_evidence(runner.evidence, "t", [result.evidence_id])
    assert {record.kind for record in chosen} == {"schema", "query", "calculation"}
    _record_rounds(
        monkeypatch, [{"action": "complete", "evidence_ids": [result.evidence_id]}]
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "resolved"


def _copy_store(store, task, transform):
    result = EvidenceStore()
    for original in store.records:
        record = transform(original)
        if record is None:
            continue
        if record.kind == "schema":
            result.observe_schema(record.task_id, record.payload())
        result.add(record)
        for requirement in task.requirements:
            if store.is_linked_to(
                task.task_id, requirement.requirement_id, original.evidence_id
            ):
                result.link(task.task_id, requirement, record.evidence_id)
    return result


def _changed(record, *, payload=None, args=None, provenance=None, task_id=None):
    return EvidenceRecord.create(
        record.evidence_id,
        task_id or record.task_id,
        record.kind,
        record.call_identity
        if args is None
        else canonical_call_identity("query", args),
        record.provenance if provenance is None else provenance,
        record.payload() if payload is None else payload,
    )


def test_public_queries_preserve_all_independent_population_leaves(public_population):
    rows = public_population[2]
    rows["Gross"].append({**rows["Gross"][0], "id": "g2", "amount": 20})
    rows["Returned"].append({**rows["Returned"][0], "id": "r2", "amount": 3})
    runner, runtime, binding = _setup(public_population)
    validate_calculation_binding_evidence(runtime.task, "net", binding, runner.evidence)
    net = calculate_evidence(
        "net-result",
        "t",
        "difference",
        [CalculationOperand(id, ("value",)) for id in ("gross", "returns")],
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    assert net.payload()["value"] == 127
    assert [
        len(source["leaf_paths"])
        for source in net.payload()["scope"]["cross_query"]["sources"]
    ] == [2, 2]
    assert [
        len(source["population_ids"])
        for source in net.payload()["scope"]["cross_query"]["sources"]
    ] == [2, 2]
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    runner.evidence.add(net, requirement=runtime.task.requirements[-1])
    assert selected_evidence(runner.evidence, "t", ["net-result"])


def test_actual_mixed_unit_population_fails_all_three_proof_gates(public_population):
    rows = public_population[2]
    rows["Gross"].append({**rows["Gross"][0], "id": "g2", "amount": 20})
    runner, runtime, binding = _setup(public_population)
    operands = [CalculationOperand(id, ("value",)) for id in ("gross", "returns")]
    net = calculate_evidence(
        "net-result",
        "t",
        "difference",
        operands,
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    runner.evidence.add(net, requirement=runtime.task.requirements[-1])
    query_id = runner.evidence.get("gross").payload()["scope"]["query_evidence_id"]

    def transform(record):
        if record.evidence_id != query_id:
            return record
        payload = record.payload()
        assert len(payload["data"]) == 2
        payload["data"][1]["unitLabel"] = "kg"
        return _changed(record, payload=payload)

    damaged = _copy_store(runner.evidence, runtime.task, transform)
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(runtime.task, "net", binding, damaged)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "difference",
            operands,
            damaged,
            require_linked=True,
            binding=binding,
        )
    with pytest.raises(CalculationError):
        selected_evidence(damaged, "t", ["net-result"])


@pytest.mark.parametrize("public_population", ["different_namespace"], indirect=True)
def test_equal_scalar_entity_ids_in_different_manager_namespaces_fail(
    public_population,
):
    runner, runtime, binding = _setup(public_population)
    gross_query = runner.evidence.get("gross").payload()["scope"]["query_evidence_id"]
    returned_query = runner.evidence.get("returns").payload()["scope"][
        "query_evidence_id"
    ]
    assert (
        runner.evidence.get(gross_query).payload()["data"][0]["subject"]["id"]
        == runner.evidence.get(returned_query).payload()["data"][0]["subject"]["id"]
        == "p1"
    )
    assert (
        inspect_manager_schema("Gross", view="full")["types"]["SubjectType"]["manager"]
        == "Subject"
    )
    assert (
        inspect_manager_schema("Returned", view="full")["types"]["OtherSubjectType"][
            "manager"
        ]
        == "OtherSubject"
    )
    with pytest.raises(CalculationError, match="same entity namespace"):
        validate_calculation_binding_evidence(
            runtime.task, "net", binding, runner.evidence
        )
    with pytest.raises(CalculationError, match="same entity namespace"):
        calculate_evidence(
            "bad",
            "t",
            "difference",
            [CalculationOperand(id, ("value",)) for id in ("gross", "returns")],
            runner.evidence,
            require_linked=True,
            binding=binding,
        )


@pytest.mark.parametrize("damage", ["value", "scope", "identity"])
def test_cross_query_completion_rechecks_derived_value_scope_and_identity(
    public_population, damage
):
    runner, runtime, binding = _setup(public_population)
    net = calculate_evidence(
        "net-result",
        "t",
        "difference",
        [CalculationOperand(id, ("value",)) for id in ("gross", "returns")],
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    runner.evidence.add(net, requirement=runtime.task.requirements[-1])

    def transform(record):
        if record.evidence_id != "net-result":
            return record
        payload = record.payload()
        provenance = dict(record.provenance)
        if damage == "value":
            payload["value"] = 111
        elif damage == "scope":
            payload["scope"]["cross_query"]["resolved_unit"]["term"]["dimension"] = {
                "[mass]": 1
            }
        else:
            provenance["calculator"] = "model"
        return _changed(record, payload=payload, provenance=provenance)

    damaged = _copy_store(runner.evidence, runtime.task, transform)
    with pytest.raises(CalculationError):
        selected_evidence(damaged, "t", ["net-result"])


@pytest.mark.parametrize("damage", [None, "missing_input", "duplicate_definition"])
def test_separate_current_output_and_input_schema_fragments_are_exact_proof(
    public_population, damage
):
    runner, runtime, binding = _setup(public_population)
    full = inspect_manager_schema("Gross", view="full")
    output_requirement = replace(
        runtime.task.requirements[0],
        schema=SchemaBinding("Gross", "detail", ("GrossType",), full["snapshot"]),
    )
    input_names = tuple(
        name
        for name, definition in full["types"].items()
        if definition["kind"] == "input"
    )
    input_requirement = EvidenceRequirement(
        "gross_input_schema",
        "schema",
        "Exact generated input definitions",
        None,
        schema=SchemaBinding("Gross", "detail", input_names, full["snapshot"]),
    )
    requirements = (
        output_requirement,
        *runtime.task.requirements[1:3],
        input_requirement,
        *runtime.task.requirements[3:],
    )
    runtime.task = replace(runtime.task, requirements=requirements)
    schema_ids = {}
    for requirement in (output_requirement, input_requirement):
        result, valid = asyncio.run(
            runner.execute_tool(
                runtime,
                ToolCallEvent(
                    requirement.requirement_id + "_detail",
                    "get_manager_schema",
                    {
                        "manager": "Gross",
                        "view": "detail",
                        "types": list(requirement.schema.types),
                        "snapshot": full["snapshot"],
                        "requirement_id": requirement.requirement_id,
                    },
                ),
            )
        )
        assert valid and result["schema_view"] == "detail"
        schema_ids[requirement.requirement_id] = next(
            record.evidence_id
            for record in reversed(runner.evidence.records)
            if record.kind == "schema"
            and record.payload().get("types") == result["types"]
        )
    mapping = binding.as_mapping()
    mapping["cross_query"]["schema_evidence_ids"][0] = schema_ids["gross_schema"]
    mapping["cross_query"]["schema_requirement_ids"].append("gross_input_schema")
    mapping["cross_query"]["schema_evidence_ids"].append(
        schema_ids["gross_input_schema"]
    )
    binding = CalculationBinding.from_mapping(mapping)
    operands = [CalculationOperand(id, ("value",)) for id in ("gross", "returns")]
    validate_calculation_binding_evidence(runtime.task, "net", binding, runner.evidence)
    net = calculate_evidence(
        "net-result",
        "t",
        "difference",
        operands,
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    runner.evidence.add(net, requirement=runtime.task.requirements[-1])
    assert net.payload()["value"] == 110
    assert {
        source["evidence_id"]
        for source in net.payload()["scope"]["cross_query"]["schema_sources"]
    } == set(mapping["cross_query"]["schema_evidence_ids"])
    assert selected_evidence(runner.evidence, "t", ["net-result"])
    if damage is None:
        return

    def transform(record):
        if record.evidence_id != schema_ids["gross_input_schema"]:
            return record
        payload = record.payload()
        if damage == "missing_input":
            payload["types"].pop("GrossFilterTypeDepth1")
        else:
            payload["types"]["GrossType"] = full["types"]["GrossType"]
        return _changed(record, payload=payload)

    damaged = _copy_store(runner.evidence, runtime.task, transform)
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(runtime.task, "net", binding, damaged)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "difference",
            operands,
            damaged,
            require_linked=True,
            binding=binding,
        )
    with pytest.raises(CalculationError):
        selected_evidence(damaged, "t", ["net-result"])


@pytest.mark.parametrize(
    "damage",
    [
        "extra_filter",
        "wrong_window",
        "wrong_inclusivity",
        "wrong_entity_filter",
        "exclude_argument",
        "historical_argument",
        "data_snapshot",
        "missing_witness",
        "unstable_witness",
        "bool_witness_version",
        "reattested_witness",
        "wrong_provenance",
        "foreign_query",
        "partial",
        "has_more",
        "wrong_count",
        "bool_count",
        "missing_id",
        "duplicate_id",
        "wrong_entity",
        "null_entity",
        "bool_entity",
        "wrong_type_entity",
        "outside_window",
        "missing_date",
        "naive_date",
        "invalid_date",
        "raw_unit",
        "null_unit",
        "missing_amount",
        "null_amount",
        "bool_amount",
        "nonfinite_amount",
        "forged_sum_value",
        "forged_sum_scope",
        "trimmed_sum",
        "duplicate_sum",
        "forged_sum_identity",
        "missing_quantity_contract",
        "wrong_fixed_unit",
        "different_scale",
        "forged_dimension",
        "hidden_entity",
        "hidden_source",
        "missing_identity",
        "mutated_filter_meaning",
    ],
)
def test_cross_query_rejects_damage_at_admission_execution_and_completion(
    public_population, damage
):
    runner, runtime, binding = _setup(public_population)
    store = runner.evidence
    operands = [CalculationOperand(id, ("value",)) for id in ("gross", "returns")]
    net = calculate_evidence(
        "net-result",
        "t",
        "difference",
        operands,
        store,
        require_linked=True,
        binding=binding,
    )
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    store.add(net, requirement=runtime.task.requirements[-1])
    gross_query = store.get("gross").payload()["scope"]["query_evidence_id"]
    return_query = store.get("returns").payload()["scope"]["query_evidence_id"]
    schema, managers, _rows = public_population

    if damage in {
        "missing_quantity_contract",
        "wrong_fixed_unit",
        "different_scale",
        "forged_dimension",
        "missing_identity",
        "mutated_filter_meaning",
    }:
        from general_manager.interface.unit_contract import (
            UNIT_EXTENSION,
            IDENTITY_EXTENSION,
        )

        native = schema.graphql_schema
        if damage == "missing_quantity_contract":
            native.get_type("ReturnedType").fields["amount"].extensions.pop(
                UNIT_EXTENSION
            )
        elif damage in {"wrong_fixed_unit", "different_scale"}:
            native.get_type("ReturnedType").fields["amount"].extensions[
                UNIT_EXTENSION
            ] = FieldUnitContract.fixed_quantity(
                unit="kg" if damage == "wrong_fixed_unit" else "g", label="pieces"
            ).as_mapping()
        elif damage == "forged_dimension":
            native.get_type("ReturnedType").fields["amount"].extensions[UNIT_EXTENSION][
                "term"
            ]["dimension"] = {"[mass]": 1}
        elif damage == "missing_identity":
            native.get_type("SubjectType").fields["id"].extensions.pop(
                IDENTITY_EXTENSION
            )
        else:
            from general_manager.api.graphql_filter_contract import FILTER_EXTENSION

            native.get_type("GrossFilterTypeDepth1").fields["eventDate_Gte"].extensions[
                FILTER_EXTENSION
            ]["operator"] = "gt"
    elif damage in {"hidden_entity", "hidden_source"}:
        managers[
            "Subject" if damage == "hidden_entity" else "Returned"
        ].chat_exposed = False

    def transform(record):
        if record.evidence_id not in {gross_query, return_query, "gross"}:
            return record
        payload = record.payload()
        provenance = dict(record.provenance)
        if record.evidence_id == return_query:
            args = json.loads(record.call_identity)["args"]
            if damage == "extra_filter":
                args["filters"]["source_Exact"] = "b"
            if damage == "wrong_window":
                args["filters"]["eventDate_Gte"] = "2025-02-01"
            if damage == "wrong_inclusivity":
                args["filters"]["eventDate_Lte"] = args["filters"].pop("eventDate_Lt")
            if damage == "wrong_entity_filter":
                args["filters"]["subject"]["id_Exact"] = "p2"
            if damage == "exclude_argument":
                args["arguments"] = {"exclude": {"source_Exact": "x"}}
            if damage == "historical_argument":
                args["arguments"] = {"asOf": "2025-01-01"}
            if damage in {
                "extra_filter",
                "wrong_window",
                "wrong_inclusivity",
                "wrong_entity_filter",
                "exclude_argument",
                "historical_argument",
            }:
                return _changed(record, args=args)
            if damage == "missing_witness":
                provenance.pop("query_schema_witness")
            if damage in {
                "unstable_witness",
                "reattested_witness",
                "bool_witness_version",
            }:
                witness = json.loads(provenance["query_schema_witness"])
                if damage == "unstable_witness":
                    witness["stable"] = False
                elif damage == "bool_witness_version":
                    witness["version"] = True
                else:
                    witness["before"]["Returned"] = "0" * 64
                provenance["query_schema_witness"] = json.dumps(witness)
            if damage == "wrong_provenance":
                provenance["manager"] = "Gross"
            if damage == "foreign_query":
                return _changed(record, task_id="foreign")
            if damage == "partial":
                payload["complete"] = False
            if damage == "has_more":
                payload["has_more"] = True
            if damage == "wrong_count":
                payload["total_count"] = 2
            if damage == "bool_count":
                payload["total_count"] = True
            if damage == "data_snapshot":
                payload["data_snapshot"] = "historical-2024"
            row = payload["data"][0]
            if damage == "missing_id":
                row.pop("id")
            if damage == "duplicate_id":
                payload["data"].append(deepcopy(row))
                payload["total_count"] = 2
            if damage in {
                "wrong_entity",
                "null_entity",
                "bool_entity",
                "wrong_type_entity",
            }:
                row["subject"]["id"] = {
                    "wrong_entity": "p2",
                    "null_entity": None,
                    "bool_entity": True,
                    "wrong_type_entity": 1,
                }[damage]
            if damage in {"outside_window", "naive_date", "invalid_date"}:
                row["eventDate"] = {
                    "outside_window": "2026-01-01",
                    "naive_date": "2025-10-01T00:00:00",
                    "invalid_date": "2025-02-30",
                }[damage]
            if damage == "missing_date":
                row.pop("eventDate")
            if damage == "missing_amount":
                row.pop("amount")
            if damage in {"null_amount", "bool_amount", "nonfinite_amount"}:
                row["amount"] = {
                    "null_amount": None,
                    "bool_amount": True,
                    "nonfinite_amount": "NaN",
                }[damage]
            return _changed(record, payload=payload, provenance=provenance)
        if record.evidence_id == gross_query:
            if damage in {"raw_unit", "null_unit"}:
                payload["data"][0]["unitLabel"] = (
                    "raw pieces" if damage != "null_unit" else None
                )
            return _changed(record, payload=payload)
        if damage == "forged_sum_value":
            payload["value"] = 121
        if damage == "forged_sum_scope":
            payload["scope"]["unit"] = "kg"
        if damage == "trimmed_sum":
            payload["operands"] = []
        if damage == "duplicate_sum":
            payload["operands"].append(deepcopy(payload["operands"][0]))
        if damage == "forged_sum_identity":
            provenance["calculator"] = "model"
        return _changed(record, payload=payload, provenance=provenance)

    # Foreign-task records cannot be linked to this task; retain them unlinked.
    if damage == "foreign_query":
        damaged = EvidenceStore()
        for original in store.records:
            changed = transform(original)
            if changed.kind == "schema":
                damaged.observe_schema("t", changed.payload())
            damaged.add(changed)
            if changed.task_id == "t":
                for req in runtime.task.requirements:
                    if store.is_linked_to(
                        "t", req.requirement_id, original.evidence_id
                    ):
                        damaged.link("t", req, changed.evidence_id)
    else:
        damaged = _copy_store(store, runtime.task, transform)
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(runtime.task, "net", binding, damaged)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "difference",
            operands,
            damaged,
            require_linked=True,
            binding=binding,
        )
    with pytest.raises(CalculationError):
        selected_evidence(damaged, "t", ["net-result"])


def test_cross_query_exact_operand_order_and_unlinked_sources_fail_closed(
    public_population,
):
    runner, runtime, binding = _setup(public_population)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "swapped",
            "t",
            "difference",
            [
                CalculationOperand("returns", ("value",)),
                CalculationOperand("gross", ("value",)),
            ],
            runner.evidence,
            require_linked=True,
            binding=binding,
        )
    wrong = binding.as_mapping()
    wrong["cross_query"]["sources"][0]["calculation_evidence_id"] = "returns"
    wrong["cross_query"]["sources"][1]["calculation_evidence_id"] = "gross"
    wrong = CalculationBinding.from_mapping(wrong)
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(
            runtime.task, "net", wrong, runner.evidence
        )
    damaged = _copy_store(
        runner.evidence,
        runtime.task,
        lambda record: None if record.evidence_id == "gross" else record,
    )
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(runtime.task, "net", binding, damaged)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "legacy",
            "t",
            "difference",
            [CalculationOperand(id, ("value",)) for id in ("gross", "returns")],
            runner.evidence,
            require_linked=True,
            binding=CalculationBinding(("gross_total", "returns_total"), None),
        )


@pytest.mark.parametrize(
    "damage",
    [
        "calculation_link",
        "query_link",
        "schema_link",
        "schema_requirement",
        "later_source",
        "later_schema",
        "wrong_operation",
    ],
)
def test_cross_query_requires_earlier_exact_current_task_requirements(
    public_population, damage
):
    runner, runtime, binding = _setup(public_population)
    task, store = runtime.task, runner.evidence
    if damage in {"calculation_link", "query_link", "schema_link"}:
        skipped = {
            "calculation_link": "gross_total",
            "query_link": "gross_query",
            "schema_link": "gross_schema",
        }[damage]
        damaged = EvidenceStore()
        for record in store.records:
            if record.kind == "schema":
                damaged.observe_schema("t", record.payload())
            damaged.add(record)
            for requirement in task.requirements:
                if requirement.requirement_id != skipped and store.is_linked_to(
                    "t", requirement.requirement_id, record.evidence_id
                ):
                    damaged.link("t", requirement, record.evidence_id)
        store = damaged
    elif damage == "schema_requirement":
        mapping = binding.as_mapping()
        mapping["cross_query"]["schema_requirement_ids"][0] = "undeclared"
        binding = CalculationBinding.from_mapping(mapping)
    elif damage in {"later_source", "later_schema"}:
        selected = "gross_total" if damage == "later_source" else "gross_schema"
        task = replace(
            task,
            requirements=(
                *tuple(
                    req for req in task.requirements if req.requirement_id != selected
                ),
                next(
                    req for req in task.requirements if req.requirement_id == selected
                ),
            ),
        )
    else:
        task = replace(
            task,
            requirements=(
                *task.requirements[:-1],
                replace(task.requirements[-1], operation="ratio"),
            ),
        )
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(task, "net", binding, store)
    assert store.records == runner.evidence.records


def test_cross_query_failed_batch_commits_no_partial_evidence(
    public_population, monkeypatch
):
    runner, runtime, binding = _setup(public_population)
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    before = runner.evidence.records
    good = {
        "action": "calculate",
        "requirement_id": "net",
        "operation": "difference",
        "operands": [
            {"evidence_id": id, "path": ["value"]} for id in ("gross", "returns")
        ],
    }
    bad = {**good, "operands": list(reversed(good["operands"]))}
    _record_rounds(
        monkeypatch, [{"action": "calculate_batch", "calculations": [good, bad]}]
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runner.evidence.records == before


def test_cached_query_keeps_original_schema_witness_and_cannot_be_reattested(
    public_population,
):
    runner, runtime, binding = _setup(public_population)
    original = next(
        record
        for record in runner.evidence.records
        if record.kind == "query" and record.provenance["manager"] == "Returned"
    )
    witness = original.provenance["query_schema_witness"]
    from general_manager.interface.unit_contract import UNIT_EXTENSION

    public_population[0].graphql_schema.get_type("ReturnedType").fields[
        "amount"
    ].extensions[UNIT_EXTENSION] = FieldUnitContract.fixed_quantity(
        unit="kg", label="pieces"
    ).as_mapping()
    runner.callbacks = replace(
        runner.callbacks,
        execute_tool=lambda *_: pytest.fail("a cache hit must not dispatch"),
    )
    result, progress = asyncio.run(
        runner.execute_tool(
            runtime,
            ToolCallEvent(
                "cached",
                "query",
                {
                    **json.loads(original.call_identity)["args"],
                    "requirement_id": "returns_query",
                },
            ),
        )
    )
    assert result["data"][0]["amount"] == 10 and progress is False
    assert runner.evidence.get(original.evidence_id) is original
    assert original.provenance["query_schema_witness"] == witness
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(
            runtime.task, "net", binding, runner.evidence
        )


def test_query_schema_change_during_dispatch_is_recorded_as_unstable(public_population):
    runner, runtime, _binding = _setup(public_population)
    from general_manager.interface.unit_contract import UNIT_EXTENSION

    def changing(name, args, context):
        result = execute_chat_tool(name, args, context)
        public_population[0].graphql_schema.get_type("GrossType").fields[
            "amount"
        ].extensions[UNIT_EXTENSION] = FieldUnitContract.quantity(
            unit_field="unitLabel", units={"pieces": "kg"}
        ).as_mapping()
        return result

    runner.callbacks = replace(runner.callbacks, execute_tool=changing)
    original = next(
        record
        for record in runner.evidence.records
        if record.kind == "query" and record.provenance["manager"] == "Gross"
    )
    args = json.loads(original.call_identity)["args"]
    args["fields"].append("source")
    asyncio.run(
        runner.execute_tool(
            runtime,
            ToolCallEvent(
                "changed", "query", {**args, "requirement_id": "gross_query"}
            ),
        )
    )
    record = runner.evidence.records[-1]
    witness = json.loads(record.provenance["query_schema_witness"])
    assert witness["stable"] is False and witness["before"] != witness["after"]


def test_fixed_unit_and_constructor_identity_publish_without_dynamic_units(
    public_population,
):
    gross = inspect_manager_schema("Gross")
    returned = inspect_manager_schema("Returned")
    subject = inspect_manager_schema("Subject")
    assert gross["output_fields"]["amount"]["unit_contract"]["kind"] == "quantity"
    assert (
        returned["output_fields"]["amount"]["unit_contract"]["kind"] == "fixed_quantity"
    )
    assert subject["output_fields"]["id"]["identity_contract"]["input_type"] == "str"
    full = inspect_manager_schema("Gross", view="full")
    assert full["types"]["GrossFilterTypeDepth1"]["fields"]["eventDate_Gte"][
        "filter_contract"
    ] == {"source": "interface_filter", "field": "eventDate", "operator": "gte"}
