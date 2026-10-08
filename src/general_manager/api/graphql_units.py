"""Install shared public unit declarations on executable GraphQL fields."""

from collections.abc import Mapping
from typing import Any

from graphene.utils.str_converters import to_camel_case
from graphql import GraphQLSchema, GraphQLObjectType

from general_manager.interface.unit_contract import UNIT_EXTENSION, IDENTITY_EXTENSION


def _public_name(metadata: Any, native: GraphQLObjectType, attribute: str) -> str:
    field = metadata.fields.get(attribute)
    value = getattr(field, "name", None) or to_camel_case(attribute)
    if field is None or value not in native.fields:
        message = "unit declarations must bind an actual public GraphQL field"
        raise ValueError(message)
    return str(value)


def attach_unit_contracts(
    schema: GraphQLSchema,
    managers: Mapping[str, Any],
    types: Mapping[str, Any],
) -> None:
    """Bind interface names through the actual Graphene field declarations.

    Unsupported/undeclared interfaces contribute nothing. No ORM reflection,
    resolver inference, private extension copy or model-supplied metadata.
    """
    for name, manager in managers.items():
        interface = getattr(manager, "Interface", None)
        provider = getattr(interface, "get_field_unit_contracts", None)
        if not callable(provider):
            continue
        declarations = provider()
        if not declarations:
            continue
        generated = types.get(name)
        metadata = getattr(generated, "_meta", None)
        native = schema.get_type(getattr(metadata, "name", ""))
        if not isinstance(native, GraphQLObjectType):
            message = "unit declarations require the manager's executable GraphQL type"
            raise TypeError(message)

        for attribute, declaration in declarations.items():
            target = native.fields[_public_name(metadata, native, attribute)]
            if declaration["kind"] == "quantity":
                declaration["unit_field"] = _public_name(
                    metadata, native, declaration["unit_field"]
                )
            target.extensions = {**target.extensions, UNIT_EXTENSION: declaration}
        identity_provider = getattr(interface, "get_unit_identity_contract", None)
        identity = identity_provider() if callable(identity_provider) else None
        if identity is not None:
            target = native.fields[
                _public_name(metadata, native, identity["input_name"])
            ]
            target.extensions = {**target.extensions, IDENTITY_EXTENSION: identity}
