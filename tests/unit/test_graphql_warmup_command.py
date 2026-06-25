"""Tests for the GraphQL warm-up management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class GraphQLWarmUpCommandTests(SimpleTestCase):
    """Verify command input validation."""

    def test_invalid_dotted_manager_path_raises_command_error(self) -> None:
        """Invalid dotted manager paths are reported as CommandError."""
        with self.assertRaisesRegex(CommandError, "Invalid GraphQL manager path"):
            call_command("graphql_warmup", "--manager", "missing.Manager")


class GraphQLWarmUpRefreshDueCommandTests(SimpleTestCase):
    """Verify the timeout-recipe refresh management command."""

    def test_refresh_due_delegates_without_limit(self) -> None:
        """The command delegates an omitted limit as None and prints a summary."""
        stdout = StringIO()
        with patch(
            "general_manager.management.commands.graphql_warmup_refresh_due."
            "refresh_due_graphql_warmup_recipes",
            return_value=3,
        ) as refresh:
            call_command("graphql_warmup_refresh_due", stdout=stdout)

        refresh.assert_called_once_with(limit=None)
        self.assertIn("GraphQL warm-up refreshed 3 recipes.", stdout.getvalue())

    def test_refresh_due_parses_cli_limit(self) -> None:
        """The command parses CLI --limit values to integers."""
        stdout = StringIO()
        with patch(
            "general_manager.management.commands.graphql_warmup_refresh_due."
            "refresh_due_graphql_warmup_recipes",
            return_value=1,
        ) as refresh:
            call_command("graphql_warmup_refresh_due", "--limit", "5", stdout=stdout)

        refresh.assert_called_once_with(limit=5)
        self.assertIn("GraphQL warm-up refreshed 1 recipe.", stdout.getvalue())

    def test_refresh_due_rejects_programmatic_boolean_limit(self) -> None:
        """Programmatic limits reject bool even though bool subclasses int."""
        with self.assertRaisesRegex(CommandError, "limit must be an integer"):
            call_command("graphql_warmup_refresh_due", limit=True)

    def test_refresh_due_rejects_programmatic_string_limit(self) -> None:
        """Programmatic limits must already be integers or None."""
        with self.assertRaisesRegex(CommandError, "limit must be an integer"):
            call_command("graphql_warmup_refresh_due", limit="5")
