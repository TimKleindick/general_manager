from __future__ import annotations

from general_manager.chat.evals.fingerprints import (
    build_run_fingerprint,
    stable_hash,
)


def test_stable_hash_ignores_dict_key_order() -> None:
    left = {"tool": {"name": "query", "args": {"manager": "PartManager", "limit": 5}}}
    right = {"tool": {"args": {"limit": 5, "manager": "PartManager"}, "name": "query"}}

    assert stable_hash(left) == stable_hash(right)


def test_build_run_fingerprint_contains_prompt_tool_model_and_dataset_hashes() -> None:
    fingerprint = build_run_fingerprint(
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        tags=["demo"],
        prompt="system prompt text",
        tool_definitions=[
            {
                "name": "query",
                "description": "Execute a structured read query via GraphQL.",
                "input_schema": {"type": "object"},
            }
        ],
    )

    assert fingerprint["provider"] == "OllamaProvider"
    assert fingerprint["model"] == "glm-4.7-flash:q4_K_M"
    assert fingerprint["fixture"] == "toy"
    assert fingerprint["datasets"] == ["demo_readiness"]
    assert fingerprint["tier"] == 1
    assert fingerprint["tags"] == ["demo"]
    assert len(fingerprint["prompt_hash"]) == 16
    assert len(fingerprint["tool_hash"]) == 16
    assert len(fingerprint["run_hash"]) == 16


def test_bounded_list_gate_has_a_distinct_versioned_run_fingerprint() -> None:
    current = build_run_fingerprint(
        provider="local",
        model="offline",
        fixture="toy",
        datasets=["basic_queries"],
        tier=0,
        tags=[],
        prompt="same prompt",
        tool_definitions=[],
    )
    assert current["evaluation_contract"] == "chat-evaluation/5"
    historical = {key: value for key, value in current.items() if key != "run_hash"}
    historical["evaluation_contract"] = "chat-evaluation/3"
    assert current["run_hash"] != stable_hash(historical)
