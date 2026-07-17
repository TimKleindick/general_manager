"""Shared database-specific helpers for the test suite."""

from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

import pytest
from django.db import connection


SQLITE_ONLY_REASON = "exercises SQLite-specific locking or transaction behavior"

sqlite_only = pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason=SQLITE_ONLY_REASON,
)


def sqlite_only_mark(test: object) -> Any:
    """Return the single SQLite skip marker attached to a test."""
    marks = [mark for mark in getattr(test, "pytestmark", ()) if mark.name == "skipif"]
    assert len(marks) == 1
    return marks[0]


def sqlite_subprocess_environment(settings_module: str) -> dict[str, str]:
    """Build a SQLite subprocess environment without empty path entries."""
    python_path = [os.path.join(os.getcwd(), "src"), os.getcwd()]
    if existing_python_path := os.environ.get("PYTHONPATH"):
        python_path.append(existing_python_path)
    return {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": settings_module,
        "GENERAL_MANAGER_TEST_DATABASE": "sqlite",
        "PYTHONPATH": os.pathsep.join(python_path),
    }


def drop_test_models(editor: Any, model_classes: Iterable[type[Any]]) -> None:
    """Drop every model and re-raise the first deletion failure afterward."""
    deletion_error: Exception | None = None
    for model_class in model_classes:
        try:
            editor.delete_model(model_class)
        except Exception as error:  # noqa: BLE001 - cleanup must continue.
            if deletion_error is None:
                deletion_error = error
    if deletion_error is not None:
        raise deletion_error


def create_test_models(
    database_connection: Any,
    model_classes: Iterable[type[Any]],
) -> list[type[Any]]:
    """Create models and roll back successful creations if a later one fails."""
    created_models: list[type[Any]] = []
    try:
        with database_connection.schema_editor() as editor:
            for model_class in model_classes:
                editor.create_model(model_class)
                created_models.append(model_class)
    except Exception:
        if created_models:
            with suppress(Exception):
                with database_connection.schema_editor() as editor:
                    drop_test_models(editor, reversed(created_models))
        raise
    return created_models
