"""Direct manifest discovery remains bound to the exact exposed contract."""

import hashlib
import json

import pytest

from general_manager.chat import graphql_contract as contract
from general_manager.chat.schema_inspection import (
    inspect_manager_schema,
    SchemaInspectionError,
)
from tests.unit.test_schema_inspection import exposed_schema as exposed_schema

pytestmark = pytest.mark.usefixtures("exposed_schema")


def test_overview_discovers_direct_types_without_transitive_input_expansion():
    overview = inspect_manager_schema("Part")
    full = inspect_manager_schema("Part", view="full")
    assert overview["inspection_version"] == 3
    assert "NestedFilter" not in overview["type_manifest"]
    assert "OtherFilter" not in overview["type_manifest"]
    assert overview["type_manifest"]["PartFilter"] == {"kind": "input"}
    assert overview["type_manifest"]["Other"] == {
        "kind": "reference",
        "manager": "Other",
    }
    assert overview["type_manifest"]["Mystery"] == {"kind": "unsupported"}
    assert overview["root_fields"] == full["root_fields"]
    assert overview["output_fields"] == full["types"]["Part"]["fields"]
    assert len(overview["type_manifest"]) < len(full["types"])


def test_details_discover_the_next_exact_inputs_and_enum_defaults():
    overview = inspect_manager_schema("Part")
    first = inspect_manager_schema(
        "Part", view="detail", types=["PartFilter"], snapshot=overview["snapshot"]
    )
    assert set(first["type_manifest"]) == {
        "PartFilter",
        "String",
        "NestedFilter",
        "OtherFilter",
    }
    nested = inspect_manager_schema(
        "Part", view="detail", types=["NestedFilter"], snapshot=first["snapshot"]
    )
    assert set(nested["type_manifest"]) == {"NestedFilter", "State"}
    assert nested["types"]["NestedFilter"]["fields"]["state"]["default"] == "OPEN"
    enum = inspect_manager_schema(
        "Part", view="detail", types=["State"], snapshot=nested["snapshot"]
    )
    assert enum["types"]["State"] == {"kind": "enum", "values": ["OPEN", "CLOSED"]}
    full = inspect_manager_schema("Part", view="full")
    for detail in (first, nested, enum):
        assert detail["inspection_version"] == 3
        assert detail["manager"] == "Part"
        assert detail["snapshot"] == overview["snapshot"]
        assert all(
            definition == full["types"][name]
            for name, definition in detail["types"].items()
        )


def test_relation_details_stop_at_reference_and_target_requires_own_snapshot():
    own = inspect_manager_schema("Part")
    relation = inspect_manager_schema(
        "Part", view="detail", types=["Other"], snapshot=own["snapshot"]
    )
    assert relation["type_manifest"] == {
        "Other": {"kind": "reference", "manager": "Other"}
    }
    assert relation["types"] == relation["type_manifest"]
    other = inspect_manager_schema("Other")
    with pytest.raises(SchemaInspectionError, match="schema_snapshot_mismatch"):
        inspect_manager_schema(
            "Other", view="detail", types=["Other"], snapshot=own["snapshot"]
        )
    target = inspect_manager_schema(
        "Other", view="detail", types=["Other"], snapshot=other["snapshot"]
    )
    assert target["types"]["Other"]["fields"] == {"secret": {"type": "String"}}
    assert set(target["type_manifest"]) == {"Other", "String"}


def test_unsupported_union_is_explicit_and_never_expanded():
    own = inspect_manager_schema("Part")
    unsupported = inspect_manager_schema(
        "Part", view="detail", types=["Mystery"], snapshot=own["snapshot"]
    )
    assert unsupported["types"] == {"Mystery": {"kind": "unsupported"}}
    assert unsupported["type_manifest"] == unsupported["types"]


def test_prior_projection_snapshot_cannot_authorize_new_details():
    full, current = contract.capture_manager_contract("Part")
    legacy = hashlib.sha256(
        json.dumps(
            {
                "projection_version": 2,
                "manager": "Part",
                "contract": full,
                "exposure": {"Part": True, "Other": True},
                "visibility": {"Part": True, "Other": True, "OtherFilter": True},
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert current != legacy
    with pytest.raises(SchemaInspectionError, match="schema_snapshot_mismatch"):
        inspect_manager_schema(
            "Part", view="detail", types=["PartFilter"], snapshot=legacy
        )
