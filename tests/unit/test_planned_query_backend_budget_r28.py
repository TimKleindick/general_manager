"""Planned query budgets do not require unrelated database I/O."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from general_manager.chat import tools
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
    _lookup,
    _public_runner,
)

category_schema = _category_fixture


@pytest.mark.parametrize("vendor", ["postgresql", "mysql", "sqlite"])
def test_planned_generic_query_needs_no_default_database(
    category_schema, monkeypatch, vendor
):
    def forbidden(*_args, **_kwargs):
        message = "A non-SQL query must not open the default database"
        raise AssertionError(message)

    @contextmanager
    def execute_wrapper(_wrapper):
        # Installing a deferred SQL hook is not a database read.
        yield

    monkeypatch.setattr(
        tools,
        "connection",
        SimpleNamespace(
            vendor=vendor, cursor=forbidden, execute_wrapper=execute_wrapper
        ),
    )
    monkeypatch.setattr(tools.transaction, "atomic", forbidden)
    runner = _public_runner(category_schema.manager)
    result, valid = _lookup(runner, category_schema)
    assert valid, result
    assert result["complete"] is True
    assert [row["id"] for row in result["data"]] == [1, 2, 3]


@pytest.mark.parametrize(
    ("configured_ms", "expected_ms"), [(None, 100000), (2000, 2000), (200000, 100000)]
)
@pytest.mark.parametrize("failure", [None, "budget", "population"])
def test_planned_sql_queries_keep_one_scoped_statement_budget(
    category_schema, monkeypatch, configured_ms, expected_ms, failure
):
    events = []
    wrappers = []

    class Cursor:
        def execute(self, sql, params):
            def execute(sql, params, many, context):
                events.append((sql, params))
                if (failure == "budget" and sql.startswith("SET LOCAL")) or (
                    failure == "population" and sql == "SELECT population"
                ):
                    message = "Simulated SQL failure"
                    raise RuntimeError(message)

            actual = execute
            for wrapper in reversed(wrappers):
                previous = actual

                def actual(sql, params, many, context, wrapper=wrapper, inner=previous):
                    return wrapper(inner, sql, params, many, context)

            return actual(sql, params, False, {})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    @contextmanager
    def execute_wrapper(wrapper):
        wrappers.append(wrapper)
        try:
            yield
        finally:
            assert wrappers.pop() is wrapper

    @contextmanager
    def atomic():
        events.append("atomic_enter")
        try:
            yield
        finally:
            events.append("atomic_exit")

    cursor = Cursor()
    monkeypatch.setattr(
        tools,
        "connection",
        SimpleNamespace(
            vendor="postgresql", cursor=lambda: cursor, execute_wrapper=execute_wrapper
        ),
    )
    monkeypatch.setattr(tools.transaction, "atomic", atomic)
    monkeypatch.setattr(tools, "get_query_timeout_ms", lambda: configured_ms)
    schema = tools.GraphQL._schema
    original = schema.execute

    def execute_with_sql(*args, **kwargs):
        cursor.execute("SELECT population", [])
        cursor.execute("SELECT total_count", [])
        return original(*args, **kwargs)

    # Keep the attested filter executor unchanged; the SQL hook surrounds the
    # actual schema execution, regardless of which Interface emits statements.
    monkeypatch.setattr(schema, "execute", execute_with_sql)
    runner = _public_runner(category_schema.manager)
    result, valid = _lookup(runner, category_schema)
    if failure:
        assert not valid
        assert result == {"status": "error", "code": "tool_failed"}
        assert events[0] == "atomic_enter"
        assert events[1] == ("SET LOCAL statement_timeout = %s", [expected_ms])
        assert events[-1] == "atomic_exit"
        assert not wrappers
        return
    assert valid, result
    assert result["complete"] is True
    assert events == [
        "atomic_enter",
        ("SET LOCAL statement_timeout = %s", [expected_ms]),
        ("SELECT population", []),
        ("SELECT total_count", []),
        "atomic_exit",
    ]
    assert not wrappers
