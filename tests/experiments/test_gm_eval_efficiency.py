"""Efficiency comparisons never reward wrong answers or invent universal budgets."""

from copy import deepcopy


def _run(calls=5, *, correct=True):
    return {
        "case_id": "E101",
        "core_id": "K13",
        "manager_count": 5,
        "snapshot": "RANK",
        "variant": "base",
        "seed": 17,
        "fixture_sha256": "same-data",
        "source_sha256": "same-code",
        "prompt_sha256": "same-question",
        "reference_version": "1.2",
        "profile": "weak-only",
        "task_quality_status": "passed" if correct else "model_task_failure",
        "mode": "live",
        "measurements": {
            "provider_requests": calls,
            "tool_calls": 1,
            "input_tokens": None,
            "turn_latency_seconds": 1.0,
            "strong_model_requests": 0,
            "unnecessary_clarification": False,
        },
    }


def test_reference_comparison_uses_observed_distribution():
    from experiments.gm_eval.efficiency import compare_to_reference

    result = compare_to_reference(_run(8), [_run(4), _run(5), _run(6)])
    assert result["comparable"] is True
    assert result["reference_repetitions"] == 3
    metric = result["metrics"]["provider_requests"]
    assert metric["reference_median"] == 5
    assert metric["delta"] == 3
    assert metric["ratio"] == 1.6
    assert metric["above_observed_reference"] is True
    assert result["metrics"]["input_tokens"]["ratio"] is None
    assert result["universal_budget_applied"] is False


def test_wrong_answers_or_mismatched_questions_are_not_speed_wins():
    from experiments.gm_eval.efficiency import compare_to_reference

    assert compare_to_reference(_run(1, correct=False), [_run()])["comparable"] is False
    changed = deepcopy(_run())
    changed["prompt_sha256"] = "different-question"
    assert compare_to_reference(changed, [_run()])["comparable"] is False
    changed = deepcopy(_run())
    changed["manager_count"] = 250
    assert compare_to_reference(changed, [_run()])["comparable"] is False
    assert compare_to_reference(changed, [_run()], axis="scale")["comparable"] is True


def test_zero_reference_escalation_does_not_divide_by_zero():
    from experiments.gm_eval.efficiency import compare_to_reference

    changed = deepcopy(_run())
    changed["measurements"]["strong_model_requests"] = 1
    row = compare_to_reference(changed, [_run()])["metrics"]["strong_model_requests"]
    assert row["ratio"] is None
    assert row["new_activity"] is True


def test_missing_usage_and_semantic_decision_stay_unknown():
    from experiments.gm_eval.efficiency import measure_run

    run = {
        "trace": {
            "provider_calls": [
                {
                    "role": "planner",
                    "model": "weak",
                    "request_count": 2,
                    "reported_usage": None,
                },
                {
                    "role": "fallback",
                    "model": "strong",
                    "strong": True,
                    "request_count": 1,
                    "reported_usage": {"input_tokens": 10, "output_tokens": 3},
                },
            ]
        },
        "turns": [{"latency_seconds": 0.3, "tool_calls": [{}, {}]}],
    }
    observed = measure_run(run)
    assert observed["provider_requests"] == 3
    assert observed["planning_requests"] == 2
    assert observed["strong_model_requests"] == 1
    assert observed["tool_calls"] == 2
    assert observed["input_tokens"] is None
    assert observed["unnecessary_clarification"] is None
