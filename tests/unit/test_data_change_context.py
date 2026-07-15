"""Tests for ORM data-change transaction ownership metadata."""

from contextvars import copy_context

import pytest
from django.db import DEFAULT_DB_ALIAS, connection, transaction

from general_manager.cache.data_change_context import (
    exclusively_owns_data_change_transaction,
    own_data_change_transaction,
    owns_data_change_transaction,
)


@pytest.mark.django_db(transaction=True)
def test_nested_owned_transaction_remains_exclusive() -> None:
    """Nested envelopes record each same-alias atomic block as owned."""
    with transaction.atomic():
        with own_data_change_transaction(DEFAULT_DB_ALIAS):
            with transaction.atomic():
                with own_data_change_transaction(DEFAULT_DB_ALIAS):
                    assert owns_data_change_transaction(DEFAULT_DB_ALIAS)
                    assert exclusively_owns_data_change_transaction(DEFAULT_DB_ALIAS)


@pytest.mark.django_db(transaction=True)
def test_unowned_outer_transaction_prevents_exclusive_ownership() -> None:
    """An arbitrary transaction beneath an envelope remains detectable."""
    with transaction.atomic():
        with transaction.atomic():
            with own_data_change_transaction(DEFAULT_DB_ALIAS):
                assert owns_data_change_transaction(DEFAULT_DB_ALIAS)
                assert not exclusively_owns_data_change_transaction(DEFAULT_DB_ALIAS)


@pytest.mark.django_db(transaction=True)
def test_interposed_transaction_prevents_exclusive_ownership() -> None:
    """An arbitrary transaction opened above an envelope remains detectable."""
    with transaction.atomic():
        with own_data_change_transaction(DEFAULT_DB_ALIAS):
            with transaction.atomic():
                assert not exclusively_owns_data_change_transaction(DEFAULT_DB_ALIAS)


@pytest.mark.django_db
def test_testcase_atomic_blocks_are_not_treated_as_application_transactions() -> None:
    """Django TestCase wrappers do not defeat exclusive envelope ownership."""
    assert any(
        getattr(block, "_from_testcase", False) for block in connection.atomic_blocks
    )

    with transaction.atomic():
        with own_data_change_transaction(DEFAULT_DB_ALIAS):
            assert exclusively_owns_data_change_transaction(DEFAULT_DB_ALIAS)


@pytest.mark.django_db(transaction=True)
def test_transaction_ownership_is_alias_scoped_and_resets() -> None:
    """Ownership state for one alias neither leaks nor changes another alias."""
    with transaction.atomic():
        with own_data_change_transaction(DEFAULT_DB_ALIAS):
            assert owns_data_change_transaction(DEFAULT_DB_ALIAS)
            assert not owns_data_change_transaction("secondary")

    assert not owns_data_change_transaction(DEFAULT_DB_ALIAS)


@pytest.mark.django_db(transaction=True)
def test_copied_context_cannot_retain_stale_transaction_ownership() -> None:
    """A copied ContextVar state cannot outlive its recorded atomic block."""
    with transaction.atomic():
        with own_data_change_transaction(DEFAULT_DB_ALIAS):
            stale_context = copy_context()

    assert not stale_context.run(
        owns_data_change_transaction,
        DEFAULT_DB_ALIAS,
    )
    assert not stale_context.run(
        exclusively_owns_data_change_transaction,
        DEFAULT_DB_ALIAS,
    )
