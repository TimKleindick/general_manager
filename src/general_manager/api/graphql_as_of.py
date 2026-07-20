"""GraphQL schema support for historical as-of queries."""

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from graphql import (
    DirectiveLocation,
    GraphQLArgument,
    GraphQLDirective,
    GraphQLError,
    GraphQLInputType,
    GraphQLNonNull,
    GraphQLSchema,
    OperationType,
    VariableNode,
    VariablesInAllowedPositionRule,
    VariableDefinitionNode,
    get_operation_ast,
    parse,
    validate,
)
from graphql.execution.values import get_directive_values, get_variable_values
from graphql.pyutils import Undefined
from graphql.utilities import value_from_ast_untyped

from general_manager.api.graphql_errors import PublicGraphQLError
from general_manager.as_of import InvalidSearchDateError, normalize_search_date

_DUPLICATE_DIRECTIVE_MESSAGE = "Only one @asOf directive is allowed per operation."
_INVALID_LOCATION_MESSAGE = "@asOf is only allowed on query operations."
_INVALID_ARGUMENTS_MESSAGE = "@asOf requires exactly one date argument."
_UNRESOLVED_DATE_MESSAGE = "@asOf date could not be resolved."
_INVALID_DATE_MESSAGE = "@asOf date is invalid."
_MISSING_SCHEMA_DIRECTIVE_MESSAGE = "@asOf is not available in this schema."


def _public_error(message: str, code: str) -> PublicGraphQLError:
    return PublicGraphQLError(message, code=code)


def extract_as_of_search_date(
    *,
    query: str | None,
    variables: object,
    operation_name: str | None,
    schema: GraphQLSchema | None = None,
) -> datetime | None:
    """Extract and normalize ``@asOf(date:)`` from the selected operation."""
    if not isinstance(query, str):
        return None

    document = parse(query)
    operation = get_operation_ast(document, operation_name)
    if operation is None:
        return None

    directives = [
        directive
        for directive in operation.directives
        if directive.name.value == "asOf"
    ]
    if not directives:
        return None
    if len(directives) != 1:
        raise _public_error(
            _DUPLICATE_DIRECTIVE_MESSAGE,
            "HISTORICAL_CONTEXT_CONFLICT",
        )
    if operation.operation is not OperationType.QUERY:
        raise _public_error(
            _INVALID_LOCATION_MESSAGE,
            "GRAPHQL_VALIDATION_FAILED",
        )

    date_arguments = [
        argument
        for argument in directives[0].arguments
        if argument.name.value == "date"
    ]
    if len(date_arguments) != 1 or len(directives[0].arguments) != 1:
        raise _public_error(
            _INVALID_ARGUMENTS_MESSAGE,
            "GRAPHQL_VALIDATION_FAILED",
        )

    variable_values: dict[str, object] = (
        dict(cast(Mapping[str, object], variables))
        if isinstance(variables, Mapping)
        else {}
    )
    if schema is None:
        value = value_from_ast_untyped(date_arguments[0].value, variable_values)
    else:
        directive_definition = schema.get_directive("asOf")
        if directive_definition is None:
            raise _public_error(
                _MISSING_SCHEMA_DIRECTIVE_MESSAGE,
                "GRAPHQL_VALIDATION_FAILED",
            )
        date_value_node = date_arguments[0].value
        relevant_variable_definitions: tuple[VariableDefinitionNode, ...] = ()
        relevant_variable_values: dict[str, object] = {}
        if isinstance(date_value_node, VariableNode):
            variable_name = date_value_node.name.value
            relevant_variable_definitions = tuple(
                definition
                for definition in operation.variable_definitions or ()
                if definition.variable.name.value == variable_name
            )
            if variable_name in variable_values:
                relevant_variable_values[variable_name] = variable_values[variable_name]

        coerced_variables = get_variable_values(
            schema,
            relevant_variable_definitions,
            relevant_variable_values,
        )
        if isinstance(coerced_variables, list):
            raise _public_error(_INVALID_DATE_MESSAGE, "BAD_USER_INPUT")
        try:
            directive_values = get_directive_values(
                directive_definition,
                operation,
                coerced_variables,
            )
        except GraphQLError as error:
            raise _public_error(_INVALID_DATE_MESSAGE, "BAD_USER_INPUT") from error
        if directive_values is None or "date" not in directive_values:
            raise _public_error(_UNRESOLVED_DATE_MESSAGE, "BAD_USER_INPUT")

        variable_position_errors = validate(
            schema,
            document,
            rules=(VariablesInAllowedPositionRule,),
        )
        if variable_position_errors:
            raise _public_error(_INVALID_DATE_MESSAGE, "BAD_USER_INPUT")
        validation_errors = validate(schema, document)
        if validation_errors:
            raise validation_errors[0]
        value = directive_values["date"]

    if value is Undefined or value is None:
        raise _public_error(
            _UNRESOLVED_DATE_MESSAGE,
            "BAD_USER_INPUT",
        )
    try:
        return normalize_search_date(value)
    except InvalidSearchDateError as error:
        raise _public_error(_INVALID_DATE_MESSAGE, "BAD_USER_INPUT") from error


def build_as_of_directive(date_time_type: GraphQLInputType) -> GraphQLDirective:
    """Build the query-only ``@asOf`` directive with the schema's DateTime type."""
    return GraphQLDirective(
        name="asOf",
        description="Execute this query against one historical snapshot.",
        locations=(DirectiveLocation.QUERY,),
        args={
            "date": GraphQLArgument(
                GraphQLNonNull(date_time_type),
                description="Historical snapshot date.",
            )
        },
    )
