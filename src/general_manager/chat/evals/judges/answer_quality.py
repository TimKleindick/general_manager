"""Judge: natural language answer quality."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AnswerQualityScore:
    """Result of answer quality judgment."""

    passed: bool
    score: float  # 0.0 - 1.0
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    PASS_THRESHOLD: float = 0.8


def judge_answer_quality(
    answer_contains: list[str],
    answer_excludes: list[str],
    answer_text: str,
) -> AnswerQualityScore:
    """Score the final answer on expected keyword/fact presence.

    ``answer_contains`` items are checked case-insensitively against the full
    answer text.  The score is the fraction of expected items found.  The case
    passes when the score is >= 80% **and** none of the ``answer_excludes``
    items appear.
    """
    answer_lower = answer_text.lower()

    found = 0
    missing: list[str] = []
    for item in answer_contains:
        if item.lower() in answer_lower:
            found += 1
        else:
            missing.append(item)

    unexpected = [item for item in answer_excludes if item.lower() in answer_lower]

    score = found / len(answer_contains) if answer_contains else 1.0
    passed = score >= AnswerQualityScore.PASS_THRESHOLD and len(unexpected) == 0

    return AnswerQualityScore(
        passed=passed,
        score=score,
        missing=missing,
        unexpected=unexpected,
    )
