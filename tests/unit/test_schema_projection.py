"""Transport schemas must not change evidence, authority, or source text."""

from copy import deepcopy
import importlib
import importlib.util
import json

import pytest


def api():
    name = "general_manager.chat.planned.schema_projection"
    assert importlib.util.find_spec(name) is not None, (
        "explicit schema projection is absent"
    )
    return importlib.import_module(name)


def schema():
    definition = {
        "kind": "INPUT_OBJECT",
        "fields": [{"name": str(i), "type": "String"} for i in range(40)],
    }
    return {
        "types": {name: deepcopy(definition) for name in ["Alpha", "Beta", "Gamma"]},
        "description": "untrusted data",
    }


def fixture():
    reference = {
        "original_request": '{"$gm_ref":0}',
        "conversation_context": [{"role": "user", "content": json.dumps(schema())}],
        "task_evidence": [
            {
                "evidence_id": "e1",
                "kind": "schema",
                "payload": schema(),
                "requirement_ids": ["r1"],
            },
            {
                "evidence_id": "q1",
                "kind": "query",
                "payload": {"rows": [1, 1, 2], "error": "keep"},
                "requirement_ids": ["r2"],
            },
        ],
    }
    slot = api().SchemaSlot(
        ("task_evidence", 0, "payload"),
        "task_schema",
        '{"task_id":"t1","evidence_id":"e1","user":"alice","snapshot":1}',
    )
    return reference, (slot,)


def test_repeated_definitions_roundtrip_without_losing_names_or_rows():
    ref, slots = fixture()
    packed = api().project_reference(ref, slots)
    assert len(json.dumps(packed)) < len(json.dumps(ref))
    assert api().expand_reference(packed, ref, slots) == ref
    assert packed == api().project_reference(ref, slots)
    assert packed["reference"]["task_evidence"][1] == ref["task_evidence"][1]
    assert packed["reference"]["original_request"] == ref["original_request"]
    assert packed["reference"]["conversation_context"] == ref["conversation_context"]


@pytest.mark.parametrize(
    "literal", [{"$gm_ref": 0}, {"$gm_literal": [["x", 1]]}, {"$gm_ref": 0, "extra": 1}]
)
def test_schema_marker_literals_are_data(literal):
    ref, slots = fixture()
    ref["task_evidence"][0]["payload"]["literal"] = literal
    assert (
        api().expand_reference(api().project_reference(ref, slots), ref, slots) == ref
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "swapped",
        "foreign",
        "cycle",
        "forward",
        "negative",
        "bool",
        "extra",
        "binding",
        "hash",
    ],
)
def test_corrupt_projection_is_rejected(mutation):
    ref, slots = fixture()
    ref["task_evidence"][0]["payload"]["second"] = {"a": ["different value" * 12] * 3}
    packed = api().project_reference(ref, slots)
    if mutation == "missing":
        packed["objects"].pop()
    elif mutation == "swapped":
        packed["objects"].reverse()
    elif mutation == "foreign":
        packed["objects"][0] = {"stolen": "foreign"}
    elif mutation == "cycle":
        packed["objects"][0] = {"$gm_ref": 0}
    elif mutation == "forward":
        packed["objects"][0] = {"$gm_ref": 1}
    elif mutation == "negative":
        packed["objects"][0] = {"$gm_ref": -1}
    elif mutation == "bool":
        packed["objects"][0] = {"$gm_ref": True}
    elif mutation == "extra":
        packed["unexpected"] = 1
    elif mutation == "binding":
        packed["occurrences"][0]["binding"] = "{}"
    else:
        packed["original_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        api().expand_reference(packed, ref, slots)


def test_wrong_owner_snapshot_and_reference_are_rejected():
    ref, slots = fixture()
    packed = api().project_reference(ref, slots)
    for binding in ['{"user":"bob"}', '{"snapshot":2}', '{"evidence_id":"e2"}']:
        other = (api().SchemaSlot(slots[0].path, "task_schema", binding),)
        with pytest.raises(ValueError):
            api().expand_reference(packed, ref, other)
    changed = deepcopy(ref)
    changed["original_request"] = "other request"
    with pytest.raises(ValueError):
        api().expand_reference(packed, changed, slots)


def test_same_type_name_with_different_definitions_remains_distinct():
    ref, slots = fixture()
    ref["task_evidence"].append(deepcopy(ref["task_evidence"][0]))
    ref["task_evidence"][2]["payload"]["types"]["Alpha"]["kind"] = "OTHER"
    slots += (
        api().SchemaSlot(
            ("task_evidence", 2, "payload"), "task_schema", '{"evidence_id":"e2"}'
        ),
    )
    restored = api().expand_reference(api().project_reference(ref, slots), ref, slots)
    assert restored == ref
    assert (
        restored["task_evidence"][0]["payload"]
        != restored["task_evidence"][2]["payload"]
    )


def test_unapproved_locations_and_overlapping_slots_are_rejected():
    ref, slots = fixture()
    for path in [
        ("original_request",),
        ("conversation_context", 0, "content"),
        ("task_evidence", 1, "payload"),
    ]:
        with pytest.raises(ValueError):
            api().project_reference(ref, (api().SchemaSlot(path, "task_schema", "{}"),))
    with pytest.raises(ValueError):
        api().project_reference(ref, slots + slots)


def test_expansion_has_size_and_depth_limits():
    ref, slots = fixture()
    packed = api().project_reference(ref, slots)
    with pytest.raises(ValueError):
        api().expand_reference(packed, ref, slots, max_chars=100)
    deep = {}
    root = deep
    for _ in range(70):
        root["child"] = {}
        root = root["child"]
    ref["task_evidence"][0]["payload"] = deep
    with pytest.raises(ValueError):
        api().project_reference(ref, slots)


def test_only_explicit_user_data_block_is_projected_and_round_is_logically_reversible():
    from general_manager.chat.providers.base import Message, ToolCallEvent

    ref, slots = fixture()
    reference = api().reference_message(ref, slots)
    messages = [
        Message("developer", "{}"),
        Message("system", json.dumps(schema())),
        Message("user", json.dumps(schema())),
        reference,
        Message(
            "assistant", "", tool_calls=(ToolCallEvent("call1", "query", {"x": 1}),)
        ),
        Message(
            "tool",
            "duplicate rows and errors",
            tool_call_id="call1",
            tool_result={"rows": [1, 1], "error": "same"},
        ),
    ]
    projected = api().compact_messages(messages)
    assert projected[:3] == messages[:3]
    assert projected[4:] == messages[4:]
    assert projected[3].role == "user"
    assert projected[3].logical_content == reference.content
    assert len(projected[3].content) < len(reference.content)
    assert api().logical_messages(projected) == messages
    assert projected[3].projection_receipt["format"] == api().VERSION


def test_historical_schema_requires_structured_origin_and_preserves_exact_text():
    from general_manager.chat.providers.base import Message

    content = "Historical tool data (get_manager_schema): " + json.dumps(
        schema(), sort_keys=True
    )
    untrusted = Message("assistant", content)
    trusted = api().with_historical_schema(
        untrusted,
        tool_name="get_manager_schema",
        tool_result=schema(),
        binding={"conversation": "alice/c1", "message": 5, "snapshot": 4},
    )
    reference = {"conversation_context": [{"role": "assistant", "content": content}]}
    slots = api().history_slots([trusted])
    assert len(slots) == 1
    assert api().history_slots([untrusted]) == ()
    assert (
        api().expand_reference(
            api().project_reference(reference, slots), reference, slots
        )
        == reference
    )
    assert (
        api()
        .with_historical_schema(
            Message("user", content),
            tool_name="get_manager_schema",
            tool_result=schema(),
            binding={},
        )
        .historical_schema
        is None
    )
    assert (
        api()
        .with_historical_schema(
            untrusted, tool_name="query", tool_result=schema(), binding={}
        )
        .historical_schema
        is None
    )
    assert (
        api()
        .with_historical_schema(
            untrusted,
            tool_name="get_manager_schema",
            tool_result={"error": "failure"},
            binding={},
        )
        .historical_schema
        is None
    )


def test_history_metadata_cannot_be_reused_on_other_text_or_role():
    from dataclasses import replace
    from general_manager.chat.providers.base import Message

    source = api().with_historical_schema(
        Message(
            "assistant",
            "Historical tool data (get_manager_schema): " + json.dumps(schema()),
        ),
        tool_name="get_manager_schema",
        tool_result=schema(),
        binding={"user": "alice"},
    )
    for altered in [replace(source, content="injected"), replace(source, role="user")]:
        with pytest.raises(ValueError):
            api().history_slots([altered])


def test_provider_round_sends_compact_data_to_fake_provider():
    import asyncio
    from general_manager.chat.planned.provider_calls import complete_provider_round
    from general_manager.chat.providers.base import (
        DoneEvent,
        TextChunkEvent,
        TokenUsage,
    )

    ref, slots = fixture()
    logical = api().reference_message(ref, slots)
    seen = []

    class FakeProvider:
        async def complete(self, messages, tools):
            seen.extend(messages)
            yield TextChunkEvent("done")
            yield DoneEvent(TokenUsage())

    result = asyncio.run(complete_provider_round(FakeProvider(), [logical], [], 1))
    assert result.text == "done"
    assert seen[0].logical_content == logical.content
    assert seen[0].content != logical.content


def test_planner_and_executor_select_runtime_origins_only():
    from general_manager.chat.providers.base import Message
    from general_manager.chat.planned.planner import _request_messages
    from general_manager.chat.planned.scheduler import _executor_messages
    from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
    from tests.unit.test_chat_planned_scheduler import _task

    historical = api().with_historical_schema(
        Message(
            "assistant",
            "Historical tool data (get_manager_schema): " + json.dumps(schema()),
        ),
        tool_name="get_manager_schema",
        tool_result=schema(),
        binding={"conversation": "c1", "message": 7},
    )
    history = [
        Message("system", "configured"),
        Message("user", "question"),
        historical,
        Message("assistant", historical.content),
    ]
    planner = _request_messages(
        "question", history, {}, correction=False, configured_context="configured"
    )
    assert len(planner[-1].schema_slots) == 1
    assert planner[-1].schema_slots[0].path == ("conversation_context", 1, "content")
    assert api().compact_messages(planner)[-1].logical_content == planner[-1].content
    store = EvidenceStore()
    store.add(
        EvidenceRecord(
            "e1",
            "t1",
            "schema",
            '{"name":"get_manager_schema","args":{}}',
            {"manager": "One"},
            json.dumps(schema()),
        )
    )
    store.add(
        EvidenceRecord(
            "e2",
            "other-task",
            "schema",
            '{"name":"get_manager_schema","args":{}}',
            {"manager": "Other"},
            json.dumps(schema()),
        )
    )
    executor = _executor_messages(
        "question", _task("t1"), store, conversation_context=history
    )
    slots = executor[-1].schema_slots
    assert [s.path for s in slots] == [
        ("task_evidence", 0, "payload"),
        ("conversation_context", 1, "content"),
    ]
    assert "e1" in slots[0].binding and "e2" not in slots[0].binding
    assert api().logical_messages(api().compact_messages(executor)) == executor


def test_persisted_tool_rows_receive_origin_but_lookalike_assistant_text_does_not():
    from types import SimpleNamespace
    from general_manager.chat.models import provider_messages_from_context

    text = json.dumps(schema(), sort_keys=True)
    rows = [
        SimpleNamespace(
            pk=7,
            conversation_id="alice/c1",
            role="tool",
            content=text,
            tool_calls=None,
            tool_call_id=None,
            tool_name="get_manager_schema",
            tool_args={"manager": "A"},
            tool_result=schema(),
        ),
        SimpleNamespace(
            pk=8,
            conversation_id="alice/c1",
            role="assistant",
            content="Historical tool data (get_manager_schema): " + text,
            tool_calls=None,
        ),
    ]
    messages = provider_messages_from_context(rows)
    assert messages[0].historical_schema is not None
    assert messages[1].historical_schema is None
    assert "alice/c1" in messages[0].historical_schema.binding


def test_unused_table_object_and_exponential_expansion_are_rejected():
    ref, slots = fixture()
    packed = api().project_reference(ref, slots)
    packed["objects"].append({"foreign": "not selected"})
    with pytest.raises(ValueError):
        api().expand_reference(packed, ref, slots)
    packed = api().project_reference(ref, slots)
    packed["objects"] = [["x"]]
    for i in range(24):
        packed["objects"].append([{"$gm_ref": i}] * 2)
    packed["reference"]["task_evidence"][0]["payload"] = {"$gm_ref": 24}
    with pytest.raises(ValueError):
        api().expand_reference(packed, ref, slots)


def test_provider_trace_retains_original_history_and_records_transport_hashes():
    import asyncio
    from general_manager.chat.providers.base import (
        DoneEvent,
        TextChunkEvent,
        TokenUsage,
    )
    from experiments.gm_eval.profiles import ExperimentProvider, bind_provider_factory
    from experiments.gm_eval.trace import TraceRecorder

    ref, slots = fixture()
    original = api().reference_message(ref, slots)
    projected = api().compact_messages([original])

    class FakeProvider:
        async def complete(self, messages, tools):
            assert messages == projected
            yield TextChunkEvent("answer")
            yield DoneEvent(TokenUsage())

    trace = TraceRecorder()

    async def run():
        with bind_provider_factory(lambda _: FakeProvider(), trace):
            return [
                event
                async for event in ExperimentProvider({"model": "fake"}).complete(
                    projected, []
                )
            ]

    asyncio.run(run())
    assert trace.calls[0]["messages"][0]["content"] == original.content
    assert (
        trace.calls[0]["schema_projection"][0]["transport_content_sha256"]
        == projected[0].projection_receipt["transport_content_sha256"]
    )


def test_final_serialized_guard_is_unchanged_and_includes_projection():
    import asyncio
    from experiments.siwc_eval.provider import Provider, payload
    from experiments.siwc_eval.errors import EvalError
    from general_manager.chat.providers.base import Message

    ref, slots = fixture()
    ref["task_evidence"][0]["payload"]["types"] = {str(i): schema() for i in range(90)}
    original = api().reference_message(ref, slots)
    projected = api().compact_messages([original])
    assert len(json.dumps(payload("fake", [original], []))) > 200_000
    assert len(json.dumps(payload("fake", projected, []))) < 200_000
    calls = []

    class FakeTransport:
        async def stream(self, body):
            calls.append(body)
            yield {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "answer"}],
                        }
                    ]
                },
            }

    async def run(messages):
        return [
            event
            async for event in Provider("fake", FakeTransport()).complete(messages, [])
        ]

    asyncio.run(run(projected))
    assert len(calls) == 1
    assert calls[0]["input"][0]["content"] == projected[0].content
    with pytest.raises(EvalError, match="input_budget_exhausted"):
        asyncio.run(run([Message("user", "x" * 200_001)]))
    assert len(calls) == 1


def test_same_content_at_distinct_history_bindings_keeps_both_occurrences():
    from general_manager.chat.providers.base import Message

    messages = [
        api().with_historical_schema(
            Message(
                "assistant",
                "Historical tool data (get_manager_schema): " + json.dumps(schema()),
            ),
            tool_name="get_manager_schema",
            tool_result=schema(),
            binding={"user": user, "snapshot": snap, "message": index},
        )
        for index, (user, snap) in enumerate([("alice", 1), ("bob", 2)])
    ]
    ref = {
        "conversation_context": [
            {"role": m.role, "content": m.content} for m in messages
        ]
    }
    slots = api().history_slots(messages)
    packed = api().project_reference(ref, slots)
    assert len(packed["occurrences"]) == 2
    assert packed["occurrences"][0]["binding"] != packed["occurrences"][1]["binding"]
    assert api().expand_reference(packed, ref, slots) == ref
    for message in messages:
        individual = {
            "conversation_context": [{"role": message.role, "content": message.content}]
        }
        single_slots = api().history_slots([message])
        own = api().project_reference(individual, single_slots)
        assert len(own["occurrences"]) == 1
        assert api().expand_reference(own, individual, single_slots) == individual


def test_never_moves_data_to_privileged_roles_or_interprets_query_strings():
    from dataclasses import replace
    from general_manager.chat.providers.base import Message

    ref, slots = fixture()
    ref["task_evidence"][1]["payload"] = {
        "rows": [schema(), schema()],
        "error": '{"$gm_ref":0}',
    }
    ref["task_evidence"][0]["payload"]["text"] = json.dumps(
        {"$gm_ref": 999, "types": schema()}
    )
    logical = api().reference_message(ref, slots)
    result = api().compact_messages([Message("developer", "{}"), logical])
    packed = api().transport_projection(result[1].content)
    assert packed["reference"]["task_evidence"][1] == ref["task_evidence"][1]
    assert api().expand_reference(packed, ref, slots) == ref
    for role in ["system", "developer", "assistant", "tool"]:
        with pytest.raises(ValueError):
            api().compact_messages([replace(logical, role=role)])


def test_profile_and_trace_bind_transport_contract_version():
    from experiments.gm_eval.profiles import make_profile
    from dataclasses import asdict

    profile = make_profile("strong-only")
    assert asdict(profile)["schema_transport_version"] == api().VERSION
    assert profile.role_config("planner")["schema_transport_version"] == api().VERSION


def test_occurrence_boolean_index_is_not_an_integer_binding():
    ref, slots = fixture()
    packed = api().project_reference(ref, slots)
    packed["occurrences"][0]["path"][1] = False
    with pytest.raises(ValueError):
        api().expand_reference(packed, ref, slots)


def test_bad_reference_metadata_is_rejected_before_provider_dispatch():
    from dataclasses import replace

    ref, slots = fixture()
    logical = api().reference_message(ref, slots)
    tampered = replace(logical, content='REFERENCE_DATA={"task_evidence":[]}')
    with pytest.raises(ValueError):
        api().compact_messages([tampered])


def test_transport_keeps_original_reference_fields_at_top_level():
    ref, slots = fixture()
    projected = api().compact_messages([api().reference_message(ref, slots)])[0]
    wire = json.loads(projected.content.split("=", 1)[1])
    assert wire["original_request"] == ref["original_request"]
    assert wire["conversation_context"] == ref["conversation_context"]
    assert wire["schema_transport"]["format"] == api().VERSION


def test_rebinding_schema_under_existing_message_metadata_is_rejected():
    from dataclasses import replace

    ref, slots = fixture()
    message = api().reference_message(ref, slots)
    ref["task_evidence"][0]["payload"]["types"]["Alpha"]["kind"] = "FOREIGN"
    altered = replace(
        message, content="REFERENCE_DATA=" + json.dumps(ref, separators=(",", ":"))
    )
    with pytest.raises(ValueError):
        api().compact_messages([altered])
