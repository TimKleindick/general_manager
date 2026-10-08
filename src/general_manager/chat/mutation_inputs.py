"""Schema-typed mutation documents; data is passed only through variables."""

from collections.abc import Callable, Mapping
from typing import Any
from graphql import GraphQLSchema, is_input_object_type, is_list_type, is_non_null_type


def _input_value(value: Any, field_type: Any, camelize: Callable[[str], str]) -> Any:
    if is_non_null_type(field_type):
        field_type = field_type.of_type
    if is_list_type(field_type):
        return (
            [_input_value(item, field_type.of_type, camelize) for item in value]
            if isinstance(value, (list, tuple))
            else _input_value(value, field_type.of_type, camelize)
        )
    if is_input_object_type(field_type) and isinstance(value, Mapping):
        return _named_values(value, field_type.fields, camelize)
    return value


def _named_values(
    values: Mapping[str, Any], fields: Mapping[str, Any], camelize: Callable[[str], str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        name = key if key in fields else camelize(key)
        if name not in fields or name in result:
            message = f"Unknown or duplicate mutation input {key!r}."
            raise ValueError(message)
        result[name] = _input_value(value, fields[name].type, camelize)
    return result


def mutation_document(
    schema: GraphQLSchema,
    mutation: str,
    inputs: Mapping[str, Any],
    camelize: Callable[[str], str],
) -> tuple[str, dict[str, Any]]:
    """Use executable input types, preserving enum and multiline string values."""
    if schema.mutation_type is None or mutation not in schema.mutation_type.fields:
        message = "Mutation is not present in the executable schema."
        raise ValueError(message)
    field = schema.mutation_type.fields[mutation]
    values = _named_values(inputs, field.args, camelize)
    declarations = []
    arguments = []
    variables = {}
    for index, (name, value) in enumerate(values.items()):
        variable = f"arg{index}"
        declarations.append(f"${variable}: {field.args[name].type}")
        arguments.append(f"{name}: ${variable}")
        variables[variable] = value
    declaration = "(" + ", ".join(declarations) + ")" if declarations else ""
    arguments_text = "(" + ", ".join(arguments) + ")" if arguments else ""
    return (
        f"mutation ChatMutation{declaration} {{ {mutation}{arguments_text} {{ success }} }}",
        variables,
    )
