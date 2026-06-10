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
