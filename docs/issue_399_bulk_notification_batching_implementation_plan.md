# Bulk Data-Change Notification Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, nested notification-batching context that emits one GraphQL refresh per manager class and one RemoteAPI refresh per resource through a single async bridge.

**Architecture:** A focused `notification_batching` module owns `ContextVar` state, target deduplication, flush-on-exit behavior, and the single sync-to-async bridge. Existing GraphQL and RemoteAPI signal receivers retain immediate behavior outside the context but enqueue aggregate refresh targets inside it. GraphQL detail and class subscriptions join dedicated refresh groups without changing ordinary per-row groups.

**Tech Stack:** Python 3.12+, Django signals and transactions, Channels channel layers, asgiref, Graphene subscriptions, pytest/unittest, Ruff, mypy.

---

## File Structure

- Create `src/general_manager/api/notification_batching.py`: public context manager and private generic batching primitives.
- Create `tests/unit/test_notification_batching.py`: context nesting, deduplication, failure, and bridge-count tests.
- Modify `src/general_manager/public_api_registry.py`: lazy public export.
- Modify `src/general_manager/_types/api.py`: type-checker-visible public export.
- Modify `tests/unit/test_graph_ql.py`: stable public import contract.
- Modify `src/general_manager/api/graphql_subscriptions.py`: refresh group naming and one-bridge immediate dispatch helper.
- Modify `src/general_manager/api/graphql.py`: enqueue GraphQL refreshes and subscribe streams to refresh groups.
- Modify `tests/unit/test_graphql_subscriptions.py`: GraphQL dispatch and class refresh tests.
- Modify `tests/unit/test_grapql_subscription_helper.py`: immediate dispatch failure tests.
- Modify `tests/integration/test_graphql_subscriptions.py`: detail and dependency refresh behavior.
- Modify `src/general_manager/api/remote_invalidation.py`: enqueue RemoteAPI refreshes and share payload construction.
- Modify `tests/unit/test_remote_invalidation.py`: RemoteAPI aggregation and payload tests.
- Modify `tests/integration/test_caching.py`: prove cache invalidation remains immediate inside notification batches.
- Modify `docs/concepts/graphql/subscriptions.md`: public API, refresh semantics, timing disclosure, and transaction ordering.
- Modify `docs/examples/remote_manager_interface_end_to_end.md`: RemoteAPI refresh payload and batching usage.

### Task 1: Shared Notification Batch Context

**Files:**
- Create: `tests/unit/test_notification_batching.py`
- Create: `src/general_manager/api/notification_batching.py`
- Modify: `src/general_manager/public_api_registry.py`
- Modify: `src/general_manager/_types/api.py`
- Modify: `tests/unit/test_graph_ql.py`

- [ ] **Step 1: Write failing context and public-export tests**

Create tests that import `bulk_data_change_notifications` publicly and exercise the private registration seam with an async recording sender:

```python
from __future__ import annotations

from unittest.mock import patch

import pytest

from general_manager.api import bulk_data_change_notifications
from general_manager.api.notification_batching import _queue_notification


def test_empty_batch_does_not_create_async_bridge() -> None:
    with patch("general_manager.api.notification_batching.async_to_sync") as bridge:
        with bulk_data_change_notifications():
            pass
    bridge.assert_not_called()


def test_nested_batch_deduplicates_targets_and_flushes_once() -> None:
    sent: list[tuple[str, dict[str, object]]] = []

    async def group_send(group: str, message: dict[str, object]) -> None:
        sent.append((group, message))

    with bulk_data_change_notifications():
        assert _queue_notification(
            key=("graphql", "Project"),
            group_send=group_send,
            group="project-refresh",
            message={"type": "gm.subscription.event", "action": "refresh"},
        )
        with bulk_data_change_notifications():
            assert _queue_notification(
                key=("graphql", "Project"),
                group_send=group_send,
                group="project-refresh",
                message={"type": "gm.subscription.event", "action": "refresh"},
            )

    assert sent == [
        (
            "project-refresh",
            {"type": "gm.subscription.event", "action": "refresh"},
        )
    ]


def test_batch_flushes_when_body_raises() -> None:
    sent: list[str] = []

    async def group_send(group: str, _message: dict[str, object]) -> None:
        sent.append(group)

    with pytest.raises(ValueError, match="row failed"):
        with bulk_data_change_notifications():
            _queue_notification(
                key=("remote", "projects"),
                group_send=group_send,
                group="projects-refresh",
                message={"action": "refresh"},
            )
            raise ValueError("row failed")

    assert sent == ["projects-refresh"]


def test_body_and_memory_failure_are_preserved() -> None:
    async def exhausted(_group: str, _message: dict[str, object]) -> None:
        raise MemoryError("thread startup")

    with pytest.raises(ExceptionGroup) as caught:
        with bulk_data_change_notifications():
            _queue_notification(
                key=("graphql", "Project"),
                group_send=exhausted,
                group="project-refresh",
                message={"action": "refresh"},
            )
            raise ValueError("row failed")

    assert [type(exc) for exc in caught.value.exceptions] == [ValueError, MemoryError]
```

Extend `tests/unit/test_graph_ql.py` to assert the runtime and type-only API exports resolve to the new context manager.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest -q tests/unit/test_notification_batching.py tests/unit/test_graph_ql.py::GraphQLTests::test_public_bulk_data_change_notifications_is_importable
```

Expected: collection fails because `bulk_data_change_notifications` and `notification_batching` do not exist.

- [ ] **Step 3: Implement the minimal batching module and exports**

Implement a private pending-target record, a `ContextVar`, sequential async flush, registration helper, and context manager with this interface:

```python
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from asgiref.sync import async_to_sync

from general_manager.logging import get_logger

type NotificationKey = tuple[str, ...]
type NotificationMessage = Mapping[str, object]
type GroupSend = Callable[[str, NotificationMessage], Awaitable[None]]

logger = get_logger("api.notification_batching")


@dataclass(slots=True)
class _PendingNotification:
    key: NotificationKey
    group_send: GroupSend = field(repr=False)
    group: str
    message: dict[str, object]


@dataclass(slots=True)
class _BatchState:
    targets: dict[NotificationKey, _PendingNotification] = field(default_factory=dict)


_active_batch: ContextVar[_BatchState | None] = ContextVar(
    "general_manager_notification_batch",
    default=None,
)


def _queue_notification(
    *,
    key: NotificationKey,
    group_send: GroupSend,
    group: str,
    message: NotificationMessage,
) -> bool:
    state = _active_batch.get()
    if state is None:
        return False
    state.targets.setdefault(
        key,
        _PendingNotification(key, group_send, group, dict(message)),
    )
    return True


async def _flush_notifications(state: _BatchState) -> None:
    for key in sorted(state.targets):
        target = state.targets[key]
        try:
            await target.group_send(target.group, target.message)
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "failed to dispatch batched notification",
                context={"key": key, "group": target.group},
                exc_info=exc,
            )


def _flush_sync(state: _BatchState) -> None:
    if state.targets:
        async_to_sync(_flush_notifications)(state)


@contextmanager
def bulk_data_change_notifications() -> Iterator[None]:
    current = _active_batch.get()
    if current is not None:
        yield
        return

    state = _BatchState()
    token = _active_batch.set(state)
    try:
        yield
    except BaseException as body_error:
        _active_batch.reset(token)
        try:
            _flush_sync(state)
        except BaseException as flush_error:
            raise BaseExceptionGroup(
                "bulk data change and notification flush failed",
                [body_error, flush_error],
            ) from None
        raise
    else:
        _active_batch.reset(token)
        _flush_sync(state)
```

Add `bulk_data_change_notifications` to `API_EXPORTS`, `general_manager._types.api.__all__`, and its type-only imports. Do not add it to the top-level `general_manager` exports because the approved public path is `general_manager.api`.

- [ ] **Step 4: Run focused tests and formatting**

Run:

```bash
python -m pytest -q tests/unit/test_notification_batching.py tests/unit/test_graph_ql.py
ruff format src/general_manager/api/notification_batching.py tests/unit/test_notification_batching.py src/general_manager/_types/api.py src/general_manager/public_api_registry.py tests/unit/test_graph_ql.py
ruff check src/general_manager/api/notification_batching.py tests/unit/test_notification_batching.py src/general_manager/_types/api.py src/general_manager/public_api_registry.py tests/unit/test_graph_ql.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 5: Verify and commit only tracked task files**

Run `git check-ignore` on every task file, stage only the five listed files, inspect `git diff --cached --name-only`, then commit:

```bash
git commit -m "feat: add bulk notification batch context"
```

### Task 2: GraphQL Dispatch Batching and One-Bridge Immediate Sends

**Files:**
- Modify: `src/general_manager/api/graphql_subscriptions.py`
- Modify: `src/general_manager/api/graphql.py`
- Modify: `tests/unit/test_graphql_subscriptions.py`
- Modify: `tests/unit/test_grapql_subscription_helper.py`

- [ ] **Step 1: Write failing dispatch tests**

Update the existing `_handle_data_change` tests to use an async `group_send` and assert:

```python
def test_handle_data_change_uses_one_bridge_for_both_groups(self) -> None:
    sent: list[str] = []

    async def group_send(group: str, _message: dict[str, object]) -> None:
        sent.append(group)

    layer = SimpleNamespace(group_send=group_send)
    with (
        patch.object(GraphQL, "_get_channel_layer", return_value=layer),
        patch(
            "general_manager.api.graphql.async_to_sync",
            side_effect=lambda fn: lambda *args: asyncio.run(fn(*args)),
        ) as bridge,
    ):
        GraphQL._handle_data_change(
            sender=RegisteredManager,
            instance=RegisteredManager(),
            action="update",
        )

    bridge.assert_called_once()
    assert set(sent) == {
        GraphQL._group_name(RegisteredManager, {"id": 1}),
        GraphQL._class_group_name(RegisteredManager),
    }
```

Add a batching test that performs many `_handle_data_change` calls inside `bulk_data_change_notifications()` and asserts no immediate bridge plus one refresh-group send at exit. Add edge-case tests asserting `MemoryError` propagates without attempting the second group and that ordinary `RuntimeError` logs and continues.

- [ ] **Step 2: Run the focused tests to verify failure**

Run:

```bash
python -m pytest -q tests/unit/test_graphql_subscriptions.py::GraphQLHandleDataChangeTests tests/unit/test_grapql_subscription_helper.py::GraphQLHandleDataChangeEdgeCasesTests
```

Expected: failures show two immediate bridge calls remain and no refresh target is queued.

- [ ] **Step 3: Add refresh naming and async dispatch helpers**

In `graphql_subscriptions.py`, add:

```python
def refresh_group_name(manager_class: type[GeneralManager]) -> str:
    return f"gm_subscriptions.{manager_class.__name__}.__refresh__"


async def dispatch_subscription_event(
    channel_layer: BaseChannelLayer,
    group_names: Iterable[str],
    message: SubscriptionMessage,
) -> int:
    dispatched = 0
    for target_group_name in group_names:
        try:
            await channel_layer.group_send(target_group_name, message)
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "failed to dispatch subscription event",
                context={"group": target_group_name, "message": message},
                exc_info=exc,
            )
        else:
            dispatched += 1
    return dispatched
```

Import these helpers into `graphql.py`, add a compatibility wrapper `_refresh_group_name`, and change `_handle_data_change` to:

1. Build the existing row-level message.
2. Queue a refresh message keyed by `("graphql", refresh_group_name)` when a batch is active.
3. Otherwise call `async_to_sync(dispatch_subscription_event)` once with both existing groups.
4. Emit the success debug log only when the returned dispatch count is non-zero.

- [ ] **Step 4: Run focused GraphQL dispatch tests**

Run:

```bash
python -m pytest -q tests/unit/test_graphql_subscriptions.py::GraphQLHandleDataChangeTests tests/unit/test_grapql_subscription_helper.py::GraphQLHandleDataChangeEdgeCasesTests
ruff format src/general_manager/api/graphql_subscriptions.py src/general_manager/api/graphql.py tests/unit/test_graphql_subscriptions.py tests/unit/test_grapql_subscription_helper.py
ruff check src/general_manager/api/graphql_subscriptions.py src/general_manager/api/graphql.py tests/unit/test_graphql_subscriptions.py tests/unit/test_grapql_subscription_helper.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 5: Verify and commit only tracked task files**

Check ignored status, stage only the four task files, inspect the staged names, and commit:

```bash
git commit -m "fix: batch GraphQL subscription dispatch"
```

### Task 3: GraphQL Refresh Subscription Semantics

**Files:**
- Modify: `src/general_manager/api/graphql.py`
- Modify: `tests/unit/test_graphql_subscriptions.py`
- Modify: `tests/integration/test_graphql_subscriptions.py`

- [ ] **Step 1: Write failing detail, dependency, and class refresh tests**

Add integration tests with actual Channels subscriptions:

```python
def test_detail_subscription_rehydrates_once_after_bulk_refresh(self) -> None:
    employee = self.Employee.create(name="Before", creator_id=self.user.id)
    schema = self._build_schema()

    async def run_subscription() -> tuple[object, object]:
        generator = await schema.subscribe(
            DETAIL_SUBSCRIPTION,
            variable_values={"id": employee.id},
            context_value=SimpleNamespace(user=self.user),
        )
        try:
            snapshot = await generator.__anext__()

            def mutate() -> None:
                with bulk_data_change_notifications():
                    employee.update(name="Middle", creator_id=self.user.id)
                    employee.update(name="After", creator_id=self.user.id)

            await asyncio.to_thread(mutate)
            return snapshot, await generator.__anext__()
        finally:
            await generator.aclose()

    snapshot, refresh = asyncio.run(run_subscription())
    assert snapshot.data["onEmployeeChange"]["action"] == "snapshot"
    assert refresh.data["onEmployeeChange"] == {
        "action": "refresh",
        "item": {"id": str(employee.id), "name": "After"},
    }
```

Add a calculation/dependency test that batches two changes to a dependency manager and asserts one `refresh` on the calculated manager subscription. Add a class-wide test that batches multiple creates and asserts exactly one `{action: "refresh", item: None}` event.

- [ ] **Step 2: Run tests to verify refresh delivery fails**

Run:

```bash
python -m pytest -q tests/unit/test_graphql_subscriptions.py -k "class_subscription and refresh" tests/integration/test_graphql_subscriptions.py -k "bulk_refresh"
```

Expected: subscriptions do not receive refresh messages because they have not joined refresh groups and class streams reject messages without identification.

- [ ] **Step 3: Join refresh groups and handle class refresh messages**

For detail subscriptions, initialize group membership with both the instance group and the manager refresh group. For every resolved dependency, add both its instance group and its manager-class refresh group. The existing action queue can carry `"refresh"`; the event stream rehydrates its own identification as it does for ordinary actions.

For class-wide subscriptions, join both the existing class group and the refresh group. In the event loop, handle the aggregate message before identification validation:

```python
if action == "refresh":
    clear_capability_context(info)
    yield SubscriptionEvent(item=None, action="refresh")
    continue
```

Discard every joined group during cleanup.

- [ ] **Step 4: Run focused subscription tests**

Run:

```bash
python -m pytest -q tests/unit/test_graphql_subscriptions.py tests/integration/test_graphql_subscriptions.py
ruff format src/general_manager/api/graphql.py tests/unit/test_graphql_subscriptions.py tests/integration/test_graphql_subscriptions.py
ruff check src/general_manager/api/graphql.py tests/unit/test_graphql_subscriptions.py tests/integration/test_graphql_subscriptions.py
```

Expected: all GraphQL subscription tests pass, including ordinary row-level permission filtering.

- [ ] **Step 5: Verify and commit only tracked task files**

Check ignored status, stage only the three task files, inspect staged names, and commit:

```bash
git commit -m "feat: deliver GraphQL batch refresh events"
```

### Task 4: RemoteAPI Refresh Batching

**Files:**
- Modify: `src/general_manager/api/remote_invalidation.py`
- Modify: `tests/unit/test_remote_invalidation.py`

- [ ] **Step 1: Write failing RemoteAPI batching tests**

Add tests that use an async recording channel layer and assert:

```python
def test_remote_invalidation_batches_one_refresh_per_resource(self) -> None:
    sent: list[tuple[str, dict[str, object]]] = []

    async def group_send(group: str, payload: dict[str, object]) -> None:
        sent.append((group, payload))

    layer = SimpleNamespace(group_send=group_send)
    with (
        patch(
            "general_manager.api.remote_invalidation._get_channel_layer_safe",
            return_value=layer,
        ),
        bulk_data_change_notifications(),
    ):
        emit_remote_invalidation(Project, instance=first, action="create")
        emit_remote_invalidation(Project, instance=second, action="create")

    assert len(sent) == 1
    group, payload = sent[0]
    assert group == remote_invalidation_group_name(get_remote_api_config(Project))
    assert payload["action"] == "refresh"
    assert payload["identification"] is None
    assert isinstance(payload["event_id"], str)
```

Add a cross-subsystem test that queues both a GraphQL refresh and a RemoteAPI refresh inside one context and patches `notification_batching.async_to_sync` to assert exactly one bridge invocation.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest -q tests/unit/test_remote_invalidation.py -k "batch or refresh"
```

Expected: two row-level invalidations are sent because RemoteAPI does not yet register batched targets.

- [ ] **Step 3: Share payload construction and enqueue refresh targets**

Extract the payload mapping into a helper accepting `config`, `action`, and optional identification. In `emit_remote_invalidation`:

```python
refresh_payload = _remote_invalidation_payload(
    config,
    action="refresh",
    identification=None,
)
group = remote_invalidation_group_name(config)
if _queue_notification(
    key=("remote", group, config.protocol_version),
    group_send=channel_layer.group_send,
    group=group,
    message=refresh_payload,
):
    return
```

When no batch is active, construct and send the existing row-level payload exactly as before.

- [ ] **Step 4: Run focused RemoteAPI and batching tests**

Run:

```bash
python -m pytest -q tests/unit/test_remote_invalidation.py tests/unit/test_notification_batching.py
ruff format src/general_manager/api/remote_invalidation.py tests/unit/test_remote_invalidation.py
ruff check src/general_manager/api/remote_invalidation.py tests/unit/test_remote_invalidation.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 5: Verify and commit only tracked task files**

Check ignored status, stage only the two task files, inspect staged names, and commit:

```bash
git commit -m "feat: batch RemoteAPI refresh invalidation"
```

### Task 5: Cache Safety, Transaction Contract, and Documentation

**Files:**
- Modify: `tests/integration/test_caching.py`
- Modify: `docs/concepts/graphql/subscriptions.md`
- Modify: `docs/examples/remote_manager_interface_end_to_end.md`

- [ ] **Step 1: Write the cache-safety regression test**

Extend `CachingTestCase` with a test that primes `budget_left`, updates its dependency inside the notification context, and reads it before context exit:

```python
def test_notification_batch_keeps_dependency_invalidation_immediate(self) -> None:
    commercials = self.TestCommercials(project=self.project1)
    assert commercials.budget_left == Measurement(800, "EUR")

    with bulk_data_change_notifications():
        self.project1 = self.project1.update(
            actual_costs=Measurement(600, "EUR"),
            ignore_permission=True,
        )
        refreshed = self.TestCommercials(project=self.project1)
        assert refreshed.budget_left == Measurement(400, "EUR")
```

- [ ] **Step 2: Run the cache regression test**

Run:

```bash
python -m pytest -q tests/integration/test_caching.py::CachingTestCase::test_notification_batch_keeps_dependency_invalidation_immediate
```

Expected: pass without cache implementation changes, proving notification batching does not intercept cache invalidation.

- [ ] **Step 3: Update user-facing documentation**

Document this canonical transaction ordering in both relevant guides:

```python
from django.db import transaction
from general_manager.api import bulk_data_change_notifications

with bulk_data_change_notifications():
    with transaction.atomic():
        for row in rows:
            ExampleManager.create(**row)
```

State that GraphQL detail subscriptions rehydrate on `refresh`, class-wide subscriptions receive `item: null`, RemoteAPI refresh invalidations carry `identification: null`, class-wide refreshes intentionally disclose class-level change timing, exceptional exits flush, and cache invalidation remains immediate.

- [ ] **Step 4: Run documentation-adjacent tests and lint**

Run:

```bash
python -m pytest -q tests/integration/test_caching.py::CachingTestCase::test_notification_batch_keeps_dependency_invalidation_immediate tests/unit/test_graph_ql.py tests/unit/test_remote_invalidation.py
ruff check tests/integration/test_caching.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 5: Verify and commit only tracked task files**

Check ignored status, stage only the three task files, inspect staged names, and commit:

```bash
git commit -m "docs: explain bulk notification refreshes"
```

### Task 6: Full Verification and Final Review

**Files:**
- Verify all modified implementation, test, and documentation files.

- [ ] **Step 1: Run the complete focused feature suite**

Run:

```bash
python -m pytest -q tests/unit/test_notification_batching.py tests/unit/test_graphql_subscriptions.py tests/unit/test_grapql_subscription_helper.py tests/integration/test_graphql_subscriptions.py tests/unit/test_remote_invalidation.py tests/integration/test_caching.py
```

Expected: all tests pass.

- [ ] **Step 2: Run repository formatting, lint, and type checks**

Run:

```bash
ruff format --check src tests
ruff check src tests
mypy src
```

Expected: all commands exit successfully with no diagnostics.

- [ ] **Step 3: Run the full test suite**

Run:

```bash
python -m pytest
```

Expected: the full suite passes.

- [ ] **Step 4: Inspect tracked, ignored, and committed scope**

Run:

```bash
git status --short --ignored
git diff --check
git diff origin/main...HEAD --name-only
```

Expected: ignored runtime artifacts may appear only with `!!`; no ignored file is staged or committed, no unexpected tracked file is modified, and the diff is limited to issue #399.

- [ ] **Step 5: Perform two-stage code review**

Dispatch one subagent to check spec compliance and a second fresh subagent to review code quality, correctness, concurrency, error handling, public API consistency, and test gaps. Address findings test-first, rerun the narrowest relevant checks, and commit any required fixes with a conventional commit message.
