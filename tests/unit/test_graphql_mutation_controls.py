"""Reserved manager controls must never become writable GraphQL fields."""

from typing import ClassVar

import pytest
from django.db.models import NOT_PROVIDED

from general_manager.api.graphql_mutations import create_write_fields
from general_manager.interface.base_interface import AttributeTypedDict
from tests.utils.simple_manager_interface import BaseTestInterface


@pytest.mark.parametrize("name", ["creator_id", "ignore_permission", "history_comment"])
@pytest.mark.parametrize("require_fields", [True, False])
def test_write_schema_rejects_editable_manager_control_names(
    name: str, require_fields: bool
) -> None:
    class ControlInterface(BaseTestInterface):
        @classmethod
        def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
            return {
                name: {
                    "type": str,
                    "is_required": False,
                    "is_editable": True,
                    "is_derived": False,
                    "default": NOT_PROVIDED,
                }
            }

    with pytest.raises(ValueError, match="reserved"):
        create_write_fields(ControlInterface, require_fields=require_fields)


@pytest.mark.parametrize("name", ["creator_id", "ignore_permission"])
def test_read_only_manager_control_names_are_not_writable(name: str) -> None:
    class ReadOnlyInterface(BaseTestInterface):
        input_fields: ClassVar[dict[str, object]] = {}

        @classmethod
        def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
            return {
                name: {
                    "type": str,
                    "is_required": False,
                    "is_editable": False,
                    "is_derived": False,
                    "default": NOT_PROVIDED,
                }
            }

    fields = create_write_fields(ReadOnlyInterface)

    assert fields[name].editable is False
    assert fields["history_comment"].editable is True
