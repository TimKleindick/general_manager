from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from general_manager.interface.capabilities.read_only.management import (
    ReadOnlyManagementCapability,
)

from core.managers import (
    Currency,
    CustomerVolume,
    CustomerVolumeCurvePoint,
    Derivative,
    DerivativeType,
    Project,
    ProjectPhaseType,
    ProjectTeam,
    ProjectType,
    ProjectUserRole,
    User,
)


TEST_PASSWORD = "test" + "-pass-123"


class ProjectManagementFactoryTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._catalogs_synced = False

    def setUp(self) -> None:
        super().setUp()
        self._sync_catalogs()

    def _sync_catalogs(self) -> None:
        if self.__class__._catalogs_synced:
            return
        capability = ReadOnlyManagementCapability()
        capability.sync_data(ProjectUserRole.Interface)
        capability.sync_data(ProjectPhaseType.Interface)
        capability.sync_data(ProjectType.Interface)
        capability.sync_data(Currency.Interface)
        capability.sync_data(DerivativeType.Interface)
        self.__class__._catalogs_synced = True

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
        project = Project.Factory.create()
        derivative = Derivative.Factory.create(project=project)
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
        project = Project.Factory.create()
        derivative = Derivative.Factory.create(project=project)
        customer_volume = CustomerVolume.Factory.create(derivative=derivative)

        points = CustomerVolumeCurvePoint.Factory.create(
            customer_volume=customer_volume,
            datapoints=6,
        )

        self.assertIsInstance(points, list)
        self.assertEqual(len(points), 6)
        self.assertEqual(points[0].volume_date, customer_volume.sop)
        self.assertEqual(points[-1].volume_date, customer_volume.eop)
