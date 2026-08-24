# Lazy DevSearch hydration

Use the built-in `DevSearchBackend` when a local Django process needs a
service-free search projection. This recipe enables per-index hydration on the
first search, keeps later same-process manager changes visible, and makes the
multi-term matching contract explicit.

## Configure the disposable backend

Put the backend and setting in the same `GENERAL_MANAGER` mapping:

```python
GENERAL_MANAGER = {
    "SEARCH_BACKEND": {
        "class": "general_manager.search.backends.dev.DevSearchBackend",
        "options": {},
    },
    "SEARCH_AUTO_REINDEX": True,
}
```

`SEARCH_AUTO_REINDEX` is opt-in and applies only to the selected
`DevSearchBackend`; it is independent of `DEBUG`. The nested setting takes
precedence over a top-level `SEARCH_AUTO_REINDEX` value. External search
backends ignore this DevSearch-specific setting.

## Declare an index and search it

Add a `SearchConfig` to the manager whose fields should be searchable:

```python
from general_manager import GeneralManager, IndexConfig


class Project(GeneralManager):
    class SearchConfig:
        indexes = (
            IndexConfig(
                name="global",
                fields=("name", "status"),
                filters=("status",),
            ),
        )
```

With the setting enabled, the first search for `global` reindexes the
configured `Project` managers in the serving process before evaluating the
query:

```python
from general_manager.search.backend_registry import get_search_backend


backend = get_search_backend()
result = backend.search("global", "alpha public", filters={"status": "public"})

for hit in result.hits:
    print(hit.id, hit.score)
```

Every distinct query term must match at least one indexed field token, so
`"alpha public"` can match `alpha` in `name` and `public` in `status`, while a
document missing either term is excluded. Repeating a term, as in
`"alpha alpha"`, does not increase its score.

## Opt in a directly constructed backend

Direct construction stays inert by default. Enable hydration explicitly when a
programmatic caller owns the backend instance:

```python
from general_manager.search.backends.dev import DevSearchBackend


backend = DevSearchBackend(auto_reindex=True)
# Equivalent after construction:
# backend.configure_auto_reindex(True)
result = backend.search("global", "alpha")
```

Hydration is tracked independently per index. If manager discovery or
reindexing raises, the original exception reaches the triggering search and a
later search retries that index. After successful hydration, synchronous
manager lifecycle updates in the same process upsert or delete documents as
usual.

## Keep the process boundary in mind

DevSearch is an in-memory, process-local projection. A separate
`python manage.py search_index --reindex` process cannot populate the backend of
an already running server, and writes made by another process are not observed.
Restart the serving process so its next search can hydrate a fresh projection.
Use a persistent external backend for production or cross-process search.

For the model and lifecycle details, see the [search concept page](../concepts/search.md)
and the [search how-to](../howto/search.md). The generated signatures and
compatibility notes are in the [Search API reference](../api/search.md).
