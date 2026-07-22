# Historical Context API

The stable Python imports for historical execution are available from
`general_manager.api`:

```python
from general_manager.api import (
    HistoricalContextConflictError,
    HistoricalMutationError,
    HistoricalReadNotSupportedError,
    InvalidSearchDateError,
    as_of,
    current_as_of_date,
)
```

The API is additive in GeneralManager 0.66.0. Existing explicit
`search_date` constructor and query arguments remain supported; use `as_of(...)`
when one date should govern an entire operation.

## Python context

::: general_manager.as_of.as_of

`as_of(search_date)` is a context manager. `search_date` is the accepted
`str | datetime.date | datetime.datetime` input; ISO strings may contain a date,
an ISO datetime, or a trailing `Z`. The yielded value is the normalized,
timezone-aware `datetime`. Date-only and naive values use Django's current
timezone, while aware values retain their timezone.

Nesting with the same instant is idempotent. A different instant raises
`HistoricalContextConflictError` before the nested body runs. The context
restores the previous value on normal and exceptional exit. It does not detect
arbitrary application writes made directly through Django or another client.

::: general_manager.as_of.current_as_of_date

`current_as_of_date()` returns the active aware `datetime`, or `None` when no
historical context is active. The value is scoped by Python `contextvars`: async
tasks inherit it normally, unrelated threads do not, and exited contexts do not
leak it.

## Exceptions

::: general_manager.as_of.InvalidSearchDateError

`InvalidSearchDateError(value)` is a `ValueError` raised when `search_date`
cannot be parsed or normalized. The original parsing or timezone failure is
chained as `__cause__`.

::: general_manager.as_of.HistoricalContextConflictError

`HistoricalContextConflictError()` is a `RuntimeError` raised when nested,
explicit, manager, bucket, or lazy relation values represent different
historical instants—or when a snapshot-bound transparent value is consumed
outside its context.

::: general_manager.as_of.HistoricalMutationError

`HistoricalMutationError(active: datetime | None = None)` is a `RuntimeError`
raised before GeneralManager or direct interface `create`, `update`, or
`delete` work proceeds. The optional `active` value is included in the default
message when the guard supplies the current snapshot.

::: general_manager.as_of.HistoricalReadNotSupportedError

`HistoricalReadNotSupportedError(interface_name: str | None = None)` is a
`RuntimeError` raised when a request-backed, custom, or history-incomplete read
cannot honor the active snapshot. The optional interface name is included in
the default message; ORM capability failures remain available as the chained
cause where applicable.

## GraphQL schema contract

Generated schemas include the following built-in directive:

```graphql
directive @asOf(date: DateTime!) on QUERY
```

The selected query operation may use one literal or variable `date`. The
directive is not available on fields, mutations, or subscriptions. GeneralManager
normalizes the value before resolver execution, restores the context after each
operation, and isolates dates across GraphQL batch items. Historical failures
are returned as public GraphQL errors with these extension codes:

| Condition | `extensions.code` |
| --- | --- |
| Invalid or unresolved date | `BAD_USER_INPUT` |
| Conflicting snapshot | `HISTORICAL_CONTEXT_CONFLICT` |
| Historical mutation | `HISTORICAL_MUTATION_FORBIDDEN` |
| Unsupported historical read | `HISTORICAL_READ_NOT_SUPPORTED` |
| Invalid directive location or shape | `GRAPHQL_VALIDATION_FAILED` |

The [historical query how-to](../howto/historical_queries.md) shows requests,
the [cookbook](../examples/historical_queries.md) provides directly usable
Python and GraphQL examples, and the [GraphQL API reference](graphql.md)
documents the generated schema and error boundary in more detail.
