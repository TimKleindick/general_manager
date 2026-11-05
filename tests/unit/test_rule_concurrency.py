import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from general_manager.rule.rule import Rule


def candidate_is_valid(candidate):
    return candidate.amount > 0


def test_concurrent_evaluations_keep_result_and_error_context():
    rule = Rule(candidate_is_valid, custom_error_message="Invalid: {amount}")
    failed = SimpleNamespace(amount=-2)
    passed = SimpleNamespace(amount=1)
    paused, resume = Event(), Event()

    def pause_failure(message, **kwargs):
        if message == "rule evaluation failed":
            paused.set()
            assert resume.wait(5)

    def evaluate_failed():
        result = rule.evaluate(failed)
        return (
            result,
            rule.last_evaluation_input,
            rule.last_evaluation_result,
            rule.get_error_message(),
        )

    with patch("general_manager.rule.rule.logger.info", side_effect=pause_failure):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(evaluate_failed)
            try:
                assert paused.wait(5)
                assert rule.evaluate(passed) is True
                assert rule.last_evaluation_input is passed
                assert rule.get_error_message() is None
            finally:
                resume.set()
            result, candidate, recorded, error = future.result(timeout=5)
    assert result is False
    assert candidate is failed
    assert recorded is False
    assert error == {"amount": "Invalid: -2"}


def test_async_tasks_keep_their_own_candidate_context():
    rule = Rule(candidate_is_valid, custom_error_message="Invalid: {amount}")
    first = SimpleNamespace(amount=-1)
    second = SimpleNamespace(amount=-2)

    async def run():
        ready = asyncio.Event()
        proceed = asyncio.Event()

        async def first_task():
            assert rule.evaluate(first) is False
            ready.set()
            await proceed.wait()
            return rule.last_evaluation_input, rule.get_error_message()

        async def second_task():
            await ready.wait()
            assert rule.evaluate(second) is False
            proceed.set()
            return rule.last_evaluation_input, rule.get_error_message()

        return await asyncio.gather(first_task(), second_task())

    first_result, second_result = asyncio.run(run())
    assert first_result == (first, {"amount": "Invalid: -1"})
    assert second_result == (second, {"amount": "Invalid: -2"})


def test_evaluation_context_does_not_keep_short_lived_rules_alive():
    import gc
    import weakref

    rule = Rule(candidate_is_valid)
    candidate = SimpleNamespace(amount=1)
    rule_ref = weakref.ref(rule)
    candidate_ref = (
        weakref.ref(candidate) if hasattr(candidate, "__weakref__") else None
    )
    assert rule.evaluate(candidate) is True
    del rule
    gc.collect()
    assert rule_ref() is None
    del candidate
    gc.collect()
    assert candidate_ref() is None
