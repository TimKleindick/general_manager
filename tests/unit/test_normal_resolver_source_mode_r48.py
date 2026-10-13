"""Native scalar certificates retain their exact collection authorization mode."""

import pytest

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_resolvers import (
    create_normal_resolver,
    field_value_source,
)
from general_manager.chat.graphql_contract import clear_contract_cache
from general_manager.chat.schema_inspection import inspect_manager_schema
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
    _binding,
    _call,
    _lookup,
    _public_runner,
)
from tests.unit.test_output_value_authority_r26 import (
    real_category_source as _real_category_fixture,
)

category_schema = _category_fixture
real_category_source = _real_category_fixture


@pytest.mark.parametrize("explicit_default", [False, True])
def test_native_default_normal_getter_attests_exact_identity(explicit_default):
    resolver = (
        create_normal_resolver("family", authorize_manager_collection=False)
        if explicit_default
        else create_normal_resolver("family")
    )

    assert field_value_source(resolver) == ("family", "identity")


@pytest.mark.parametrize(
    "unsupported_mode",
    [
        pytest.param(True, id="permission-filtered-members"),
        pytest.param(0, id="integer-zero"),
        pytest.param(None, id="none"),
        pytest.param("", id="empty-string"),
    ],
)
def test_other_native_getter_modes_cannot_attest_identity(unsupported_mode):
    resolver = create_normal_resolver(
        "family", authorize_manager_collection=unsupported_mode
    )

    assert field_value_source(resolver) is None


@pytest.mark.parametrize(
    ("capture", "replacement"),
    [
        pytest.param("authorize_manager_collection", True, id="enable-member-filter"),
        pytest.param("authorize_manager_collection", 0, id="replace-bool-with-zero"),
        pytest.param("field_name", "name", id="different-source-field"),
    ],
)
def test_capture_change_revokes_current_category_and_snapshot(
    real_category_source, monkeypatch, capture, replacement
):
    schema, _manager = real_category_source
    runner = _public_runner(schema.manager)
    _, valid = _lookup(runner, schema)
    assert valid
    arguments = {
        "manager": schema.manager,
        "fields": ["id", "family"],
        "filters": {"family_Exact": schema.actual},
        "category_bindings": [_binding()],
    }
    result, valid = _call(runner, "query", **arguments)
    assert valid and result["complete"] is True
    assert [row["id"] for row in result["data"]] == [1, 2]

    before = inspect_manager_schema(schema.manager, view="full")["snapshot"]
    resolver = (
        GraphQL._schema.graphql_schema.get_type(schema.manager).fields["family"].resolve
    )
    assert field_value_source(resolver) == ("family", "identity")
    cells = dict(zip(resolver.__code__.co_freevars, resolver.__closure__, strict=True))
    monkeypatch.setattr(cells[capture], "cell_contents", replacement)
    clear_contract_cache()

    assert field_value_source(resolver) is None
    assert inspect_manager_schema(schema.manager, view="full")["snapshot"] != before
    calls_before = len(schema.calls)
    result, valid = _call(runner, "query", **arguments)
    assert not valid and result == {
        "status": "error",
        "code": "invalid_category_binding",
    }
    assert len(schema.calls) == calls_before
