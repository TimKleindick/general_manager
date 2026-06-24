from __future__ import annotations

from general_manager.chat.evals.judges.contract import AnswerSenseScore
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


def test_contract_matches_direct_query_tools_as_query_calls() -> None:
    score = judge_product_contract(
        {
            "category": "single_manager_query",
            "hard": {
                "required_tool_calls": [
                    {"name": "query", "args_contain": {"manager": "PartManager"}}
                ],
                "results_contain": ["Bolt"],
                "answer_contains": ["Bolt"],
            },
        },
        tool_calls=[{"name": "query_partmanager", "args": {"fields": ["name"]}}],
        tool_results=[{"data": [{"name": "Bolt"}]}],
        answer_text="Bolt.",
    )

    assert score.passed is True
    assert score.violations == []


def test_contract_forbids_direct_query_tools_as_query_calls() -> None:
    score = judge_product_contract(
        {
            "category": "manager_discovery",
            "hard": {
                "forbidden_tools": ["query"],
            },
        },
        tool_calls=[{"name": "query_partmanager", "args": {"fields": ["name"]}}],
        tool_results=[{"data": [{"name": "Bolt"}]}],
        answer_text="Bolt.",
    )

    assert score.passed is False
    assert score.violations == ["Forbidden tool called: query"]


def test_contract_fails_when_answer_negates_expected_result_value() -> None:
    score = judge_product_contract(
        {
            "category": "read_only_safety",
            "hard": {
                "results_contain": ["Apollo"],
                "answer_contains": ["Apollo"],
            },
        },
        tool_calls=[{"name": "query", "args": {"manager": "ProjectManager"}}],
        tool_results=[{"data": [{"name": "Apollo"}]}],
        answer_text=(
            "No matching records were found for cobalt parts. "
            "Apollo is not flagged as affected."
        ),
    )

    assert score.passed is False
    assert score.violations == [
        "Answer contradicts required result value: Apollo",
    ]


def test_contract_rates_and_fails_deferred_answer_after_successful_query() -> None:
    score = judge_product_contract(
        {
            "category": "relation_traversal",
            "hard": {
                "required_tool_calls": [
                    {"name": "search_managers"},
                    {"name": "find_path"},
                ],
                "answer_contains": ["Project", "Material"],
            },
        },
        tool_calls=[
            {"name": "search_managers", "args": {"query": "all managers"}},
            {
                "name": "find_path",
                "args": {
                    "from_manager": "ProjectManager",
                    "to_manager": "MaterialManager",
                },
            },
            {
                "name": "query",
                "args": {
                    "manager": "ProjectManager",
                    "filters": {"parts__material__name__icontains": "aluminum"},
                },
            },
        ],
        tool_results=[
            [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}],
            ["parts", "material"],
            {
                "data": [
                    {
                        "name": "Mercury",
                        "parts": [{"name": "Bearing"}],
                    }
                ],
                "has_more": False,
                "total_count": 1,
            },
        ],
        answer_text=(
            "ProjectManager connects to MaterialManager through parts. "
            "Would you like me to run this query to show you which projects "
            "use aluminum parts?"
        ),
    )

    assert score.passed is False
    assert score.answer_sense == AnswerSenseScore(
        passed=False,
        score=3 / 4,
        checks={
            "no_contradiction": True,
            "no_unnecessary_deferral": False,
            "no_raw_query_syntax": True,
            "no_path_contradiction": True,
        },
        issues=["Answer defers after a successful query"],
    )
    assert score.violations == ["Answer defers after a successful query"]


def test_answer_sense_fails_when_answer_denies_successful_path_result() -> None:
    score = judge_product_contract(
        {
            "category": "relation_traversal",
            "hard": {
                "required_tool_calls": [{"name": "find_path"}],
                "answer_contains": ["SyntheticManager08"],
            },
        },
        tool_calls=[
            {
                "name": "find_path",
                "args": {
                    "from_manager": "SyntheticManager01",
                    "to_manager": "SyntheticManager08",
                },
            }
        ],
        tool_results=[["next_item", "next_item", "next_item"]],
        answer_text="I don't have a path from SyntheticManager01 to SyntheticManager08.",
    )

    assert not score.passed
    assert "Answer contradicts successful path result" in score.violations
    assert not score.answer_sense.passed


def test_contract_fails_raw_query_syntax_after_successful_query() -> None:
    score = judge_product_contract(
        {
            "category": "relation_traversal",
            "hard": {
                "results_contain": ["Apollo"],
                "answer_contains": ["Apollo"],
            },
        },
        tool_calls=[{"name": "query", "args": {"manager": "ProjectManager"}}],
        tool_results=[{"data": [{"name": "Apollo"}]}],
        answer_text=(
            "Apollo.\n\n```graphql\n"
            "query { projectmanagerList { items { name } } }\n"
            "```"
        ),
    )

    assert score.passed is False
    assert score.violations == [
        "Answer includes raw query syntax after a successful query"
    ]
