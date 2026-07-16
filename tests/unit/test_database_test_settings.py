"""Tests for selecting the database used by the Django test suite."""

import pytest

from tests.test_settings import _database_config


def test_database_config_defaults_to_in_memory_sqlite() -> None:
    assert _database_config({}) == {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }


@pytest.mark.parametrize(
    ("selector", "engine"),
    [
        ("postgresql", "django.db.backends.postgresql"),
        ("mariadb", "django.db.backends.mysql"),
    ],
)
def test_database_config_selects_server_backend(
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

    assert _database_config(environ) == {
        "ENGINE": engine,
        "NAME": "general_manager",
        "USER": "test-user",
        "PASSWORD": "test-password",
        "HOST": "database.local",
        "PORT": "1234",
    }


def test_database_config_rejects_unknown_backend() -> None:
    with pytest.raises(
        ValueError,
        match="Unsupported GENERAL_MANAGER_TEST_DATABASE 'oracle'",
    ):
        _database_config({"GENERAL_MANAGER_TEST_DATABASE": "oracle"})
