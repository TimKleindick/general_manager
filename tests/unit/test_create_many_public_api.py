"""Public import and typing checks for the bounded creation API."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import general_manager
import general_manager.manager as manager_api
from general_manager.manager.bulk_create import (
    CreateManyBatchResult,
    CreateManyError,
    CreateManyInvalidBatchSizeError,
    CreateManyPostCommitError,
    CreateManyUnsupportedError,
)
from general_manager.manager.general_manager import GeneralManager


_CREATE_MANY_EXPORTS = {
    "CreateManyBatchResult": CreateManyBatchResult,
    "CreateManyError": CreateManyError,
    "CreateManyInvalidBatchSizeError": CreateManyInvalidBatchSizeError,
    "CreateManyPostCommitError": CreateManyPostCommitError,
    "CreateManyUnsupportedError": CreateManyUnsupportedError,
}


def test_create_many_symbols_have_stable_root_and_manager_imports() -> None:
    for name, expected in _CREATE_MANY_EXPORTS.items():
        assert name in general_manager.__all__
        assert name in manager_api.__all__
        assert getattr(general_manager, name) is expected
        assert getattr(manager_api, name) is expected


def test_create_many_signature_and_result_error_fields_are_publicly_typed() -> None:
    signature = inspect.signature(GeneralManager.create_many)
    assert list(signature.parameters) == [
        "records",
        "creator_id",
        "history_comment",
        "ignore_permission",
        "batch_size",
    ]
    assert (
        signature.parameters["records"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    )
    assert signature.parameters["creator_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["batch_size"].default == 1000
    assert {
        "start_index",
        "end_index",
        "ids",
        "successful_count",
        "committed_successful_count",
        "pending_successful_count",
        "database_alias",
        "committed",
    } <= set(CreateManyBatchResult.__dataclass_fields__)
    assert {"failure_index", "cause", "successful_count", "committed"} <= set(
        inspect.signature(CreateManyError).parameters
    )


def test_create_many_exports_are_present_in_the_public_snapshot() -> None:
    snapshot_path = Path(__file__).parents[1] / "snapshots" / "public_api_exports.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for module_name in ("general_manager", "general_manager.manager"):
        exports = snapshot[module_name]
        for name in _CREATE_MANY_EXPORTS:
            assert exports[name] == [
                "general_manager.manager.bulk_create",
                name,
            ]
