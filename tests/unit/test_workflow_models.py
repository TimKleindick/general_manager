"""Tests for workflow persistence model metadata."""

from __future__ import annotations

import importlib
from typing import cast

from django.test import SimpleTestCase

from general_manager.workflow.engine import (
    ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES as RUNTIME_ACTIVE_PLUS_COMPLETED_STATES,
)
from general_manager.workflow.models import (
    WorkflowDeliveryAttempt,
    WorkflowEventRecord,
    WorkflowExecutionRecord,
    WorkflowOutbox,
)


class WorkflowModelIndexTests(SimpleTestCase):
    """Verify workflow indexes stay aligned with checked-in migrations."""

    def test_workflow_index_names_match_migrations(self) -> None:
        expected = {
            WorkflowEventRecord: {
                ("event_type",): "general_man_event_t_55e4f2_idx",
                ("event_name",): "general_man_event_n_33eb24_idx",
                ("created_at",): "general_man_created_5b1ca2_idx",
            },
            WorkflowOutbox: {
                ("status", "available_at"): "general_man_status_180bed_idx",
                ("status", "claimed_at"): "workflow_ou_status__8b7f7b_idx",
                ("status", "available_at", "id"): "workflow_ou_status__a5f7dc_idx",
                ("claim_token",): "general_man_claim_t_78fd22_idx",
                ("created_at",): "general_man_created_073f4b_idx",
            },
            WorkflowExecutionRecord: {
                ("workflow_id", "state"): "general_man_workflo_a2876f_idx",
                ("correlation_id", "workflow_id"): "general_man_correla_b2be1f_idx",
                ("created_at",): "general_man_created_4dbaac_idx",
            },
            WorkflowDeliveryAttempt: {
                ("status", "updated_at"): "general_man_status_5f4aa1_idx",
                ("handler_registration_id",): "general_man_handler_f8368f_idx",
            },
        }

        for model, expected_indexes in expected.items():
            with self.subTest(model=model.__name__):
                actual = {
                    tuple(index.fields): index.name for index in model._meta.indexes
                }
                self.assertEqual(actual, expected_indexes)


class WorkflowMigrationConstraintTests(SimpleTestCase):
    """Verify historical workflow constraint migrations stay deterministic."""

    def test_correlation_constraint_states_are_frozen_in_migration(self) -> None:
        migration_module = importlib.import_module(
            "general_manager.migrations."
            "0003_workflow_execution_correlation_constraint"
        )
        migration_states = cast(
            "tuple[str, str, str, str]",
            migration_module.ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES,
        )

        self.assertEqual(
            migration_states,
            ("pending", "running", "waiting", "completed"),
        )
        self.assertIsNot(migration_states, RUNTIME_ACTIVE_PLUS_COMPLETED_STATES)
