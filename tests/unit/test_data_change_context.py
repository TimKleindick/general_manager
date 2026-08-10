"""Tests for context-local ORM data-change transaction ownership."""

from contextlib import AbstractContextManager
from contextvars import copy_context
from dataclasses import FrozenInstanceError
import inspect

import pytest

from general_manager.cache import data_change_context


def _own(
    database_alias: str, *, caller_in_atomic_block: bool
) -> AbstractContextManager[data_change_context.DataChangeTransactionScope]:
    return data_change_context.own_data_change_transaction(
        database_alias,
        caller_in_atomic_block=caller_in_atomic_block,
    )


def test_nested_same_alias_reuses_outer_transaction_context() -> None:
    """Nested scopes for an alias share their transaction-owned state."""
    with _own("default", caller_in_atomic_block=False) as outer:
        with _own("default", caller_in_atomic_block=False) as inner:
            assert inner.transaction is outer.transaction
            assert inner.is_outermost is False


def test_changed_classes_are_deduplicated_and_alias_scoped() -> None:
    """Only a framework-owned transaction records its own alias's classes."""
    with _own("default", caller_in_atomic_block=False) as current:
        assert data_change_context.register_data_change_class("Project", "default")
        assert data_change_context.register_data_change_class("Project", "default")
        assert not data_change_context.register_data_change_class("Part", "secondary")
        assert current.transaction.changed_classes == {"Project"}


def test_changed_classes_register_against_each_matching_live_alias() -> None:
    """Cross-alias nesting records classes in each matching live context."""
    with _own("default", caller_in_atomic_block=False) as default:
        with _own("secondary", caller_in_atomic_block=False) as secondary:
            assert data_change_context.register_data_change_class("Project", "default")
            assert data_change_context.register_data_change_class("Part", "secondary")
            assert not data_change_context.register_data_change_class("Task", "missing")
            assert default.transaction.changed_classes == {"Project"}
            assert secondary.transaction.changed_classes == {"Part"}


def test_caller_owned_transaction_is_exposed() -> None:
    """The public context distinguishes caller-owned atomic transactions."""
    with _own("default", caller_in_atomic_block=True) as current:
        assert current.transaction.caller_in_atomic_block is True


def test_transaction_identity_is_immutable_while_state_is_mutable() -> None:
    """Identity cannot change without preventing transaction-local state updates."""
    with _own("default", caller_in_atomic_block=False) as current:
        transaction = current.transaction

        with pytest.raises(FrozenInstanceError):
            transaction.database_alias = "secondary"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            transaction.caller_in_atomic_block = True  # type: ignore[misc]

        transaction.changed_classes.add("Project")
        transaction.metadata["source"] = "test"
        transaction.phase_seconds["database"] = 0.1

        assert transaction.changed_classes == {"Project"}
        assert transaction.metadata == {"source": "test"}
        assert transaction.phase_seconds == {"database": 0.1}


def test_context_ownership_does_not_inspect_django_atomic_internals() -> None:
    """Core ownership must remain independent of Django's private stack."""
    source = inspect.getsource(data_change_context)

    assert "atomic_blocks" not in source
    assert "_from_testcase" not in source


def test_operation_authorization_is_limited_to_the_decorated_body() -> None:
    """Envelope ownership alone does not authorize upload-envelope reuse."""
    with _own("default", caller_in_atomic_block=False):
        assert data_change_context.owns_data_change_transaction("default")
        assert not data_change_context.is_data_change_operation_authorized("default")
        assert not data_change_context.may_reuse_data_change_transaction("default")

        with data_change_context.authorize_data_change_operation("default"):
            assert data_change_context.is_data_change_operation_authorized("default")
            assert data_change_context.may_reuse_data_change_transaction("default")

        assert not data_change_context.is_data_change_operation_authorized("default")
        assert not data_change_context.may_reuse_data_change_transaction("default")


def test_outer_transaction_prevents_conservative_reuse_authorization() -> None:
    """Public caller state makes arbitrary outer transactions fail closed."""
    with _own("default", caller_in_atomic_block=True):
        with data_change_context.authorize_data_change_operation("default"):
            assert data_change_context.owns_data_change_transaction("default")
            assert data_change_context.is_data_change_operation_authorized("default")
            assert not data_change_context.may_reuse_data_change_transaction("default")


def test_transaction_ownership_is_alias_scoped_and_nesting_safe() -> None:
    """A nested alias neither replaces nor authorizes its outer owner."""
    with _own("default", caller_in_atomic_block=False):
        with data_change_context.authorize_data_change_operation("default"):
            with _own("secondary", caller_in_atomic_block=False):
                assert data_change_context.owns_data_change_transaction("default")
                assert data_change_context.owns_data_change_transaction("secondary")
                assert data_change_context.may_reuse_data_change_transaction("default")
                assert not data_change_context.may_reuse_data_change_transaction(
                    "secondary"
                )

                with data_change_context.authorize_data_change_operation("secondary"):
                    assert data_change_context.may_reuse_data_change_transaction(
                        "default"
                    )
                    assert data_change_context.may_reuse_data_change_transaction(
                        "secondary"
                    )

    assert not data_change_context.owns_data_change_transaction("default")
    assert not data_change_context.owns_data_change_transaction("secondary")


def test_copied_context_cannot_retain_stale_transaction_ownership() -> None:
    """A copied ContextVar state cannot outlive its live owner marker."""
    with _own("default", caller_in_atomic_block=False):
        with data_change_context.authorize_data_change_operation("default"):
            stale_context = copy_context()

    assert not stale_context.run(
        data_change_context.owns_data_change_transaction, "default"
    )
    assert not stale_context.run(
        data_change_context.is_data_change_operation_authorized, "default"
    )
    assert not stale_context.run(
        data_change_context.may_reuse_data_change_transaction, "default"
    )
