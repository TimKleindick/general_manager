"""GraphQL schema support for historical as-of queries."""

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from graphql import (
    DirectiveLocation,
    GraphQLArgument,
    GraphQLDirective,
    GraphQLInputType,
    GraphQLNonNull,
    OperationType,
    get_operation_ast,
    parse,
)
from graphql.pyutils import Undefined
from graphql.utilities import value_from_ast_untyped

from general_manager.api.graphql_errors import PublicGraphQLError
from general_manager.as_of import InvalidSearchDateError, normalize_search_date

_DUPLICATE_DIRECTIVE_MESSAGE = "Only one @asOf directive is allowed per operation."
_INVALID_LOCATION_MESSAGE = "@asOf is only allowed on query operations."
_INVALID_ARGUMENTS_MESSAGE = "@asOf requires exactly one date argument."
_UNRESOLVED_DATE_MESSAGE = "@asOf date could not be resolved."


def _public_error(message: str, code: str) -> PublicGraphQLError:
    return PublicGraphQLError(message, code=code)


def extract_as_of_search_date(
    *,
    query: str | None,
    variables: object,
    operation_name: str | None,
) -> datetime | None:
    """Extract and normalize ``@asOf(date:)`` from the selected operation."""
    if query is None:
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

    variable_values = (
        dict(cast(Mapping[str, object], variables))
        if isinstance(variables, Mapping)
        else {}
    )
    value = value_from_ast_untyped(date_arguments[0].value, variable_values)
    if value is Undefined or value is None:
        raise _public_error(
            _UNRESOLVED_DATE_MESSAGE,
            "BAD_USER_INPUT",
        )
    try:
        return normalize_search_date(value)
    except InvalidSearchDateError as error:
        raise _public_error(str(error), "BAD_USER_INPUT") from error


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
