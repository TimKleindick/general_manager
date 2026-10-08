"""Coverage never turns an unjudged or failed experiment into a model pass."""

from importlib.util import find_spec


def test_reporting_exists():
    assert find_spec("experiments.gm_eval.reporting") is not None


def test_summary_retains_failed_and_unjudged_denominator():
    from experiments.gm_eval.reporting import summarize

    report = summarize(
        [
            {
                "case_id": "E001",
                "status": "passed",
                "dimensions": {"D": True, "C": None},
            },
            {"case_id": "E002", "status": "transport_failure", "dimensions": {}},
            {"case_id": "E003", "status": "judge_failure", "dimensions": {"D": False}},
        ],
        mode="offline",
        catalog_size=100,
    )
    assert report["attempted_cases"] == 3
    assert report["catalog_cases"] == 100
    assert report["unattempted_cases"] == 97
    assert report["model_performance_measured"] is False
    assert report["end_to_end_success_rate"] is None
    assert report["status_counts"]["transport_failure"] == 1
    assert report["dimensions"]["D"] == {
        "passed": 1,
        "failed": 1,
        "not_applicable": 0,
        "not_scored": 1,
    }
    assert report["dimensions"]["C"] == {
        "passed": 0,
        "failed": 0,
        "not_applicable": 0,
        "not_scored": 3,
    }


def test_missing_telemetry_and_prices_are_unknown():
    from experiments.gm_eval.reporting import aggregate_calls

    totals = aggregate_calls(
        [
            {
                "kind": "provider",
                "model": "weak",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
            {"kind": "provider", "model": "strong", "usage": None},
            {"kind": "tool"},
            {
                "kind": "judge",
                "model": "strong",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        ],
        strong_models={"strong"},
    )
    assert totals["provider_calls"] == 2
    assert totals["tool_calls"] == 1
    assert totals["judge_calls"] == 1
    assert totals["input_tokens"] is None
    assert totals["reported_input_tokens"] == 8
    assert totals["usage_coverage"] == {"reported": 2, "eligible": 3}
    assert totals["strong_model_calls"] == 2
    assert totals["estimated_cost_usd"] is None
    assert totals["estimated_strong_model_cost_usd"] is None


def test_explicit_dated_prices_can_estimate_without_claiming_a_bill():
    from experiments.gm_eval.reporting import aggregate_calls

    totals = aggregate_calls(
        [
            {
                "kind": "provider",
                "model": "strong",
                "usage": {
                    "input_tokens": 1000000,
                    "output_tokens": 500000,
                    "reasoning_tokens": 200,
                },
            },
        ],
        strong_models={"strong"},
        prices={
            "strong": {
                "input_per_million_usd": "2",
                "output_per_million_usd": "4",
                "source": "operator-supplied",
                "as_of": "2026-10-03",
            }
        },
    )
    assert totals["estimated_cost_usd"] == "4.000000"
    assert totals["estimated_strong_model_cost_usd"] == "4.000000"
    assert totals["reasoning_tokens"] == 200


def test_duplicate_case_ids_and_unknown_statuses_fail_closed():
    import pytest
    from experiments.gm_eval.reporting import summarize

    with pytest.raises(ValueError, match="duplicate"):
        summarize(
            [{"case_id": "E001", "status": "passed"}] * 2,
            mode="offline",
            catalog_size=100,
        )
    with pytest.raises(ValueError, match="status"):
        summarize(
            [{"case_id": "E001", "status": "probably_ok"}],
            mode="offline",
            catalog_size=100,
        )


def test_not_applicable_is_distinct_from_missing_judgment():
    from experiments.gm_eval.reporting import summarize

    report = summarize(
        [
            {"case_id": "E001", "status": "unscored", "dimensions": {"C": "N/A"}},
            {
                "case_id": "E002",
                "status": "judge_failure",
                "dimensions": {"C": "unscored"},
            },
        ],
        mode="offline",
        catalog_size=100,
    )
    assert report["dimensions"]["C"]["not_applicable"] == 1
    assert report["dimensions"]["C"]["not_scored"] == 1


def test_summary_accepts_actual_scorer_output_without_losing_dimensions():
    from experiments.gm_eval.catalog import load_catalog
    from experiments.gm_eval.oracle import expected_turn
    from experiments.gm_eval.reporting import summarize
    from experiments.gm_eval.scoring import score_turn

    score = score_turn(expected_turn(load_catalog()[0], 0), {})
    report = summarize([score], mode="live", catalog_size=121)
    assert report["status_counts"] == {score["primary_failure"]: 1}
    for name, dimension in score["dimensions"].items():
        bucket = {
            "pass": "passed",
            "fail": "failed",
            "N/A": "not_applicable",
            "unscored": "not_scored",
        }[dimension["status"]]
        assert report["dimensions"][name][bucket] == 1


def test_case_summary_keeps_failed_and_missing_followup_turns():
    from experiments.gm_eval.reporting import case_result

    first = {
        "case_id": "E009",
        "turn_index": 0,
        "primary_failure": "passed",
        "dimensions": {"D": {"status": "pass"}, "C": {"status": "N/A"}},
    }
    second = {
        "case_id": "E009",
        "turn_index": 1,
        "primary_failure": "transport_failure",
        "dimensions": {"D": {"status": "N/A"}, "C": {"status": "unscored"}},
    }
    complete = case_result([first, second], expected_turns=2)
    assert complete["status"] == "transport_failure"
    assert complete["dimensions"]["D"] == "pass"
    assert complete["dimensions"]["C"] == "unscored"
    missing = case_result([first], expected_turns=2)
    assert missing["status"] == "unscored"
    assert missing["dimensions"]["D"] == "unscored"
    assert missing["missing_turns"] == [1]
