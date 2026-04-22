from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from general_manager.permission.graphql_capabilities import (
    CapabilityEvaluationContext,
    object_capability,
)


@dataclass
class DummyManager:
    identification: dict[str, Any]

    def __hash__(self) -> int:
        return hash(self.identification["code"])


class PolicyBackendUnavailable(RuntimeError):
    pass


class GraphQLPermissionCapabilityTests(SimpleTestCase):
    def test_object_capability_is_public_permission_api(self) -> None:
        from general_manager.permission import object_capability as public_helper

        self.assertIs(public_helper, object_capability)

    def test_object_capability_evaluates_once_per_operation_cache_key(self) -> None:
        calls: list[str] = []

        def can_rename(instance: DummyManager, user: Any) -> bool:
            calls.append(instance.identification["code"])
            return True

        declaration = object_capability("canRename", can_rename)
        instance = DummyManager({"code": "ALPHA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        self.assertTrue(context.evaluate(declaration, instance))
        self.assertTrue(context.evaluate(declaration, instance))

        self.assertEqual(calls, ["ALPHA"])

    def test_object_capability_denies_and_caches_evaluator_errors(self) -> None:
        calls = 0

        def broken_evaluator(instance: DummyManager, user: Any) -> bool:
            nonlocal calls
            calls += 1
            raise PolicyBackendUnavailable

        declaration = object_capability("canRename", broken_evaluator)
        instance = DummyManager({"code": "ALPHA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        self.assertFalse(context.evaluate(declaration, instance))
        self.assertFalse(context.evaluate(declaration, instance))

        self.assertEqual(calls, 1)

    def test_object_capability_batch_evaluator_warms_cache(self) -> None:
        calls: list[list[str]] = []

        def can_rename_batch(
            instances: list[DummyManager],
            user: Any,
        ) -> dict[DummyManager, bool]:
            calls.append([instance.identification["code"] for instance in instances])
            return {instance: True for instance in instances}

        declaration = object_capability(
            "canRename",
            lambda _instance, _user: False,
            batch_evaluator=can_rename_batch,
        )
        first = DummyManager({"code": "ALPHA"})
        second = DummyManager({"code": "BETA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        context.warm([declaration], [first, second])

        self.assertTrue(context.evaluate(declaration, first))
        self.assertTrue(context.evaluate(declaration, second))
        self.assertEqual(calls, [["ALPHA", "BETA"]])
