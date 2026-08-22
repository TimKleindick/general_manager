# DevSearch Lifecycle and All-Term Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the process-local DevSearch backend hydrate itself from configured managers on first use in development and require every distinct query term to match.

**Architecture:** Keep DevSearch as a disposable in-memory projection. First implement all-term eligibility inside its existing scorer, then add opt-in, per-index lazy hydration using `SearchIndexer.reindex_manager_index()` and enable it through the existing `SEARCH_AUTO_REINDEX` plus `DEBUG` settings path.

**Tech Stack:** Python 3.12+, Django, pytest/pytest-django, standard-library threading.

**Spec:** `docs/superpowers/specs/2026-08-22-dev-search-lifecycle-design.md`

## Global Constraints

- `DevSearchBackend` remains process-local and uses no persistent runtime files.
- Add no models, migrations, dependencies, or cross-process coordination.
- External backend lifecycle and query behavior remain unchanged.
- Automatic hydration requires both `SEARCH_AUTO_REINDEX` and Django `DEBUG`.
- Directly constructed DevSearch instances remain isolated unless explicitly opted in.
- Hydration failures propagate and remain retryable.
- Existing synchronous invalidation remains the only post-hydration maintenance mechanism.
- Follow strict TDD: add each behavioral test and observe its expected failure before production changes.

---

### Task 1: Require Every Distinct Query Term

**Files:**
- Modify: `src/general_manager/search/backends/dev.py:225-309`
- Test: `tests/unit/test_search_dev_backend.py`

**Interfaces:**
- Consumes: `DevSearchBackend.search()` and its existing `_tokenize_query()` / `_score_document()` helpers.
- Produces: `_tokenize_query(query: str) -> list[str]` returning distinct lowercase terms in first-seen order; `_score_document(...) -> float` returning zero unless every term matches at least one document field.

- [ ] **Step 1: Add failing all-term and cross-field tests**

Extend the existing fixture with focused assertions equivalent to:

```python
def test_search_requires_every_distinct_query_term(self) -> None:
    result = self.backend.search("global", "Alpha missing")
    assert result.total == 0


def test_search_allows_query_terms_to_match_different_fields(self) -> None:
    result = self.backend.search("global", "Alpha public")
    assert [hit.id for hit in result.hits] == ["Project:1"]


def test_repeated_query_terms_do_not_inflate_score(self) -> None:
    single = self.backend.search("global", "Alpha")
    repeated = self.backend.search("global", "Alpha Alpha")
    assert repeated.hits[0].score == single.hits[0].score
```

The production mutation caught by these tests is restoring the current
any-positive-score eligibility or preserving duplicate query tokens.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m pytest \
  tests/unit/test_search_dev_backend.py::DevSearchBackendTests::test_search_requires_every_distinct_query_term \
  tests/unit/test_search_dev_backend.py::DevSearchBackendTests::test_search_allows_query_terms_to_match_different_fields \
  tests/unit/test_search_dev_backend.py::DevSearchBackendTests::test_repeated_query_terms_do_not_inflate_score -q
```

Expected: the first test fails because `Project:1` is returned, and the duplicate-score test fails because the score is doubled; the cross-field test documents intended behavior and may already pass.

- [ ] **Step 3: Implement distinct-token all-term scoring**

Change tokenization to preserve first-seen order while deduplicating:

```python
@staticmethod
def _tokenize_query(query: str) -> list[str]:
    return list(dict.fromkeys(query.lower().split()))
```

Score one query term at a time. Accumulate every matching field boost for that
term, but reject the document immediately when a term matches no field:

```python
score = 0.0
for token in tokens:
    token_score = 0.0
    for field_name, field_tokens in token_index.items():
        if token in field_tokens or any(
            field_token.startswith(token) for field_token in field_tokens
        ):
            token_score += document.field_boosts.get(field_name, 1.0)
    if token_score <= 0:
        return 0.0
    score += token_score
```

Retain the existing empty-query and `index_boost` behavior. Update the method
docstrings to say that all distinct query tokens are required.

- [ ] **Step 4: Run focused and neighboring tests and verify GREEN**

Run:

```bash
python -m pytest tests/unit/test_search_dev_backend.py tests/unit/test_search_auto_reindex.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run Ruff on the task files**

Run:

```bash
ruff check src/general_manager/search/backends/dev.py tests/unit/test_search_dev_backend.py
ruff format --check src/general_manager/search/backends/dev.py tests/unit/test_search_dev_backend.py
```

Expected: both commands exit zero.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/general_manager/search/backends/dev.py tests/unit/test_search_dev_backend.py
git commit -m "fix: require all dev search query terms"
```

---

### Task 2: Lazily Hydrate DevSearch Per Process and Index

**Files:**
- Modify: `src/general_manager/search/backends/dev.py`
- Modify: `src/general_manager/search/backend_registry.py`
- Modify: `tests/unit/test_search_backend_registry.py`
- Replace: `tests/unit/test_search_auto_reindex.py`
- Modify: `docs/howto/search.md`
- Modify: `docs/concepts/search.md`

**Interfaces:**
- Consumes: `SearchIndexer(backend).reindex_manager_index(manager_class, index_name)`, `iter_index_configs(index_name)`, Django settings, and Task 1 all-term scoring.
- Produces: `DevSearchBackend(*, auto_reindex: bool = False)`, `DevSearchBackend.configure_auto_reindex(enabled: bool) -> None`, private per-index hydration before `search()`, and `_dev_auto_reindex_enabled(django_settings: object) -> bool` in the backend registry.

- [ ] **Step 1: Add failing real-behavior tests for lazy hydration**

Replace the removal-only auto-reindex coverage with tests that exercise a
DevSearch subclass whose hydration source writes a real document into itself:

```python
class RecordingHydrationBackend(DevSearchBackend):
    def __init__(self, *, fail_once: bool = False) -> None:
        super().__init__(auto_reindex=True)
        self.hydrated_indexes: list[str] = []
        self.fail_once = fail_once

    def _reindex_configured_managers(self, index_name: str) -> None:
        self.hydrated_indexes.append(index_name)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("source unavailable")
        self.upsert(index_name, [_search_document(index_name)])
```

Add tests whose consumer-visible assertions establish:

```python
def test_first_search_hydrates_index_once() -> None:
    backend = RecordingHydrationBackend()
    assert backend.search("global", "Dock").total == 1
    assert backend.search("global", "Dock").total == 1
    assert backend.hydrated_indexes == ["global"]


def test_hydration_is_tracked_per_index() -> None:
    backend = RecordingHydrationBackend()
    assert backend.search("global", "Dock").total == 1
    assert backend.search("private", "Dock").total == 1
    assert backend.hydrated_indexes == ["global", "private"]


def test_failed_hydration_propagates_and_retries() -> None:
    backend = RecordingHydrationBackend(fail_once=True)
    with pytest.raises(RuntimeError, match="source unavailable"):
        backend.search("global", "Dock")
    assert backend.search("global", "Dock").total == 1
    assert backend.hydrated_indexes == ["global", "global"]


def test_direct_backend_does_not_hydrate_without_opt_in() -> None:
    backend = DevSearchBackend()
    assert backend.search("global", "").total == 0
```

The production mutations caught are removing first-search hydration, marking a
failed hydration complete, using one global hydration flag, or enabling direct
instances by default.

Also add the integration test now, before production changes: use the existing
lightweight `Project` fixture and manager initialization pattern from
`tests/unit/test_search_indexer.py`, instantiate
`DevSearchBackend(auto_reindex=True)`, and call
`search("global", "Alpha")` without calling `SearchIndexer` first. Assert the
real result contains the expected project identity. Add a second source record
through the existing synchronous manager lifecycle/invalidation path after
hydration and assert a subsequent search finds it without rebuilding the whole
index.

- [ ] **Step 2: Add failing settings tests**

In `tests/unit/test_search_backend_registry.py`, test the setting boundary
through configured backend state rather than restored request signals:

```python
def test_dev_auto_reindex_requires_setting_and_debug() -> None:
    enabled = SimpleNamespace(
        DEBUG=True,
        GENERAL_MANAGER={"SEARCH_BACKEND": DevSearchBackend, "SEARCH_AUTO_REINDEX": True},
    )
    configure_search_backend_from_settings(enabled)
    assert get_search_backend().auto_reindex_enabled is True


def test_dev_auto_reindex_is_disabled_outside_debug() -> None:
    disabled = SimpleNamespace(
        DEBUG=False,
        GENERAL_MANAGER={"SEARCH_BACKEND": DevSearchBackend, "SEARCH_AUTO_REINDEX": True},
    )
    configure_search_backend_from_settings(disabled)
    assert get_search_backend().auto_reindex_enabled is False
```

Also cover a true `SEARCH_AUTO_REINDEX` with a non-Dev protocol backend and
assert its configuration succeeds without DevSearch-specific mutation.

- [ ] **Step 3: Run hydration/configuration tests and verify RED**

Run:

```bash
python -m pytest \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_indexer.py -q
```

Expected: failures identify the missing constructor option, hydration method,
observable enabled state, and settings activation.

- [ ] **Step 4: Implement opt-in per-index hydration in DevSearch**

Add standard-library state in `DevSearchBackend.__init__`:

```python
def __init__(self, *, auto_reindex: bool = False) -> None:
    self._indexes: dict[str, _IndexStore] = {}
    self._auto_reindex = auto_reindex
    self._hydrated_indexes: set[str] = set()
    self._hydrating_indexes: set[str] = set()
    self._hydration_lock = RLock()

@property
def auto_reindex_enabled(self) -> bool:
    return self._auto_reindex

def configure_auto_reindex(self, enabled: bool) -> None:
    self._auto_reindex = enabled
```

At the beginning of `search()`, call `_ensure_hydrated(index_name)`. Implement
the guarded behavior as:

```python
def _ensure_hydrated(self, index_name: str) -> None:
    if not self._auto_reindex or index_name in self._hydrated_indexes:
        return
    with self._hydration_lock:
        if index_name in self._hydrated_indexes:
            return
        if index_name in self._hydrating_indexes:
            return
        self._hydrating_indexes.add(index_name)
        try:
            self._reindex_configured_managers(index_name)
        else:
            self._hydrated_indexes.add(index_name)
        finally:
            self._hydrating_indexes.discard(index_name)

def _reindex_configured_managers(self, index_name: str) -> None:
    from general_manager.search.indexer import SearchIndexer
    from general_manager.search.registry import iter_index_configs

    indexer = SearchIndexer(self)
    for manager_class, _index_config in iter_index_configs(index_name):
        indexer.reindex_manager_index(manager_class, index_name)
```

Use `threading.RLock`. Do not catch or log hydration errors. Update class/search
docstrings to describe opt-in lazy lifecycle, retry semantics, and concurrency
scope.

- [ ] **Step 5: Enable hydration through settings only for DevSearch**

Add a settings helper in `backend_registry.py` that honors nested precedence:

```python
def _dev_auto_reindex_enabled(django_settings: object) -> bool:
    config_candidate = getattr(django_settings, _SETTINGS_KEY, None)
    if isinstance(config_candidate, Mapping) and "SEARCH_AUTO_REINDEX" in config_candidate:
        configured = config_candidate["SEARCH_AUTO_REINDEX"]
    else:
        configured = getattr(django_settings, "SEARCH_AUTO_REINDEX", False)
    return bool(getattr(django_settings, "DEBUG", False)) and bool(configured)
```

After resolving a settings-supplied backend, call
`configure_auto_reindex(...)` only when it is a `DevSearchBackend`. When
`get_search_backend()` creates its fallback instance, pass the helper result to
the constructor. Do not alter external backends.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```bash
python -m pytest \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_dev_backend.py \
  tests/unit/test_search_indexer.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Verify real manager hydration and invalidation**

Run the integration coverage added in Step 1 after the implementation:

```bash
python -m pytest tests/unit/test_search_auto_reindex.py tests/unit/test_search_indexer.py -q
```

Expected: all tests pass and no explicit pre-search reindex appears in the test.

- [ ] **Step 8: Update user-facing lifecycle documentation**

In `docs/howto/search.md`, explain that `SEARCH_AUTO_REINDEX=True` with
`DEBUG=True` lazily fills each DevSearch index on first search in that serving
process. State that `search_index --reindex` in another process cannot fill a
running DevSearch backend.

In `docs/concepts/search.md`, replace the claim that DevSearch is only a manually
filled process-local store with the approved lifecycle: disposable per-process
projection, per-index first-search hydration, retry-on-failure, synchronous
same-process invalidation, and restart required for other-process writes.
Document that every distinct query term is required.

- [ ] **Step 9: Run task verification**

Run:

```bash
ruff check \
  src/general_manager/search/backends/dev.py \
  src/general_manager/search/backend_registry.py \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_dev_backend.py \
  tests/unit/test_search_indexer.py
ruff format --check \
  src/general_manager/search/backends/dev.py \
  src/general_manager/search/backend_registry.py \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_dev_backend.py \
  tests/unit/test_search_indexer.py
mypy src/general_manager/search/backends/dev.py src/general_manager/search/backend_registry.py
python -m pytest \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_dev_backend.py \
  tests/unit/test_search_indexer.py \
  tests/integration/test_graphql_search.py -q
```

Expected: every command exits zero.

- [ ] **Step 10: Commit Task 2**

```bash
git add \
  src/general_manager/search/backends/dev.py \
  src/general_manager/search/backend_registry.py \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_indexer.py \
  docs/howto/search.md \
  docs/concepts/search.md
git commit -m "feat: lazily hydrate development search"
```

---

### Task 3: Whole-Feature Verification and Documentation Consistency

**Files:**
- Modify only if a verification failure identifies a task-scoped defect in files already listed above.

**Interfaces:**
- Consumes: Task 1 all-term matching and Task 2 lazy-hydration lifecycle.
- Produces: A verified branch whose docs, types, tests, and runtime behavior agree with the approved spec.

- [ ] **Step 1: Run the complete search test surface**

```bash
python -m pytest \
  tests/unit/test_search_*.py \
  tests/unit/test_graphql_search.py \
  tests/integration/test_graphql_search.py \
  tests/integration/test_search_m2m_invalidation.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run repository-standard checks on the final diff**

```bash
ruff check src/general_manager/search tests/unit/test_search_*.py
ruff format --check src/general_manager/search tests/unit/test_search_*.py
mypy src/general_manager/search
git diff --check
```

Expected: all commands exit zero.

- [ ] **Step 3: Confirm the example project configuration**

Run:

```bash
PYTHONPATH=src:example_project/outer_rim_logistics \
DJANGO_SETTINGS_MODULE=orl.settings \
python -m django check
```

Expected: Django system checks report no issues. The existing example setting
`SEARCH_AUTO_REINDEX=True` requires no persistent file or management command.

- [ ] **Step 4: Commit verification-only fixes if needed**

If Steps 1-3 required a correction in an already scoped file, commit that
correction and its regression test together:

```bash
git add \
  src/general_manager/search/backends/dev.py \
  src/general_manager/search/backend_registry.py \
  tests/unit/test_search_auto_reindex.py \
  tests/unit/test_search_backend_registry.py \
  tests/unit/test_search_dev_backend.py \
  tests/unit/test_search_indexer.py \
  docs/howto/search.md \
  docs/concepts/search.md
git commit -m "fix: complete dev search lifecycle verification"
```

If no correction was needed, do not create an empty commit.
