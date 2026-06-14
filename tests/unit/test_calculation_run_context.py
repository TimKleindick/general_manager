from general_manager.cache.dependency_cache import DependencyCacheHit
from general_manager.cache.run_context import (
    CalculationRunContext,
    current_calculation_run_context,
)


def test_context_is_active_only_inside_with_block() -> None:
    assert current_calculation_run_context() is None

    with CalculationRunContext() as ctx:
        assert current_calculation_run_context() is ctx

    assert current_calculation_run_context() is None


def test_get_or_set_reuses_loaded_value_inside_context() -> None:
    calls = 0

    def loader() -> int:
        nonlocal calls
        calls += 1
        return 42

    with CalculationRunContext() as ctx:
        assert ctx.get_or_set(("answer",), loader) == 42
        assert ctx.get_or_set(("answer",), loader) == 42

    assert calls == 1


def test_public_storage_helpers_store_and_check_values() -> None:
    with CalculationRunContext() as ctx:
        assert ctx.get("missing") is None
        assert ctx.get("missing", "fallback") == "fallback"
        assert not ctx.has("answer")
        assert "answer" not in ctx

        ctx.set("answer", 42)

        assert ctx.get("answer") == 42
        assert ctx.has("answer")
        assert "answer" in ctx


def test_dependency_cache_prefetch_hits_are_available_inside_context() -> None:
    hit = DependencyCacheHit(
        value="ready",
        dependencies=frozenset({("Project", "identification", '{"id": 1}')}),
    )

    with CalculationRunContext() as context:
        context.set_dependency_cache_hits({"cache-key": hit})

        assert context.get_dependency_cache_hit("cache-key") == hit
        assert context.get_dependency_cache_hit("missing", "fallback") == "fallback"


def test_dependency_cache_prefetch_hits_are_cleared_on_exit() -> None:
    hit = DependencyCacheHit(value=10, dependencies=frozenset())
    context = CalculationRunContext()

    with context:
        context.set_dependency_cache_hits({"cache-key": hit})
        assert context.get_dependency_cache_hit("cache-key") == hit

    assert context.get_dependency_cache_hit("cache-key", None) is None


def test_discard_prefix_removes_matching_tuple_keys_only() -> None:
    with CalculationRunContext() as ctx:
        ctx.set(("orm_instance", "Human", 1, "default"), "alice")
        ctx.set(("orm_instance", "Human", 2, "default"), "bob")
        ctx.set(("other", "Human", 1), "other")
        ctx.set("plain", "value")

        ctx.discard_prefix(("orm_instance", "Human", 1))

        assert not ctx.has(("orm_instance", "Human", 1, "default"))
        assert ctx.get(("orm_instance", "Human", 2, "default")) == "bob"
        assert ctx.get(("other", "Human", 1)) == "other"
        assert ctx.get("plain") == "value"


def test_index_loads_once_and_groups_by_key() -> None:
    calls = 0

    class Row:
        def __init__(self, day: str, value: int) -> None:
            self.day = day
            self.value = value

    def loader() -> list[Row]:
        nonlocal calls
        calls += 1
        return [Row("2026-06-10", 10), Row("2026-06-11", 11)]

    with CalculationRunContext() as ctx:
        first = ctx.index(
            key=("rows", 1),
            loader=loader,
            index_by=lambda row: row.day,
        )
        second = ctx.index(
            key=("rows", 1),
            loader=loader,
            index_by=lambda row: row.day,
        )

    assert calls == 1
    assert first is second
    assert first["2026-06-10"].value == 10
    assert first["2026-06-11"].value == 11


def test_group_by_loads_once_and_groups_rows() -> None:
    calls = 0

    class Row:
        def __init__(self, project_id: int, value: int) -> None:
            self.project_id = project_id
            self.value = value

    def loader() -> list[Row]:
        nonlocal calls
        calls += 1
        return [Row(1, 10), Row(1, 11), Row(2, 20)]

    with CalculationRunContext() as ctx:
        first = ctx.group_by(
            key=("rows", "project"),
            loader=loader,
            group_by=lambda row: row.project_id,
        )
        second = ctx.index_many(
            key=("rows", "project"),
            loader=loader,
            index_by=lambda row: row.project_id,
        )

    assert calls == 1
    assert first is second
    assert [row.value for row in first[1]] == [10, 11]
    assert [row.value for row in first[2]] == [20]
