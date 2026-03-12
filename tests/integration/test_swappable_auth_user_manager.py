# type: ignore

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from django.test import SimpleTestCase


class SwappableAuthUserManagerIntegrationTest(SimpleTestCase):
    def test_swappable_auth_user_manager_wrapper_behaves_correctly(self) -> None:
        script = textwrap.dedent(
            """
            import django
            import sys

            django.setup()

            from django.contrib.auth import get_user_model
            from django.contrib.auth.models import Group, Permission
            from django.contrib.contenttypes.models import ContentType
            from django.db import connection
            from django.utils.crypto import get_random_string

            auth_model = get_user_model()
            managers_module = sys.modules["tests.custom_user_app.managers"]
            User = managers_module.User
            Ticket = managers_module.Ticket
            ReviewAssignment = managers_module.ReviewAssignment
            ticket_model = Ticket.Interface._model
            review_assignment_model = ReviewAssignment.Interface._model
            user_attribute_types = User.Interface.get_attribute_types()

            assert auth_model._general_manager_class is User
            assert Ticket.Interface.get_field_type("owner") is User
            assert user_attribute_types["requested_review_assignments_list"]["type"] is ReviewAssignment
            assert user_attribute_types["received_review_assignments_list"]["type"] is ReviewAssignment

            models_to_create = [
                ContentType,
                Permission,
                Group,
                auth_model,
                auth_model.history.model,
                ticket_model,
                ticket_model.history.model,
                review_assignment_model,
                review_assignment_model.history.model,
            ]

            with connection.schema_editor() as editor:
                for model in models_to_create:
                    editor.create_model(model)

            manager_user = User.create(
                ignore_permission=True,
                username="pilot",
                email="pilot@example.com",
                password=get_random_string(16),
            )
            factory_user = User.Factory.create(username="factory-user")
            ticket = Ticket.create(
                ignore_permission=True,
                title="Inspect hyperdrive",
                owner=manager_user,
            )
            assignment = ReviewAssignment.create(
                ignore_permission=True,
                summary="Review docking protocol",
                requester=manager_user,
                reviewer=factory_user,
            )

            from tests.custom_user_app.managers import ReviewAssignment as ImportedReviewAssignment, Ticket as ImportedTicket, User as ImportedManagerUser

            assert ImportedManagerUser is User
            assert ImportedTicket is Ticket
            assert ImportedReviewAssignment is ReviewAssignment
            assert ImportedManagerUser is not auth_model
            assert auth_model._general_manager_class is User
            assert manager_user.username == "pilot"
            assert isinstance(factory_user, User)
            assert factory_user.email.endswith("@example.com")
            assert isinstance(ticket.owner, User)
            assert ticket.owner.id == manager_user.id
            assert assignment.requester.id == manager_user.id
            assert assignment.reviewer.id == factory_user.id
            assert list(manager_user.requested_review_assignments_list.all())[0].id == assignment.id
            assert list(factory_user.received_review_assignments_list.all())[0].id == assignment.id
            assert Ticket.Interface.get_field_type("owner") is User
            """
        )
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tests.swappable_user_settings",
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout or "swappable auth check failed")
