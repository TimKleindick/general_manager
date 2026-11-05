import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from general_manager.rule.rule import Rule


def candidate_is_valid(candidate):
    """Require a positive amount."""
    return candidate.amount > 0


def test_concurrent_evaluations_keep_result_and_error_context():
    """A concurrent success cannot replace a failed result or its context."""
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
    """Tasks retain their own custom message context."""
    rule = Rule(candidate_is_valid, custom_error_message="Invalid: {amount}")
    first = SimpleNamespace(amount=-1)
    second = SimpleNamespace(amount=-2)
    parent = SimpleNamespace(amount=-9)
    assert rule.evaluate(parent) is False

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
    assert rule.last_evaluation_input is parent
    assert rule.get_error_message() == {"amount": "Invalid: -9"}


def test_evaluation_context_does_not_keep_short_lived_rules_alive():
    """The current context does not keep short-lived rules alive."""
    import gc
    import weakref

    rule = Rule(candidate_is_valid)

    class Candidate:
        amount = 1

    candidate = Candidate()
    rule_ref = weakref.ref(rule)
    candidate_ref = weakref.ref(candidate)
    assert rule.evaluate(candidate) is True
    del rule
    gc.collect()
    assert rule_ref() is None
    del candidate
    gc.collect()
    assert candidate_ref() is None


def test_context_does_not_root_rule_candidate_class_cycle():
    """A context token must not keep a dynamic rule's class graph alive."""
    import gc
    import weakref

    rule = Rule(candidate_is_valid)

    class Candidate:
        amount = -1
        rules: ClassVar[list] = [rule]

    candidate = Candidate()
    candidate_ref = weakref.ref(candidate)
    class_ref = weakref.ref(Candidate)
    rule_ref = weakref.ref(rule)
    assert rule.evaluate(candidate) is False
    del candidate
    gc.collect()
    assert rule.last_evaluation_input is candidate_ref()
    assert candidate_ref() is not None
    del rule, Candidate
    gc.collect()
    assert rule_ref() is None
    assert candidate_ref() is None
    assert class_ref() is None


def test_async_tasks_keep_automatic_comparison_messages_separate():
    """Automatic messages retain the operand for the evaluated candidate."""
    rule = Rule(candidate_is_valid)

    async def run():
        ready, proceed = asyncio.Event(), asyncio.Event()

        async def first():
            assert rule.evaluate(SimpleNamespace(amount=-1)) is False
            ready.set()
            await proceed.wait()
            return rule.get_error_message()

        async def second():
            await ready.wait()
            assert rule.evaluate(SimpleNamespace(amount=-2)) is False
            proceed.set()
            return rule.get_error_message()

        return await asyncio.gather(first(), second())

    assert asyncio.run(run()) == [
        {"amount": "[amount] (-1) must be > 0!"},
        {"amount": "[amount] (-2) must be > 0!"},
    ]
