"""Tests for context-local ORM data-change transaction ownership."""

from contextvars import copy_context
import inspect

from general_manager.cache import data_change_context


def _own(database_alias: str, *, caller_in_atomic_block: bool):
    return data_change_context.own_data_change_transaction(
        database_alias,
        caller_in_atomic_block=caller_in_atomic_block,
    )


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
