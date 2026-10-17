"""Compatibility import for the packaged evaluation terminal-category owner."""

from general_manager.chat.evals.outcomes import (
    terminal_failure_categories,
    runtime_failure_provenance,
)

__all__ = ["runtime_failure_provenance", "terminal_failure_categories"]
