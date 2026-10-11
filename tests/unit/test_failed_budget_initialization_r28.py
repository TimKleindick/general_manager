from contextlib import contextmanager, nullcontext
import pytest
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from general_manager.chat import tools
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
)

category_schema = _category_fixture


def test_failed_budget_setup_cannot_allow_later_graphql_resolver_sql(
    category_schema, monkeypatch
):
    events = []

    class Connection:
        vendor = "postgresql"
        execute_wrapper = BaseDatabaseWrapper.execute_wrapper

        def __init__(self):
            self.execute_wrappers = []
            self.wrap_database_errors = nullcontext()
            self.atomic_depth = 0

        def validate_no_broken_transaction(self):
            pass

        def cursor(self):
            return CursorWrapper(RawCursor(), self)

    db = Connection()

    class RawCursor:
        def execute(self, sql, params=None):
            events.append({"sql": sql, "params": params})
            if sql.startswith("SET LOCAL"):
                message = (
                    "Budget setup intercepted by a failing instrumentation/driver hook"
                )
                raise RuntimeError(message)

        def close(self):
            pass

    @contextmanager
    def atomic():
        db.atomic_depth += 1
        try:
            yield
        finally:
            db.atomic_depth -= 1

    monkeypatch.setattr(tools, "connection", db)
    monkeypatch.setattr(tools.transaction, "atomic", atomic)
    monkeypatch.setattr(tools, "get_query_timeout_ms", lambda: 50)
    native = tools.GraphQL._schema.graphql_schema.get_type(category_schema.manager)
    original_id = native.fields["id"].resolve
    original_name = native.fields["name"].resolve

    def id_resolver(row, info):
        with db.cursor() as cursor:
            cursor.execute("SELECT id_value", [])
        return original_id(row, info)

    def name_resolver(row, info):
        with db.cursor() as cursor:
            cursor.execute("SELECT name_value", [])
        return original_name(row, info)

    monkeypatch.setattr(native.fields["id"], "resolve", id_resolver)
    monkeypatch.setattr(native.fields["name"], "resolve", name_resolver)
    # Real Graphene execution catches the first resolver's SET failure and
    # continues its sibling fields. This is not an injected schema.execute error.
    with pytest.raises(ValueError, match="Budget setup intercepted"):
        tools.query(
            manager=category_schema.manager,
            filters={},
            fields=["id", "name"],
            context=category_schema.context,
        )
    assert not db.execute_wrappers and db.atomic_depth == 0
    print("FAILED_BUDGET_INITIALIZATION", {"events": events, "hooks_cleaned_up": True})
    assert not [event for event in events if event["sql"].startswith("SELECT")], (
        "Later resolver SQL ran after the query budget failed to initialize"
    )


@pytest.mark.django_db(transaction=True)
def test_nested_setup_failure_keeps_real_django_savepoint_cleanup_and_fresh_queries(
    category_schema, monkeypatch
):
    db = connections["default"]
    events = []
    fail_setup = True
    monkeypatch.setattr(db, "vendor", "postgresql")
    monkeypatch.setattr(tools, "connection", db)
    monkeypatch.setattr(tools, "get_query_timeout_ms", lambda: 5000)

    original_create_cursor = db.create_cursor

    class BudgetCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def execute(self, sql, params=None):
            # Adapt just PostgreSQL's SET at the driver boundary. All Django
            # wrappers and real transaction/savepoint statements still execute.
            if sql.startswith("SET LOCAL"):
                events.append(sql)
                if fail_setup:
                    message = "Nested budget setup failed"
                    raise RuntimeError(message)
                return None
            return self.cursor.execute(sql, params)

    monkeypatch.setattr(
        db,
        "create_cursor",
        lambda *args, **kwargs: BudgetCursor(original_create_cursor(*args, **kwargs)),
    )

    def record_budget(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if not sql.startswith("SET LOCAL"):
            events.append(sql)
        return result

    native = tools.GraphQL._schema.graphql_schema.get_type(category_schema.manager)
    original_id = native.fields["id"].resolve

    def id_resolver(row, info):
        with db.cursor() as cursor:
            cursor.execute("SELECT 1")
        return original_id(row, info)

    monkeypatch.setattr(native.fields["id"], "resolve", id_resolver)
    schema = tools.GraphQL._schema
    original_execute = schema.execute
    execution_depth = 0
    outer = tools.ScopeChatContext(
        user=category_schema.context.user, planned_query_timeout_ms=2000
    )
    inner = tools.ScopeChatContext(
        user=category_schema.context.user, planned_query_timeout_ms=50
    )

    def execute(*args, **kwargs):
        nonlocal execution_depth
        execution_depth += 1
        try:
            if fail_setup and execution_depth == 1:
                with pytest.raises(ValueError, match="Nested budget setup failed"):
                    tools.query(
                        manager=category_schema.manager,
                        filters={},
                        fields=["id"],
                        context=inner,
                    )
            return original_execute(*args, **kwargs)
        finally:
            execution_depth -= 1

    monkeypatch.setattr(schema, "execute", execute)
    initial_wrappers = list(db.execute_wrappers)
    with db.execute_wrapper(record_budget):
        with pytest.raises(ValueError, match="Nested budget setup failed"):
            tools.query(
                manager=category_schema.manager,
                filters={},
                fields=["id"],
                context=outer,
            )
        assert not any(sql.startswith("SELECT") for sql in events)
        assert any(sql.startswith("SAVEPOINT") for sql in events)
        assert any(sql.startswith("ROLLBACK TO SAVEPOINT") for sql in events)
        assert any(sql.startswith("RELEASE SAVEPOINT") for sql in events)
        assert not db.in_atomic_block
        assert db.execute_wrappers == [*initial_wrappers, record_budget]
        fail_setup = False
        fresh = tools.query(
            manager=category_schema.manager,
            filters={},
            fields=["id"],
            context=outer,
        )
        assert fresh["complete"] and fresh["total_count"] == 3
        assert not db.in_atomic_block
    assert db.execute_wrappers == initial_wrappers
