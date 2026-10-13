"""Execute the collection-input cookbook's actual Python and GraphQL snippets."""

import json
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestCollectionInputCookbook(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class Employee(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=50)

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=50)

        class Customer(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=50)

        cls.cookbook = (
            Path(__file__).resolve().parents[2] / "docs/examples/collection_inputs.md"
        ).read_text(encoding="utf-8")
        example = re.search(r"```python\n(.*?)\n```", cls.cookbook, re.DOTALL)
        assert example is not None
        namespace = {
            "__name__": __name__,
            "Employee": Employee,
            "Project": Project,
            "Customer": Customer,
        }
        # Execute only the version-controlled cookbook, never supplied user code.
        exec(compile(example.group(1), "collection_inputs.md", "exec"), namespace)  # noqa: S102
        cls.Summary = namespace["WorkforceSummary"]
        cls.Employee = Employee
        cls.general_manager_classes = [Employee, Project, Customer, cls.Summary]

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="collection-cookbook", email="cookbook@example.test"
        )
        self.client.force_login(user)
        self.Employee.Interface._model.objects.create(pk=17, name="Alice")
        self.Employee.Interface._model.objects.create(pk=18, name="Bob")

    def test_published_summary_query_and_variables(self):
        query = re.search(r"```graphql\n(.*?)\n```", self.cookbook, re.DOTALL)
        variables = re.search(r"```json\n(.*?)\n```", self.cookbook, re.DOTALL)
        assert query is not None and variables is not None
        response = self.query(query.group(1), variables=json.loads(variables.group(1)))
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["workforceSummary"],
            {
                "month": "2026-10-01",
                "selectedEmployeeCount": 2,
                "employees": [{"id": "17"}, {"id": "18"}],
            },
        )
        examples = re.findall(r"```python\n(.*?)\n```", self.cookbook, re.DOTALL)
        exec(examples[1], {"WorkforceSummary": self.Summary})  # noqa: S102
