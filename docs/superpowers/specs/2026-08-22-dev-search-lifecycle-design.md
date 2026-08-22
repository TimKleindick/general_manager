# DevSearch Lifecycle and All-Term Matching Design

## Goal

Make the service-free development search backend usable from a running Django
server without introducing persistent search storage. The database and other
manager interfaces remain the source of truth; each process-local DevSearch
index is a disposable projection.

Also correct multi-term matching so that a document is eligible only when every
distinct query term matches.

## Scope

This change applies only to `DevSearchBackend`. External backends retain their
existing lifecycle and native query behavior. The change adds no files used as
runtime storage, database models, migrations, dependencies, or cross-process
cache-coordination mechanism.

Cross-process freshness is deliberately outside this design. A development
server does not observe changes made by another process until it restarts and
hydrates again. The normal supported development configuration uses synchronous
search invalidation so changes made by the serving process update its in-memory
projection immediately.

## Configuration

The existing `SEARCH_AUTO_REINDEX` setting controls lazy hydration. It is
effective when the selected backend is `DevSearchBackend` and remains disabled
by default unless explicitly enabled.

Backend instances constructed directly remain isolated unless their caller
explicitly opts into automatic hydration. This prevents ordinary unit tests and
programmatic uses from unexpectedly discovering and indexing globally
registered managers.

The example project keeps `SEARCH_AUTO_REINDEX` enabled, so its runserver works
without first running `search_index --reindex` in a separate process.

## Lazy Hydration

On the first `search(index_name, ...)` for an unhydrated index, an opted-in
DevSearch backend reindexes every searchable manager configured for that index
into the same backend instance. Hydration happens in the process performing the
search, so the resulting documents are available to that search immediately.

Hydration is tracked per index rather than globally. A lock prevents concurrent
first searches from rebuilding the same index more than once. The index is
marked hydrated only after all relevant managers reindex successfully. If
hydration fails, the search raises the original error and a later search may
retry; the backend must not silently return a partial index as successfully
hydrated.

Reindexing uses the existing `SearchIndexer` and registry helpers. The backend
uses a guarded internal path so indexing operations performed during hydration
cannot recursively trigger another hydration attempt.

The `search_index --reindex` command remains useful for persistent external
backends. Running it in a separate process is not presented as a way to fill a
DevSearch backend.

## Change Maintenance

After hydration, the existing search invalidation hooks continue to call
upsert/delete operations on the active backend for changes made in that process.
No second change-tracking system is added.

Documentation will state that asynchronous invalidation or writes performed by
other processes cannot keep a process-local DevSearch index current. Restarting
the server rebuilds the projection on its next search.

## Query Matching

Query and document tokenization remain lowercase, whitespace-based, prefix
matching. Phrase parsing, fuzzy matching, stemming, and production-backend
parity remain out of scope.

Before ranking, each distinct query token must equal or prefix at least one
indexed field token in the document. If any term has no match, the document is
excluded. Once eligible, the existing field-boost and index-boost calculation
ranks results. Repeated identical query terms do not impose duplicate matching
requirements or inflate relevance scores.

Empty queries retain their current behavior and match all documents that pass
type and structured filters.

## Error Handling

- Lazy-hydration discovery, serialization, and source-read errors propagate to
  the triggering search.
- A failed hydration is retryable because its index is not marked hydrated.
- Ordinary search behavior is unchanged when automatic hydration is disabled.
- External search backends never enter the DevSearch hydration path.

## Tests

Tests will verify:

- a multi-term query excludes a document when only some terms match;
- every term may match a different searchable field;
- duplicate terms do not inflate scores;
- empty-query behavior remains unchanged;
- opted-in DevSearch hydrates an index once on its first search;
- hydration is tracked independently per index;
- hydration failures propagate and remain retryable;
- disabled, non-debug, directly constructed, and non-Dev backends do not
  auto-hydrate;
- a database-backed integration search works without an earlier management
  command reindex; and
- existing synchronous invalidation keeps an already hydrated backend current.

Relevant unit tests, Ruff, formatting, mypy, and the focused integration tests
will be run before completion.
