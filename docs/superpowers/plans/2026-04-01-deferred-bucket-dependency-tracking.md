# Deferred Bucket Dependency Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record effective non-request bucket dependencies when a bucket is terminally evaluated, instead of eagerly when intermediate bucket objects are constructed.

**Architecture:** Add a shared deferred-tracking mechanism for ORM-backed bucket evaluation and route terminal operations through it. Preserve request-bucket eager `request_query` tracking, and cover calculation/group behavior through the underlying bucket paths they exercise.

**Tech Stack:** Python 3.12+, Django, pytest, Ruff, mypy

---

### Task 1: Add failing integration tests for deferred bucket tracking risks

**Files:**
- Modify: `tests/integration/test_caching.py`
- Test: `tests/integration/test_caching.py`

- [ ] **Step 1: Write the failing tests**

Add integration tests covering:
- deferred tracking on `count()` for a chained `all().filter(...).exclude(...)` query
- empty-result invalidation for a cached zero-count query
- non-`__iter__` terminal evaluation such as `first()` or `__contains__`
- calculation/group interaction that depends on filtered buckets without iterating them directly

- [ ] **Step 2: Run each new test to verify it fails**

Run:
```bash
PYTHONPATH=/Users/tim/.codex/worktrees/450c/general_manager/src python -m pytest tests/integration/test_caching.py -k "deferred_tracking or empty_result or terminal_operation or grouped_bucket" -q
```

Expected: FAIL with stale-cache behavior or missing deferred dependency recording.

### Task 2: Implement shared deferred tracking for ORM-backed bucket evaluation

**Files:**
- Modify: `src/general_manager/bucket/database_bucket.py`
- Modify: `src/general_manager/manager/general_manager.py`
- Modify: `src/general_manager/cache/dependency_index.py`

- [ ] **Step 1: Add minimal production support for deferred bucket dependency recording**

Implement a single helper on `DatabaseBucket` that records the bucket's effective filter/exclude state before terminal reads.

- [ ] **Step 2: Route terminal operations through the helper**

Cover:
- `__iter__`
- `count()`
- `first()`
- `last()`
- `get()`
- `__len__`
- `__getitem__` for int/slice reads
- `__contains__`

- [ ] **Step 3: Keep request buckets unchanged**

Do not change request-bucket eager `request_query` tracking semantics.

- [ ] **Step 4: Re-run the focused integration tests**

Run:
```bash
PYTHONPATH=/Users/tim/.codex/worktrees/450c/general_manager/src python -m pytest tests/integration/test_caching.py -k "deferred_tracking or empty_result or terminal_operation or grouped_bucket" -q
```

Expected: PASS

### Task 3: Verify calculation/group behavior and tighten edge cases

**Files:**
- Modify: `src/general_manager/bucket/calculation_bucket.py`
- Modify: `src/general_manager/bucket/group_bucket.py`
- Modify: `tests/integration/test_caching.py`

- [ ] **Step 1: Add only the minimal code needed if focused tests still fail**

If calculation/group paths bypass the deferred ORM hook, add the smallest bridging changes needed to make their terminal operations record dependencies correctly.

- [ ] **Step 2: Re-run the focused integration slice**

Run:
```bash
PYTHONPATH=/Users/tim/.codex/worktrees/450c/general_manager/src python -m pytest tests/integration/test_caching.py -k "deferred_tracking or empty_result or terminal_operation or grouped_bucket" -q
```

Expected: PASS

### Task 4: Broad verification

**Files:**
- Modify: `tasks/todo.md`
- Modify: `tasks/lessons.md` (only if a new reusable lesson is discovered)

- [ ] **Step 1: Run the full integration suite**

Run:
```bash
PYTHONPATH=/Users/tim/.codex/worktrees/450c/general_manager/src python -m pytest tests/integration -q
```

Expected: PASS

- [ ] **Step 2: Run targeted quality checks**

Run:
```bash
ruff check src/general_manager/bucket src/general_manager/cache src/general_manager/manager tests/integration/test_caching.py
ruff format --check src/general_manager/bucket src/general_manager/cache src/general_manager/manager tests/integration/test_caching.py
mypy src/general_manager/bucket src/general_manager/cache src/general_manager/manager
```

Expected: PASS

- [ ] **Step 3: Update task records**

Record files changed, commands run, verification results, subagents used, and any new reusable lesson if one emerged.
