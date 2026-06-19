"""Tests for the GraphQL warm-up management command."""

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class GraphQLWarmUpCommandTests(SimpleTestCase):
    """Verify command input validation."""

    def test_invalid_dotted_manager_path_raises_command_error(self) -> None:
        """Invalid dotted manager paths are reported as CommandError."""
        with self.assertRaisesRegex(CommandError, "Invalid GraphQL manager path"):
            call_command("graphql_warmup", "--manager", "missing.Manager")
