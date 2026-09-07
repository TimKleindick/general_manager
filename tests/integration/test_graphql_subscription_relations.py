import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

import graphene
from django.db.models import CASCADE, CharField, ForeignKey

from general_manager.api.graphql import GraphQL, _join_subscription_groups
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLSubscriptionRelations(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class SubscriptionStatus(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class SubscriptionProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                status = ForeignKey(
                    "general_manager.SubscriptionStatus",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                )

        cls.Status = SubscriptionStatus
        cls.Project = SubscriptionProject
        cls.general_manager_classes = [SubscriptionStatus, SubscriptionProject]
        super().setUpClass()

    def test_instance_subscription_resolves_uncached_relation(self) -> None:
        self._check_relation_subscription(class_wide=False)

    def test_class_subscription_resolves_uncached_relation(self) -> None:
        self._check_relation_subscription(class_wide=True)

    def _check_relation_subscription(self, *, class_wide: bool) -> None:
        status = self.Status.create(name="Open", ignore_permission=True)
        project = self.Project.create(
            name="Demo", status=status, ignore_permission=True
        )
        schema = graphene.Schema(
            query=GraphQL._query_class, subscription=GraphQL._subscription_class
        )
        field = (
            "onSubscriptionProjectClassChange"
            if class_wide
            else "onSubscriptionProjectChange"
        )
        arguments = "" if class_wide else f'(id: "{project.id}")'
        query = f"subscription {{ {field}{arguments} {{ action item {{ name status {{ name }} }} }} }}"

        async def run_subscription() -> None:
            joined = asyncio.Event()

            async def join_groups(*args: object, **kwargs: object) -> object:
                result = await _join_subscription_groups(*args, **kwargs)
                joined.set()
                return result

            with patch(
                "general_manager.api.graphql._join_subscription_groups",
                side_effect=join_groups,
            ):
                stream = await schema.subscribe(
                    query, context_value=SimpleNamespace(user=None)
                )
                self.assertFalse(
                    hasattr(stream, "errors"), getattr(stream, "errors", None)
                )
                pending = None
                try:
                    if not class_wide:
                        snapshot = await asyncio.wait_for(anext(stream), timeout=5)
                        self.assertIsNone(snapshot.errors)
                        self.assertEqual(snapshot.data[field]["action"], "snapshot")
                        self.assertEqual(
                            snapshot.data[field]["item"]["status"], {"name": "Open"}
                        )
                    for relation in (status, None):
                        pending = asyncio.create_task(anext(stream))
                        await asyncio.wait_for(joined.wait(), timeout=5)
                        await asyncio.to_thread(
                            project.update,
                            name="Updated",
                            status=relation,
                            ignore_permission=True,
                        )
                        event = await asyncio.wait_for(pending, timeout=5)
                        self.assertIsNone(event.errors)
                        self.assertEqual(event.data[field]["action"], "update")
                        self.assertEqual(
                            event.data[field]["item"],
                            {
                                "name": "Updated",
                                "status": {"name": "Open"} if relation else None,
                            },
                        )
                finally:
                    if pending is not None and not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    await stream.aclose()

        with patch.dict(os.environ):
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
            asyncio.run(run_subscription())
