# Structured read input errors

Use the exact filter and argument shapes advertised by the exposed manager's
GraphQL contract. A relation quantifier `none` is unsupported anywhere inside
an `exclude` input, even when the generated input type exposes that field.
It can be used in a `filter` input only when that input advertises it. This
restriction also applies to nested relation inputs and nested selections.

For example, `exclude: {materialsList: {none: {id_In: [2]}}}` is rejected.
The existing resolver raises `UnsupportedExcludeNoneRelationFilterError`.
The Python `query` tool preserves that exception when all GraphQL execution
errors are this known input error. The planned chat executor returns
`status=error`, `code=invalid_graphql_request`, and a message explaining the
restriction. This lets the current read workflow choose an allowed query shape
without treating an invalid input as new record evidence.

No additional meaning for `exclude.none` is introduced. Successful partial
GraphQL data are not returned when execution reports an error. If any other
resolver error accompanies the known error, the general error path remains
active; matching the message text of a generic `ValueError` does not turn it
into a known input error. Unknown resolver and transport failures retain their
existing infrastructure classification.

The experimental read replay recognizes the same exception type as a local
model input failure. A recovered new run can still complete normally; an
independent replay never supplies replacement evidence to the model. This
classification change applies to new runs and separately versioned offline
replays. Previously frozen results are not rewritten.

## Database statement budgets

On PostgreSQL, the read tool applies the smaller of the configured statement
timeout and the remaining planned-query budget. A boolean context value does
not supply a planned timeout. The transaction and `SET LOCAL statement_timeout`
start when the query first executes SQL on the default connection and remain
scoped to that GraphQL execution. They are cleaned up when execution finishes
or raises an error.

Nested read queries use the shortest active statement budget. After an inner
query finishes, the next outer statement receives the outer query's budget
again. If transaction or timeout setup fails, later GraphQL resolvers in that
query cannot continue executing SQL. Transaction cleanup and later independent
queries retain their own scopes, including queries started by commit callbacks.

An Interface that completes a query without SQL does not open the default
database merely to install this budget. Installing the deferred SQL execution
hook does not access the database. This preserves queries implemented by
calculation, read-only, and other non-SQL Interfaces when the application's
default database is PostgreSQL. Statement timeout behavior on other database
backends is unchanged.
