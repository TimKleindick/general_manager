from contextlib import contextmanager, nullcontext
import pytest
from django.db import connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from general_manager.chat import tools
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
)

category_schema = _category_fixture


@pytest.mark.parametrize("outer_ms,inner_ms", [(2000, 50), (50, 2000)])
@pytest.mark.parametrize("outer_started", [False, True])
def test_nested_public_queries_preserve_the_shortest_active_budget(
    category_schema, monkeypatch, outer_ms, inner_ms, outer_started
):
    events = []

    class Connection:
        vendor = "postgresql"
        execute_wrapper = BaseDatabaseWrapper.execute_wrapper

        def __init__(self):
            self.execute_wrappers = []
            self.wrap_database_errors = nullcontext()
            self.atomic_depth = 0
            self.timeout = None

        def validate_no_broken_transaction(self):
            pass

        def cursor(self):
            return CursorWrapper(RawCursor(), self)

    db = Connection()

    class RawCursor:
        def execute(self, sql, params=None):
            if sql.startswith("SET LOCAL"):
                db.timeout = params[0]
            events.append(
                {
                    "sql": sql,
                    "params": params,
                    "effective_timeout_ms": db.timeout,
                    "atomic_depth": db.atomic_depth,
                }
            )

        def close(self):
            pass

    @contextmanager
    def atomic():
        db.atomic_depth += 1
        events.append({"event": "atomic_enter", "depth": db.atomic_depth})
        try:
            yield
        finally:
            events.append({"event": "atomic_exit", "depth": db.atomic_depth})
            db.atomic_depth -= 1
            if not db.atomic_depth:
                db.timeout = None

    monkeypatch.setattr(tools, "connection", db)
    monkeypatch.setattr(tools.transaction, "atomic", atomic)
    monkeypatch.setattr(tools, "get_query_timeout_ms", lambda: 5000)
    schema = tools.GraphQL._schema
    original = schema.execute
    execution_depth = 0
    inner_context = tools.ScopeChatContext(user=category_schema.context.user)
    inner_context.planned_query_timeout_ms = inner_ms
    outer_context = tools.ScopeChatContext(user=category_schema.context.user)
    outer_context.planned_query_timeout_ms = outer_ms

    def execute(*args, **kwargs):
        nonlocal execution_depth
        execution_depth += 1
        try:
            if execution_depth == 1:
                if outer_started:
                    with db.cursor() as cursor:
                        cursor.execute("SELECT outer_first", [])
                inner = tools.query(
                    manager=category_schema.manager,
                    filters={},
                    fields=["id"],
                    context=inner_context,
                )
                assert inner["complete"]
                with db.cursor() as cursor:
                    cursor.execute("SELECT outer_population", [])
            else:
                with db.cursor() as cursor:
                    cursor.execute("SELECT inner_population", [])
            return original(*args, **kwargs)
        finally:
            execution_depth -= 1

    monkeypatch.setattr(schema, "execute", execute)
    outer = tools.query(
        manager=category_schema.manager,
        filters={},
        fields=["id"],
        context=outer_context,
    )
    assert outer["complete"] and outer["total_count"] == 3
    assert not db.execute_wrappers and db.atomic_depth == 0 and db.timeout is None
    inner_sql = next(
        event for event in events if event.get("sql") == "SELECT inner_population"
    )
    print(
        "NESTED_POSTGRES_BUDGET",
        {
            "outer_ms": outer_ms,
            "inner_ms": inner_ms,
            "events": events,
            "hooks_cleaned_up": True,
        },
    )
    assert inner_sql["effective_timeout_ms"] <= min(outer_ms, inner_ms), (
        "A nested query ran after its tighter timeout was overwritten by the outer hook"
    )
    outer_sql = next(
        event for event in events if event.get("sql") == "SELECT outer_population"
    )
    assert outer_sql["effective_timeout_ms"] == outer_ms
    if outer_started:
        outer_first = next(
            event for event in events if event.get("sql") == "SELECT outer_first"
        )
        assert outer_first["effective_timeout_ms"] == outer_ms


@pytest.mark.django_db(transaction=True)
def test_commit_callback_query_receives_its_own_budget_with_real_django_transactions(
    category_schema, monkeypatch
):
    db = connections["default"]
    events = []
    current_timeout = None
    callback_registered = False
    callback_results = []
    monkeypatch.setattr(db, "vendor", "postgresql")
    monkeypatch.setattr(tools, "connection", db)
    monkeypatch.setattr(tools, "get_query_timeout_ms", lambda: 10000)
    outer = tools.ScopeChatContext(
        user=category_schema.context.user, planned_query_timeout_ms=50
    )
    callback_context = tools.ScopeChatContext(
        user=category_schema.context.user, planned_query_timeout_ms=8000
    )

    original_create_cursor = db.create_cursor

    class BudgetCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def execute(self, sql, params=None):
            nonlocal current_timeout
            if sql.startswith("SET LOCAL"):
                current_timeout = params[0]
                events.append(("budget", current_timeout))
                return None
            return self.cursor.execute(sql, params)

    monkeypatch.setattr(
        db,
        "create_cursor",
        lambda *args, **kwargs: BudgetCursor(original_create_cursor(*args, **kwargs)),
    )

    def record_budget(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith("SELECT"):
            events.append(("select", current_timeout))
        return result

    def callback_query():
        callback_results.append(
            tools.query(
                manager=category_schema.manager,
                filters={},
                fields=["id"],
                context=callback_context,
            )
        )

    native = tools.GraphQL._schema.graphql_schema.get_type(category_schema.manager)
    original_id = native.fields["id"].resolve

    def id_resolver(row, info):
        nonlocal callback_registered
        with db.cursor() as cursor:
            cursor.execute("SELECT 1")
        if not callback_registered:
            callback_registered = True
            transaction.on_commit(callback_query)
        return original_id(row, info)

    monkeypatch.setattr(native.fields["id"], "resolve", id_resolver)
    initial_wrappers = list(db.execute_wrappers)
    with db.execute_wrapper(record_budget):
        result = tools.query(
            manager=category_schema.manager,
            filters={},
            fields=["id"],
            context=outer,
        )
        assert result["complete"] and result["total_count"] == 3
        assert len(callback_results) == 1
        assert callback_results[0]["complete"]
        assert not db.in_atomic_block
        assert db.execute_wrappers == [*initial_wrappers, record_budget]
    assert db.execute_wrappers == initial_wrappers
    assert [value for kind, value in events if kind == "budget"] == [50, 8000]
    assert [value for kind, value in events if kind == "select"] == [
        50,
        50,
        50,
        8000,
        8000,
        8000,
    ]
