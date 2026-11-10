"""Generated relation inputs reach direct registered create permissions."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import models

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.permission_checks import (
    permission_functions,
    register_permission,
)
from general_manager.permission.permission_data_manager import PermissionDataManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLRelationPermissionInputs(GeneralManagerTransactionTestCase):
    observations: ClassVar[list[tuple[object, object, object, object]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        """Register a direct customer permission and expose a declared customer FK."""
        super().setUpClass()
        cls.enterClassContext(patch.dict(permission_functions))

        class RelationPermissionCustomer(GeneralManager):
            class Interface(DatabaseInterface):
                allowed_user_id = models.IntegerField()

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        @register_permission("relationPermissionCustomerCreate")
        def customer_create_allowed(instance, user, config) -> bool:
            """Authorize the request user through the customer's actual stored owner."""
            customer = instance.customer
            customer_id = instance.customer_id
            cls.observations.append((instance, customer, customer_id, user))
            return (
                isinstance(customer, RelationPermissionCustomer)
                and customer.allowed_user_id == user.id
            )

        class RelationPermissionOrder(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=80)
                customer = models.ForeignKey(
                    "general_manager.RelationPermissionCustomer",
                    on_delete=models.CASCADE,
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = [
                    "isAuthenticated&relationPermissionCustomerCreate"
                ]

        cls.Customer = RelationPermissionCustomer
        cls.Order = RelationPermissionOrder
        cls.general_manager_classes = [
            RelationPermissionCustomer,
            RelationPermissionOrder,
        ]

    def setUp(self) -> None:
        """Authenticate a non-admin and distinguish customers owned by different users."""
        super().setUp()
        self.user = User.objects.create_user(username="relation-permission-user")
        other_user = User.objects.create_user(username="other-customer-user")
        self.client.force_login(self.user)
        self.allowed_customer = self.Customer.create(
            ignore_permission=True, allowed_user_id=self.user.id
        )
        self.denied_customer = self.Customer.create(
            ignore_permission=True, allowed_user_id=other_user.id
        )
        self.observations.clear()

    def _create_through_graphql(self, customer: GeneralManager, name: str):
        """Exercise the real generated resolver and observe its ID-only manager input."""
        with patch.object(self.Order, "create", wraps=self.Order.create) as create:
            response = self.query(
                """mutation($customer: ID!, $name: String!) {
                    createRelationPermissionOrder(customer: $customer, name: $name) {
                        success RelationPermissionOrder { id name }
                    }
                }""",
                variables={"customer": str(customer.id), "name": name},
            )
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["customer_id"], str(customer.id))
        self.assertNotIn("customer", kwargs)
        self.assertNotIn("ignore_permission", kwargs)
        self.assertEqual(kwargs["creator_id"], self.user.id)
        return response

    def _assert_direct_customer_check(self, expected_customer: GeneralManager) -> None:
        """Require the registered permission to observe both aliases and the real user."""
        self.assertTrue(self.observations)
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        for candidate, customer, customer_id, user in self.observations:
            self.assertIsInstance(candidate, PermissionDataManager)
            self.assertIs(candidate.manager, self.Order)
            self.assertIsInstance(customer, self.Customer)
            self.assertEqual(customer, expected_customer)
            self.assertEqual(customer.identification, {"id": expected_customer.id})
            self.assertEqual(customer_id, expected_customer.id)
            self.assertEqual(
                customer.allowed_user_id, expected_customer.allowed_user_id
            )
            self.assertIsInstance(user, User)
            self.assertEqual(user, self.user)
            self.assertTrue(user.is_authenticated)
            self.assertFalse(user.is_staff)
            self.assertFalse(user.is_superuser)

    def test_generated_create_allows_customer_owned_by_request_user(self) -> None:
        """Authorize ID-only GraphQL input through a direct, non-delegated permission."""
        response = self._create_through_graphql(self.allowed_customer, "Allowed order")
        self.assertResponseNoErrors(response)
        result = response.json()["data"]["createRelationPermissionOrder"]
        self.assertTrue(result["success"])
        row = self.Order.Interface._model.objects.get(
            pk=result["RelationPermissionOrder"]["id"]
        )
        self.assertEqual(row.customer_id, self.allowed_customer.id)
        self.assertEqual(row.name, "Allowed order")
        self.assertEqual(self.Order.Interface._model.objects.count(), 1)
        self._assert_direct_customer_check(self.allowed_customer)

    def test_generated_create_denies_customer_owned_by_another_user(self) -> None:
        """Deny the actual selected customer and leave no order persisted."""
        response = self._create_through_graphql(self.denied_customer, "Denied order")
        self.assertEqual(
            response.json()["errors"][0]["extensions"]["code"], "PERMISSION_DENIED"
        )
        self.assertEqual(self.Order.Interface._model.objects.count(), 0)
        self._assert_direct_customer_check(self.denied_customer)

    def test_generated_id_input_enforces_canonical_customer_field_rule(self) -> None:
        """Apply a canonical field restriction to the resolver's ID-only manager input."""
        with patch.object(
            self.Order.Permission, "customer", {"create": ["isAdmin"]}, create=True
        ):
            response = self._create_through_graphql(
                self.allowed_customer, "Field-restricted order"
            )
        self.assertIn("errors", response.json())
        self.assertEqual(
            response.json()["errors"][0]["extensions"]["code"], "PERMISSION_DENIED"
        )
        self.assertEqual(self.Order.Interface._model.objects.count(), 0)

    def test_generated_id_input_requires_both_explicit_alias_field_groups(self) -> None:
        """Do not let an explicit public raw-ID definition bypass the canonical gate."""
        with (
            patch.object(
                self.Order.Permission, "customer", {"create": ["isAdmin"]}, create=True
            ),
            patch.object(
                self.Order.Permission,
                "customer_id",
                {"create": ["public"]},
                create=True,
            ),
        ):
            response = self._create_through_graphql(
                self.allowed_customer, "Conflicting-field order"
            )
        self.assertIn("errors", response.json())
        self.assertEqual(
            response.json()["errors"][0]["extensions"]["code"], "PERMISSION_DENIED"
        )
        self.assertEqual(self.Order.Interface._model.objects.count(), 0)
