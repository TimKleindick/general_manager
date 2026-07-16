"""Tests for selecting the database used by the Django test suite."""

import pytest

from tests.test_settings import _database_configs


def test_database_configs_default_to_separate_in_memory_sqlite_aliases() -> None:
    assert _database_configs({}) == {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        },
        "secondary": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        },
    }


@pytest.mark.parametrize(
    ("selector", "engine"),
    [
        ("postgresql", "django.db.backends.postgresql"),
        ("mariadb", "django.db.backends.mysql"),
    ],
)
def test_database_configs_select_server_backend_for_both_aliases(
    selector: str,
    engine: str,
) -> None:
    environ = {
        "GENERAL_MANAGER_TEST_DATABASE": selector,
        "GENERAL_MANAGER_TEST_DATABASE_NAME": "general_manager",
        "GENERAL_MANAGER_TEST_DATABASE_USER": "test-user",
        "GENERAL_MANAGER_TEST_DATABASE_PASSWORD": "test-password",
        "GENERAL_MANAGER_TEST_DATABASE_HOST": "database.local",
        "GENERAL_MANAGER_TEST_DATABASE_PORT": "1234",
    }

    assert _database_configs(environ) == {
        "default": {
            "ENGINE": engine,
            "NAME": "general_manager",
            "USER": "test-user",
            "PASSWORD": "test-password",
            "HOST": "database.local",
            "PORT": "1234",
        },
        "secondary": {
            "ENGINE": engine,
            "NAME": "general_manager_secondary",
            "USER": "test-user",
            "PASSWORD": "test-password",
            "HOST": "database.local",
            "PORT": "1234",
        },
    }


def test_database_configs_use_explicit_secondary_server_name() -> None:
    environ = {
        "GENERAL_MANAGER_TEST_DATABASE": "postgresql",
        "GENERAL_MANAGER_TEST_DATABASE_NAME": "general_manager",
        "GENERAL_MANAGER_TEST_SECONDARY_DATABASE_NAME": "other_database",
        "GENERAL_MANAGER_TEST_DATABASE_USER": "test-user",
        "GENERAL_MANAGER_TEST_DATABASE_PASSWORD": "test-password",
        "GENERAL_MANAGER_TEST_DATABASE_HOST": "database.local",
        "GENERAL_MANAGER_TEST_DATABASE_PORT": "1234",
    }

    assert _database_configs(environ)["secondary"]["NAME"] == "other_database"


def test_database_configs_reject_unknown_backend() -> None:
    with pytest.raises(
        ValueError,
        match="Unsupported GENERAL_MANAGER_TEST_DATABASE 'oracle'",
    ):
        _database_configs({"GENERAL_MANAGER_TEST_DATABASE": "oracle"})
