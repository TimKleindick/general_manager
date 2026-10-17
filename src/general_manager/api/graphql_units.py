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


def _identity_attribute(interface: Any, identity: Mapping[str, str]) -> str | None:
    """Bind constructor aliases through shared attribute metadata, never ORM fields."""
    attributes = interface.get_attribute_types()
    primary = {
        name: info
        for name, info in attributes.items()
        if info.get("is_primary_key") is True
    }
    if not primary:
        return identity["input_name"]
    candidates = [
        name
        for name, info in primary.items()
        if info.get("is_identifier") is True
        and info.get("type") is {"int": int, "str": str}[identity["input_type"]]
    ]
    return candidates[0] if len(candidates) == 1 else None


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
        generated = types.get(name)
        metadata = getattr(generated, "_meta", None)
        native = schema.get_type(getattr(metadata, "name", ""))
        if not isinstance(native, GraphQLObjectType) and not declarations:
            continue
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
            attribute = _identity_attribute(interface, identity)
            if (
                attribute is not None
                and metadata is not None
                and attribute in metadata.fields
            ):
                target = native.fields[_public_name(metadata, native, attribute)]
                target.extensions = {**target.extensions, IDENTITY_EXTENSION: identity}
    from general_manager.api.graphql_value_contract import attach_value_contracts
    from general_manager.api.graphql_filter_contract import attach_filter_contracts

    attach_value_contracts(schema, managers, types)
    attach_filter_contracts(schema, managers, types)
