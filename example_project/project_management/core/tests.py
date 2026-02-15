from __future__ import annotations

import json
from importlib import import_module

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from general_manager.interface.capabilities.read_only.management import (
    ReadOnlyManagementCapability,
)
from general_manager.permission.permission_checks import permission_functions

from core.managers.catalogs import (
    Currency,
    DerivativeType,
    ProjectPhaseType,
    ProjectType,
    ProjectUserRole,
)
from core.managers.identity import User
from core.managers.master_data import AccountNumber, Customer, Plant
from core.managers.project_domain import Derivative, Project, ProjectTeam
from core.managers.volume_domain import CustomerVolume, CustomerVolumeCurvePoint
from general_manager.search.config import IndexConfig


TEST_PASSWORD = "test" + "-pass-123"


class ProjectManagementFactoryTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._sync_catalogs()

    def _sync_catalogs(self) -> None:
        capability = ReadOnlyManagementCapability()
        capability.sync_data(ProjectUserRole.Interface)
        capability.sync_data(ProjectPhaseType.Interface)
        capability.sync_data(ProjectType.Interface)
        capability.sync_data(Currency.Interface)
        capability.sync_data(DerivativeType.Interface)

    def test_user_manager_uses_auth_user_model_and_login(self) -> None:
        auth_model = get_user_model()
        self.assertIs(User.Interface._model, auth_model)

        user = User.Factory.create(is_active=True)
        self.assertTrue(auth_model.objects.filter(id=user.id).exists())

        client = Client()
        did_login = client.login(username=user.username, password=TEST_PASSWORD)
        self.assertTrue(did_login)

    def test_factories_create_realistic_related_project_data(self) -> None:
        user = User.Factory.create(is_active=True)
        customer = Customer.Factory.create(key_account=user)
        plant = Plant.Factory.create()
        project = Project.Factory.create(customer=customer)
        derivative = Derivative.Factory.create(project=project, _plant=plant)
        customer_volume = CustomerVolume.Factory.create(derivative=derivative)
        points = CustomerVolumeCurvePoint.Factory.create(
            customer_volume=customer_volume
        )
        team = ProjectTeam.Factory.create(project=project, responsible_user=user)

        self.assertGreaterEqual(customer_volume.eop, customer_volume.sop)
        self.assertIsInstance(points, list)
        self.assertGreaterEqual(len(points), 2)
        self.assertEqual(points[0].customer_volume.id, customer_volume.id)
        self.assertEqual(team.project.id, project.id)
        self.assertEqual(team.responsible_user.id, user.id)

    def test_curve_point_factory_can_generate_multiple_datapoints(self) -> None:
        user = User.Factory.create(is_active=True)
        customer = Customer.Factory.create(key_account=user)
        plant = Plant.Factory.create()
        project = Project.Factory.create(customer=customer)
        derivative = Derivative.Factory.create(project=project, _plant=plant)
        customer_volume = CustomerVolume.Factory.create(derivative=derivative)

        points = CustomerVolumeCurvePoint.Factory.create(
            customer_volume=customer_volume,
            datapoints=6,
        )

        self.assertIsInstance(points, list)
        expected_points = min(6, customer_volume.eop.year - customer_volume.sop.year + 1)
        self.assertEqual(len(points), expected_points)
        self.assertTrue(
            all(point.volume_date.month == 1 and point.volume_date.day == 1 for point in points)
        )
        self.assertEqual(points[0].volume_date.year, customer_volume.sop.year)
        self.assertEqual(points[-1].volume_date.year, customer_volume.eop.year)

    def test_managers_domain_modules_are_importable(self) -> None:
        module_names = [
            "core.managers.constants",
            "core.managers.exceptions",
            "core.managers.ids",
            "core.managers.permissions",
            "core.managers.catalogs",
            "core.managers.identity",
            "core.managers.master_data",
            "core.managers.project_domain",
            "core.managers.volume_domain",
        ]
        for module_name in module_names:
            self.assertIsNotNone(import_module(module_name))

    def test_managers_package_exports_expected_symbols_and_permissions(self) -> None:
        managers_module = import_module("core.managers")
        exported_names = set(getattr(managers_module, "__all__", []))
        expected_exports = {
            "AccountNumber",
            "Currency",
            "Customer",
            "CustomerVolume",
            "CustomerVolumeCurvePoint",
            "Derivative",
            "DerivativeType",
            "Plant",
            "Project",
            "ProjectPhaseType",
            "ProjectTeam",
            "ProjectType",
            "ProjectUserRole",
            "ProjectVolumeCurve",
            "User",
        }
        self.assertEqual(exported_names, expected_exports)
        for permission_name in (
            "isProjectRoleAny",
            "isKeyAccountOfProjectCustomer",
            "isLegacyProjectCreateAllowed",
            "canUpdateProbabilityOfNomination",
        ):
            self.assertIn(permission_name, permission_functions)

    def test_project_search_config_contains_global_project_manager_and_derivative_fields(self) -> None:
        config = getattr(Project, "SearchConfig", None)
        self.assertIsNotNone(config)
        indexes = getattr(config, "indexes", [])
        self.assertTrue(indexes)
        global_index = next(
            (
                index
                for index in indexes
                if isinstance(index, IndexConfig) and index.name == "global"
            ),
            None,
        )
        self.assertIsNotNone(global_index)
        fields = [
            entry.name if hasattr(entry, "name") else entry
            for entry in global_index.fields  # type: ignore[union-attr]
        ]
        self.assertIn("name", fields)
        self.assertIn("projectteam_list__responsible_user__full_name", fields)
        self.assertIn("derivative_list__name", fields)


class DashboardRoutingTests(TestCase):
    def test_root_redirects_to_dashboard(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, "dashboard/")

    def test_dashboard_route_serves_spa_without_project_id(self) -> None:
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Program Dashboard")
        self.assertContains(response, 'id="app-root"')
        self.assertContains(response, "core/dashboard_app/assets/app.css")
        self.assertContains(response, "core/dashboard_app/assets/app.js")

    def test_dashboard_route_is_available(self) -> None:
        response = self.client.get(f"{reverse('dashboard')}?projectId=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Program Dashboard")
        self.assertContains(response, "core/dashboard_app/assets/app.css")
        self.assertContains(response, "core/dashboard_app/assets/app.js")

    def test_projects_route_remains_available(self) -> None:
        response = self.client.get(reverse("project-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Program Dashboard")
        self.assertContains(response, 'id="app-root"')
        self.assertContains(response, "core/dashboard_app/assets/app.css")
        self.assertContains(response, "core/dashboard_app/assets/app.js")


class ProjectManagementGraphQLMutationContractTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._sync_catalogs()
        user_model = get_user_model()
        self.password = TEST_PASSWORD
        self.user = user_model.objects.create_user(
            username="seed_user_1",
            password=self.password,
            email="seed_user_1@example.local",
        )
        self.other_user = user_model.objects.create_user(
            username="seed_user_2",
            password=self.password,
            email="seed_user_2@example.local",
        )
        self.client = Client()
        did_login = self.client.login(username=self.user.username, password=self.password)
        self.assertTrue(did_login)

    def _sync_catalogs(self) -> None:
        capability = ReadOnlyManagementCapability()
        capability.sync_data(ProjectUserRole.Interface)
        capability.sync_data(ProjectPhaseType.Interface)
        capability.sync_data(ProjectType.Interface)
        capability.sync_data(Currency.Interface)
        capability.sync_data(DerivativeType.Interface)

    def _graphql(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        response = self.client.post(
            "/graphql/",
            data=json.dumps({"query": query, "variables": variables}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _create_project_via_graphql(self) -> int:
        customer = Customer.Factory.create()
        phase_type = ProjectPhaseType.filter(id=1).first()
        project_type = ProjectType.filter(id=1).first()
        currency = Currency.filter(id=1).first()
        self.assertIsNotNone(phase_type)
        self.assertIsNotNone(project_type)
        self.assertIsNotNone(currency)

        create_project_mutation = """
        mutation CreateProject(
          $name: String!,
          $customer: ID!,
          $projectPhaseType: ID!,
          $projectType: ID,
          $currency: ID!
        ) {
          createProject(
            name: $name,
            customer: $customer,
            projectPhaseType: $projectPhaseType,
            projectType: $projectType,
            currency: $currency
          ) {
            success
            Project { id }
          }
        }
        """

        payload = self._graphql(
            create_project_mutation,
            {
                "name": "Mutation Contract Project",
                "customer": str(customer.id),
                "projectPhaseType": str(phase_type.id),
                "projectType": str(project_type.id),
                "currency": str(currency.id),
            },
        )
        self.assertNotIn("errors", payload)
        data = payload["data"]["createProject"]
        self.assertTrue(data["success"])
        project_id = int(data["Project"]["id"])
        if not ProjectTeam.filter(
            project_id=project_id,
            project_user_role_id=1,
            responsible_user_id=self.user.id,
        ).first():
            ProjectTeam.create(
                ignore_permission=True,
                creator_id=self.user.id,
                project_id=project_id,
                project_user_role_id=1,
                responsible_user_id=self.user.id,
                active=True,
            )
        return project_id

    def _create_derivative_via_graphql(self, project_id: int) -> int:
        derivative_type = DerivativeType.filter(id=1).first()
        plant = Plant.Factory.create()
        self.assertIsNotNone(derivative_type)

        create_derivative_mutation = """
        mutation CreateDerivative(
          $project: ID!,
          $name: String!,
          $derivativeType: ID!,
          $Plant: ID!,
          $piecesPerCarSet: Int
        ) {
          createDerivative(
            project: $project,
            name: $name,
            derivativeType: $derivativeType,
            Plant: $Plant,
            piecesPerCarSet: $piecesPerCarSet
          ) {
            success
            Derivative { id }
          }
        }
        """
        payload = self._graphql(
            create_derivative_mutation,
            {
                "project": str(project_id),
                "name": "Mutation Contract Derivative",
                "derivativeType": str(derivative_type.id),
                "Plant": str(plant.id),
                "piecesPerCarSet": 2,
            },
        )
        self.assertNotIn("errors", payload)
        self.assertTrue(payload["data"]["createDerivative"]["success"])
        return int(payload["data"]["createDerivative"]["Derivative"]["id"])

    def _create_customer_volume_via_graphql(self, derivative_id: int) -> int:
        create_volume_mutation = """
        mutation CreateVolume($derivative: ID!, $sop: Date!, $eop: Date!) {
          createCustomerVolume(derivative: $derivative, sop: $sop, eop: $eop) {
            success
            CustomerVolume { id }
          }
        }
        """
        payload = self._graphql(
            create_volume_mutation,
            {
                "derivative": str(derivative_id),
                "sop": "2026-01-01",
                "eop": "2028-01-01",
            },
        )
        self.assertNotIn("errors", payload)
        self.assertTrue(payload["data"]["createCustomerVolume"]["success"])
        return int(payload["data"]["createCustomerVolume"]["CustomerVolume"]["id"])

    def test_create_project_with_invest_number_list(self) -> None:
        customer = Customer.Factory.create()
        phase_type = ProjectPhaseType.filter(id=1).first()
        project_type = ProjectType.filter(id=1).first()
        currency = Currency.filter(id=1).first()
        self.assertIsNotNone(phase_type)
        self.assertIsNotNone(project_type)
        self.assertIsNotNone(currency)
        invest_a = AccountNumber.Factory.create(is_project_account=False)
        invest_b = AccountNumber.Factory.create(is_project_account=False)

        create_project_mutation = """
        mutation CreateProject(
          $name: String!,
          $customer: ID!,
          $projectPhaseType: ID!,
          $projectType: ID,
          $currency: ID!,
          $investNumberList: [ID]
        ) {
          createProject(
            name: $name,
            customer: $customer,
            projectPhaseType: $projectPhaseType,
            projectType: $projectType,
            currency: $currency,
            investNumberList: $investNumberList
          ) {
            success
            Project { id }
          }
        }
        """

        payload = self._graphql(
            create_project_mutation,
            {
                "name": "Mutation Contract Project With Invest",
                "customer": str(customer.id),
                "projectPhaseType": str(phase_type.id),
                "projectType": str(project_type.id),
                "currency": str(currency.id),
                "investNumberList": [str(invest_a.id), str(invest_b.id)],
            },
        )
        self.assertNotIn("errors", payload)
        self.assertTrue(payload["data"]["createProject"]["success"])

    def test_update_customer_with_sales_responsible_list(self) -> None:
        create_customer_mutation = """
        mutation CreateCustomer($companyName: String!, $groupName: String!) {
          createCustomer(companyName: $companyName, groupName: $groupName) {
            success
            Customer { id }
          }
        }
        """
        create_payload = self._graphql(
            create_customer_mutation,
            {
                "companyName": "Mutation Contract Customer",
                "groupName": "Mutation Contract Group",
            },
        )
        self.assertNotIn("errors", create_payload)
        customer_id = int(create_payload["data"]["createCustomer"]["Customer"]["id"])

        update_customer_mutation = """
        mutation UpdateCustomer(
          $id: Int!,
          $companyName: String!,
          $groupName: String!,
          $salesResponsibleList: [ID]
        ) {
          updateCustomer(
            id: $id,
            companyName: $companyName,
            groupName: $groupName,
            salesResponsibleList: $salesResponsibleList
          ) {
            success
            Customer { id }
          }
        }
        """
        payload = self._graphql(
            update_customer_mutation,
            {
                "id": customer_id,
                "companyName": "Mutation Contract Customer Updated",
                "groupName": "Mutation Contract Group",
                "salesResponsibleList": [str(self.other_user.id)],
            },
        )
        self.assertNotIn("errors", payload)
        self.assertTrue(payload["data"]["updateCustomer"]["success"])

    def test_create_derivative_with_plant_id(self) -> None:
        project_id = self._create_project_via_graphql()
        derivative_id = self._create_derivative_via_graphql(project_id)
        self.assertGreater(derivative_id, 0)

    def test_create_curve_point_rejects_non_january_first_date(self) -> None:
        project_id = self._create_project_via_graphql()
        derivative_id = self._create_derivative_via_graphql(project_id)
        volume_id = self._create_customer_volume_via_graphql(derivative_id)

        create_curve_mutation = """
        mutation CreateCurvePoint($customerVolume: ID!, $volumeDate: Date!, $volume: Int!) {
          createCustomerVolumeCurvePoint(
            customerVolume: $customerVolume,
            volumeDate: $volumeDate,
            volume: $volume
          ) {
            success
            CustomerVolumeCurvePoint { id }
          }
        }
        """
        payload = self._graphql(
            create_curve_mutation,
            {
                "customerVolume": str(volume_id),
                "volumeDate": "2027-03-15",
                "volume": 1234,
            },
        )
        self.assertIn("errors", payload)
        self.assertIn("January 1st", payload["errors"][0]["message"])

    def test_update_curve_point_rejects_non_january_first_date(self) -> None:
        project_id = self._create_project_via_graphql()
        derivative_id = self._create_derivative_via_graphql(project_id)
        volume_id = self._create_customer_volume_via_graphql(derivative_id)

        create_curve_mutation = """
        mutation CreateCurvePoint($customerVolume: ID!, $volumeDate: Date!, $volume: Int!) {
          createCustomerVolumeCurvePoint(
            customerVolume: $customerVolume,
            volumeDate: $volumeDate,
            volume: $volume
          ) {
            success
            CustomerVolumeCurvePoint { id }
          }
        }
        """
        create_payload = self._graphql(
            create_curve_mutation,
            {
                "customerVolume": str(volume_id),
                "volumeDate": "2027-01-01",
                "volume": 1000,
            },
        )
        self.assertNotIn("errors", create_payload)
        curve_id = int(
            create_payload["data"]["createCustomerVolumeCurvePoint"][
                "CustomerVolumeCurvePoint"
            ]["id"]
        )

        update_curve_mutation = """
        mutation UpdateCurvePoint($id: Int!, $customerVolume: ID!, $volumeDate: Date!, $volume: Int!) {
          updateCustomerVolumeCurvePoint(
            id: $id,
            customerVolume: $customerVolume,
            volumeDate: $volumeDate,
            volume: $volume
          ) {
            success
            CustomerVolumeCurvePoint { id }
          }
        }
        """
        update_payload = self._graphql(
            update_curve_mutation,
            {
                "id": curve_id,
                "customerVolume": str(volume_id),
                "volumeDate": "2027-06-01",
                "volume": 1111,
            },
        )
        self.assertIn("errors", update_payload)
        self.assertIn("January 1st", update_payload["errors"][0]["message"])
