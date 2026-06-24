from __future__ import annotations

from django.test import TestCase

from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import (
    InvalidPermissionDataError as PublicInvalidPermissionDataError,
)
from general_manager.permission import (
    PermissionDataManager as PublicPermissionDataManager,
)
from general_manager.permission.permission_data_manager import (
    InvalidPermissionDataError,
    PermissionDataManager,
)


class PermissionDataManagerTests(TestCase):
    def test_dict_payload_exposes_values_and_missing_keys_as_none(self) -> None:
        manager = PermissionDataManager({"status": "draft", "count": 3})

        self.assertEqual(manager.status, "draft")
        self.assertEqual(manager.count, 3)
        self.assertIsNone(manager.missing)
        self.assertEqual(manager.permission_data, {"status": "draft", "count": 3})
        self.assertIsNone(manager.manager)

    def test_dict_payload_keeps_associated_manager_class(self) -> None:
        permission_data = PermissionDataManager({"status": "draft"}, GeneralManager)

        self.assertIs(permission_data.manager, GeneralManager)

    def test_wrapper_properties_take_precedence_over_payload_keys(self) -> None:
        payload = {"manager": "payload manager", "permission_data": "payload data"}

        permission_data = PermissionDataManager(payload, GeneralManager)

        self.assertIs(permission_data.manager, GeneralManager)
        self.assertIs(permission_data.permission_data, payload)
        self.assertEqual(permission_data.permission_data["manager"], "payload manager")

    def test_manager_instance_payload_delegates_attribute_access(self) -> None:
        instance = GeneralManager.__new__(GeneralManager)
        instance.status = "active"

        permission_data = PermissionDataManager(instance)

        self.assertEqual(permission_data.status, "active")
        self.assertIs(permission_data.permission_data, instance)
        self.assertIs(permission_data.manager, GeneralManager)

        def read_missing_attribute() -> object:
            return permission_data.missing

        with self.assertRaises(AttributeError):
            read_missing_attribute()

    def test_for_update_merges_old_state_with_new_values(self) -> None:
        class UpdateManager(GeneralManager):
            def __iter__(self):
                yield from {
                    "status": "draft",
                    "owner": "old",
                    "metadata": {"old": True},
                }.items()

        instance = UpdateManager.__new__(UpdateManager)

        permission_data = PermissionDataManager.for_update(
            instance,
            {"status": "published", "metadata": {"new": True}},
        )

        self.assertEqual(permission_data.status, "published")
        self.assertEqual(permission_data.owner, "old")
        self.assertEqual(permission_data.metadata, {"new": True})
        self.assertIs(permission_data.manager, UpdateManager)
        self.assertEqual(
            permission_data.permission_data,
            {"status": "published", "owner": "old", "metadata": {"new": True}},
        )

    def test_invalid_payload_raises_public_error(self) -> None:
        with self.assertRaises(InvalidPermissionDataError) as error_context:
            PermissionDataManager(["not", "a", "payload"])  # type: ignore[arg-type]

        self.assertEqual(
            str(error_context.exception),
            "permission_data must be either a dict or an instance of GeneralManager.",
        )

    def test_permission_data_manager_public_exports(self) -> None:
        self.assertIs(PublicPermissionDataManager, PermissionDataManager)
        self.assertIs(PublicInvalidPermissionDataError, InvalidPermissionDataError)
