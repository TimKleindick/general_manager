"""The advertised filter shorthand and root argument have the same native shape."""

import pytest

from general_manager.chat.tool_metadata import TOOL_INPUT_SCHEMAS, TOOL_USAGE_EXAMPLES
from general_manager.chat.tools import query
from tests.unit.test_chat_graphql_native import native_schema  # noqa: F401


def test_query_examples_pair_direct_filter_fields_with_exact_root_argument():
    examples = [value for name, value in TOOL_USAGE_EXAMPLES if name == "query"]
    direct = next(
        value for value in examples if value.get("filters") == {"name": "Apollo"}
    )
    expected = {key: value for key, value in direct.items() if key != "filters"}
    expected["arguments"] = {"filter": direct["filters"]}
    assert expected in examples
    description = TOOL_INPUT_SCHEMAS["query"]["properties"]["filters"]["description"]
    assert "direct fields" in description
    assert "do not wrap" in description.lower()


@pytest.mark.usefixtures("native_schema")
def test_direct_filters_and_exact_arguments_execute_but_double_wrapper_is_invalid():
    direct = query(manager="Material", fields=["code"], filters={"isActive": True})
    argument = query(
        manager="Material",
        fields=["code"],
        filters={},
        arguments={"filter": {"isActive": True}},
    )
    assert direct == argument
    assert len(direct["data"]) == 3
    with pytest.raises(ValueError):
        query(
            manager="Material", fields=["code"], filters={"filter": {"isActive": True}}
        )
    with pytest.raises(ValueError):
        query(
            manager="Material",
            fields=["code"],
            filters={"isActive": True},
            arguments={"filter": {"isActive": True}},
        )
