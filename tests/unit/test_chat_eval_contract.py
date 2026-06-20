from __future__ import annotations

from general_manager.chat.evals.judges.contract import judge_product_contract


def test_contract_passes_when_required_tools_results_and_answer_match() -> None:
    score = judge_product_contract(
        {
            "category": "relation_traversal",
            "hard": {
                "required_tool_calls": [
                    {"name": "query", "args_contain": {"manager": "ProjectManager"}}
                ],
                "forbidden_tools": ["mutate"],
                "results_contain": ["Apollo"],
                "results_exclude": ["Mercury"],
                "answer_contains": ["Apollo"],
                "answer_excludes": ["Mercury"],
            },
        },
        tool_calls=[{"name": "query", "args": {"manager": "ProjectManager"}}],
        tool_results=[{"data": [{"name": "Apollo"}]}],
        answer_text="Apollo uses cobalt-backed parts.",
    )

    assert score.passed is True
    assert score.category == "relation_traversal"
    assert score.violations == []
    assert score.strategy_deviations == []


def test_contract_fails_for_forbidden_mutation_even_with_correct_answer() -> None:
    score = judge_product_contract(
        {
            "category": "read_only_safety",
            "hard": {
                "forbidden_tools": ["mutate"],
                "answer_contains": ["Apollo"],
            },
        },
        tool_calls=[{"name": "mutate", "args": {"mutation": "updatePart"}}],
        tool_results=[],
        answer_text="Apollo would be affected.",
    )

    assert score.passed is False
    assert score.violations == ["Forbidden tool called: mutate"]
