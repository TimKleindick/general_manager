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
