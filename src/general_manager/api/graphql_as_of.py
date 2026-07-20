"""GraphQL schema support for historical as-of queries."""

from graphql import (
    DirectiveLocation,
    GraphQLArgument,
    GraphQLDirective,
    GraphQLInputType,
    GraphQLNonNull,
)


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
