"""Execute published grouping examples against the generated GraphQL schema."""

from __future__ import annotations

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLGroupingQueries(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                status = CharField(max_length=20)
                name = CharField(max_length=100, null=True, blank=True)

        cls.general_manager_classes = [Project]
        cls.project = Project

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        get_user_model().objects.create_user(
            username="grouping-docs-user", password=password
        )
        self.client.login(username="grouping-docs-user", password=password)

    def test_published_text_aggregation_query(self) -> None:
        for status, names in (
            ("empty", (None, None)),
            ("other", ("Gamma",)),
            ("active", ("Alpha", "Alpha", "Beta", None)),
        ):
            for name in names:
                self.project.Factory.create(status=status, name=name)

        cookbook = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "examples"
            / "graphql_queries.md"
        ).read_text(encoding="utf-8")
        section = cookbook.split("## Aggregate unique text values in groups\n", 1)[
            1
        ].split("\n## ", 1)[0]
        example = re.search(r"```graphql\n(.*?)\n```", section, re.DOTALL)
        self.assertIsNotNone(example)
        assert example is not None

        response = self.query(example.group(1))

        self.assertResponseNoErrors(response)
        grouped = response.json()["data"]["projectGroups"]
        self.assertEqual(
            grouped["items"],
            [
                {"status": "active", "name": "Alpha, Beta"},
                {"status": "other", "name": "Gamma"},
                {"status": "empty", "name": None},
            ],
        )
        self.assertEqual(grouped["pageInfo"]["totalCount"], 3)
