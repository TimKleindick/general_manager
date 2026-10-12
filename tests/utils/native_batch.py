"""Reuse preparation only for read scenarios with identical immutable fixtures.

Cold-start, schema-mutation and cancellation scenarios retain their own children.
Every program has a separate namespace, scope and harness-created conversation.
The original business seed, schema generation and prior durable rows must survive.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from tests.experiments.test_gm_eval_harness import _child


def run_native_programs(
    programs: Mapping[str, str],
    *,
    manager_count: int,
    snapshot: str = "RANK",
    variant: str = "weight",
    seed: int = 17,
) -> dict[str, Any]:
    configuration = {
        "programs": dict(programs),
        "fixture": {
            "manager_count": manager_count,
            "snapshot": snapshot,
            "variant": variant,
            "seed": seed,
        },
    }
    program = (
        "configuration = json.loads(CONFIGURATION)\n"
        + r"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
from unittest.mock import patch
from django.db import connections
from experiments.gm_eval.runtime import bootstrap, FixtureRuntime

prepared = bootstrap(**configuration['fixture'])
from general_manager.api.graphql import GraphQL
from general_manager.chat.graphql_contract import compile_read_contract
from general_manager.chat.models import ChatConversation, ChatMessage

expected_schema = GraphQL.get_schema()
expected_contract = compile_read_contract(expected_schema.graphql_schema)

class ProgramRuntime:
    def __init__(self):
        self.scope = deepcopy(prepared.scope)
        for name in ('catalog', 'schema_index', 'seed_data', 'blocks', 'startup_managers'):
            setattr(self, name, deepcopy(getattr(prepared, name)))

    def __getattr__(self, name):
        return getattr(prepared, name)

    def tool(self, name, arguments):
        return FixtureRuntime.tool(self, name, arguments)

    def close(self):
        pass  # Only the batch owns and closes the prepared database.

def durable_rows(model, identifiers=None):
    rows = model.objects.order_by('pk')
    if identifiers is not None:
        rows = rows.filter(pk__in=identifiers)
    return list(rows.values())

results = {}
try:
    for name, source in configuration['programs'].items():
        context = ProgramRuntime()
        def reuse(*args, **kwargs):
            assert not args and kwargs == configuration['fixture'], 'fixture_configuration_mismatch'
            return context
        prior_conversations = durable_rows(ChatConversation)
        prior_messages = durable_rows(ChatMessage)
        output = io.StringIO()
        namespace = {'__name__': '__native_test_program__'}
        with patch('experiments.gm_eval.runtime.bootstrap', reuse), redirect_stdout(output):
            exec(compile(source, '<native-test-program>', 'exec'), namespace)
        results[name] = json.loads(output.getvalue().strip().splitlines()[-1])
        assert GraphQL.get_schema() is expected_schema, 'schema_replaced_in_shared_fixture'
        assert compile_read_contract(expected_schema.graphql_schema) is expected_contract, 'schema_rebuilt_in_shared_fixture'
        assert durable_rows(ChatConversation, [row['id'] for row in prior_conversations]) == prior_conversations, 'prior_conversation_changed'
        assert durable_rows(ChatMessage, [row['id'] for row in prior_messages]) == prior_messages, 'prior_message_changed'
        prepared.census()
        prepared.source_integrity()
        connections.close_all()
    print(json.dumps(results))
finally:
    prepared.close()
"""
    )
    program = "import json\n" + program.replace(
        "CONFIGURATION", json.dumps(json.dumps(configuration))
    )
    return _child(program)
