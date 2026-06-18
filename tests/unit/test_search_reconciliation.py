from __future__ import annotations

from typing import ClassVar

from django.test import TestCase

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import FieldConfig, IndexConfig
from general_manager.search.models import (
    SEARCH_INDEX_DIRTY_REASON_INITIALIZATION,
    SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED,
    SearchIndexState,
)
from general_manager.search.reconciliation import (
    build_search_schema_fingerprint,
    ensure_search_index_states,
    iter_search_index_targets,
    manager_import_path,
)
from tests.utils.simple_manager_interface import BaseTestInterface


class ReconcileProjectInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

    @classmethod
    def get_attribute_types(cls):
        return {"name": {"type": str}, "status": {"type": str}}

    @classmethod
    def get_attributes(cls):
        return {
            "name": lambda _interface: "Alpha",
            "status": lambda _interface: "public",
        }


class ReconcileProject(GeneralManager):
    Interface = ReconcileProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(
                name="global",
                fields=[FieldConfig("name", boost=2.0)],
                filters=["status"],
                sorts=["name"],
            )
        ]
        type_label = "ProjectDoc"
        update_strategy = "inline"


class SearchReconciliationDiscoveryTests(TestCase):
    def setUp(self) -> None:
        self._original_all_classes = list(GeneralManagerMeta.all_classes)
        GeneralManagerMeta.all_classes = [ReconcileProject]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [ReconcileProject], [ReconcileProject]
        )

    def tearDown(self) -> None:
        GeneralManagerMeta.all_classes = self._original_all_classes
        super().tearDown()

    def test_manager_import_path_is_stable(self) -> None:
        assert manager_import_path(ReconcileProject).endswith(".ReconcileProject")

    def test_iter_search_index_targets_includes_fingerprint(self) -> None:
        targets = list(iter_search_index_targets())

        assert len(targets) == 1
        target = targets[0]
        assert target.manager_class is ReconcileProject
        assert target.manager_path == manager_import_path(ReconcileProject)
        assert target.index_name == "global"
        assert len(target.schema_fingerprint) == 64

    def test_fingerprint_changes_when_index_config_changes(self) -> None:
        original = build_search_schema_fingerprint(
            ReconcileProject,
            ReconcileProject.SearchConfig.indexes[0],
        )
        changed_index = IndexConfig(
            name="global",
            fields=["name", "status"],
            filters=["status"],
        )

        changed = build_search_schema_fingerprint(ReconcileProject, changed_index)

        assert original != changed

    def test_ensure_states_marks_missing_targets_dirty_for_initialization(self) -> None:
        result = ensure_search_index_states()

        assert result.created == 1
        state = SearchIndexState.objects.get()
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_INITIALIZATION
        assert state.dirty_since is not None

    def test_ensure_states_marks_schema_changes_dirty(self) -> None:
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.schema_fingerprint = "outdated"
        state.dirty_since = None
        state.dirty_reason = ""
        state.save(
            update_fields=[
                "schema_fingerprint",
                "dirty_since",
                "dirty_reason",
                "updated_at",
            ]
        )

        result = ensure_search_index_states()
        state.refresh_from_db()

        assert result.updated == 1
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED
        assert state.dirty_since is not None
        assert state.schema_fingerprint != "outdated"
