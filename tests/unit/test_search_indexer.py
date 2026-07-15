from __future__ import annotations

from typing import ClassVar
from unittest.mock import Mock, patch

import pytest
from django.test import SimpleTestCase, TestCase

import general_manager.search.indexer as indexer_module
from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.search.backend import SearchDocument
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.search.models import (
    SEARCH_INDEX_DIRTY_REASON_INITIALIZATION,
    SearchIndexState,
)
from general_manager.search.utils import build_document_id
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class ProjectInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {
        1: {"name": "Alpha", "status": "public", "secret": "hidden"},
        2: {"name": "Beta", "status": "private", "secret": "hidden"},
    }

    def get_data(self, search_date=None):
        """
        Return the stored data dictionary for this instance's id.

        Parameters:
            search_date (optional): Ignored and kept for interface compatibility.

        Returns:
            dict: Attribute dictionary for the instance identified by self.identification["id"].
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Return the attribute type descriptors used for indexing and schema generation.

        Returns:
            dict: A mapping from attribute name to a descriptor dict containing a "type" key with the Python type for that attribute (e.g., {"name": {"type": str}}).
        """
        return {
            "name": {"type": str},
            "status": {"type": str},
        }

    @classmethod
    def get_attributes(cls):
        """
        Return a mapping of attribute names to callables that extract those attributes from an interface instance.

        Each callable accepts an interface instance and returns the corresponding attribute value. The mapping includes:
        - "name": returns the project's name
        - "status": returns the project's status

        Returns:
            dict[str, Callable[[object], object]]: Mapping of attribute name to extractor callable.
        """
        return {
            "name": lambda interface: interface.get_data()["name"],
            "status": lambda interface: interface.get_data()["status"],
        }

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket of Project instances filtered by the optional `id__in` keyword.

        Parameters:
            **kwargs: Optional keyword arguments controlling the filter.
                id__in (iterable[int], optional): Iterable of IDs to include; if omitted, all stored IDs are returned.

        Returns:
            SimpleBucket: A bucket of manager instances corresponding to the requested IDs.
        """
        ids = kwargs.get("id__in")
        if ids is None:
            ids = list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class Project(GeneralManager):
    Interface = ProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"], filters=["status"])
        ]

        @staticmethod
        def to_document(instance: "Project") -> dict:
            """
            Convert a Project instance into a dictionary document for indexing.

            Returns:
                dict: A mapping with keys "name", "status", and "secret" extracted from the instance's interface data.
            """
            data = instance._interface.get_data()
            return {
                "name": data["name"],
                "status": data["status"],
                "secret": data["secret"],
            }


class MultiIndexProjectInterface(ProjectInterface):
    pass


class MultiIndexProject(GeneralManager):
    Interface = MultiIndexProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"], filters=["status"]),
            IndexConfig(name="private", fields=["status"], filters=["status"]),
        ]


class StableDocumentProjectInterface(ProjectInterface):
    pass


class StableDocumentProject(GeneralManager):
    Interface = StableDocumentProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"]),
            IndexConfig(name="private", fields=["status"]),
        ]

        @staticmethod
        def document_id(instance: "StableDocumentProject") -> str:
            """Return the stable external document identifier."""
            return f"project-{instance.identification['id']}"


class SearchIndexerTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Initialize general manager classes required by the test suite.

        Registers the Project class with GeneralmanagerConfig so manager and model metadata are prepared before each test.
        """
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])

    def test_indexer_preserves_injected_falsey_backend(self) -> None:
        class FalseyBackend(DevSearchBackend):
            def __bool__(self) -> bool:
                return False

        backend = FalseyBackend()

        indexer = SearchIndexer(backend)

        assert indexer.backend is backend

    def test_indexer_indexes_configured_fields(self) -> None:
        """
        Verify that SearchIndexer indexes only the fields configured for the index and respects search filters.

        Indexes a Project instance into a DevSearchBackend, searches the "global" index for "Alpha" with filter status="public", and asserts that exactly one hit is returned, the hit identifies the instance by {"id": 1}, and the indexed document does not include the "secret" field.
        """
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        instance = Project(id=1)
        indexer.index_instance(instance)

        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 1
        hit = result.hits[0]
        assert hit.identification == {"id": 1}
        assert "secret" not in hit.data

    def test_indexer_delete_instance(self) -> None:
        """
        Verifies that deleting a previously indexed instance removes it from the search index.

        Indexes a Project(id=1), deletes that indexed instance, and asserts that searching the "global" index for "Alpha" with filter status="public" returns no results.
        """
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        instance = Project(id=1)
        indexer.index_instance(instance)
        indexer.delete_instance(instance)

        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 0

    def test_capture_delete_targets_preserves_custom_id_while_instance_is_live(
        self,
    ) -> None:
        """Capture immutable custom document IDs before a manager is deleted."""
        from general_manager.search.indexer import capture_delete_targets

        GeneralmanagerConfig.initialize_general_manager_classes(
            [StableDocumentProject], [StableDocumentProject]
        )

        targets = capture_delete_targets(StableDocumentProject(id=1))

        assert [target.index_name for target in targets] == ["global", "private"]
        assert {target.document_id for target in targets} == {"project-1"}
        assert all(target.manager_class is StableDocumentProject for target in targets)

    def test_delete_documents_uses_captured_targets_without_manager_reconstruction(
        self,
    ) -> None:
        """Delete supplied immutable IDs without reading a deleted manager."""
        from general_manager.search.indexer import SearchDeleteTarget

        GeneralmanagerConfig.initialize_general_manager_classes(
            [StableDocumentProject], [StableDocumentProject]
        )
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)
        indexer.index_instance(StableDocumentProject(id=1))
        manager_path = (
            f"{StableDocumentProject.__module__}.{StableDocumentProject.__name__}"
        )

        indexer.delete_documents(
            (
                SearchDeleteTarget(
                    StableDocumentProject, manager_path, "global", "project-1"
                ),
                SearchDeleteTarget(
                    StableDocumentProject, manager_path, "private", "project-1"
                ),
            )
        )

        assert backend.list_document_ids("global") == set()
        assert backend.list_document_ids("private") == set()

    def test_index_instance_index_writes_only_requested_index(self) -> None:
        """Incremental indexing can target one exact manager/index pair."""
        GeneralmanagerConfig.initialize_general_manager_classes(
            [MultiIndexProject], [MultiIndexProject]
        )
        backend = DevSearchBackend()

        SearchIndexer(backend).index_instance_index(MultiIndexProject(id=1), "private")

        assert backend.list_document_ids("global") == set()
        assert backend.list_document_ids("private") == {
            build_document_id("MultiIndexProject", {"id": 1})
        }

    def test_custom_document_id_remains_one_document_across_update(self) -> None:
        """Upserting updated data preserves the configured stable identity."""
        GeneralmanagerConfig.initialize_general_manager_classes(
            [StableDocumentProject], [StableDocumentProject]
        )
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)
        original = dict(StableDocumentProjectInterface.data_store[1])
        try:
            indexer.index_instance_index(StableDocumentProject(id=1), "global")
            StableDocumentProjectInterface.data_store[1] = {
                **original,
                "name": "Alpha updated",
            }
            indexer.index_instance_index(StableDocumentProject(id=1), "global")
        finally:
            StableDocumentProjectInterface.data_store[1] = original

        assert backend.list_document_ids("global") == {"project-1"}
        assert backend.search("global", "updated").total == 1

    def test_indexer_reindex_manager(self) -> None:
        """Reindex all configured documents for a manager class."""
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        indexer.reindex_manager(Project)
        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 1

    def test_indexer_reindex_manager_index_deletes_stale_same_type_documents(
        self,
    ) -> None:
        """Delete stale same-type documents during manager/index reindexing."""
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)
        backend.ensure_index("global", {})
        stale_project_id = build_document_id("Project", {"id": 999})
        other_type_id = build_document_id("OtherProject", {"id": 1})
        backend.upsert(
            "global",
            [
                SearchDocument(
                    id=stale_project_id,
                    type="Project",
                    identification={"id": 999},
                    index="global",
                    data={"name": "Stale Project", "status": "public"},
                    field_boosts={},
                ),
                SearchDocument(
                    id=other_type_id,
                    type="OtherProject",
                    identification={"id": 1},
                    index="global",
                    data={"name": "Other Project", "status": "public"},
                    field_boosts={},
                ),
            ],
        )

        indexed = indexer.reindex_manager_index(Project, "global")

        assert indexed == 2
        existing_ids = backend.list_document_ids("global")
        assert stale_project_id not in existing_ids
        assert build_document_id("Project", {"id": 1}) in existing_ids
        assert build_document_id("Project", {"id": 2}) in existing_ids
        assert other_type_id in existing_ids


def test_indexer_reindex_manager_index_limits_backend_writes() -> None:
    """Reindex only the requested index for multi-index managers."""
    GeneralmanagerConfig.initialize_general_manager_classes(
        [MultiIndexProject], [MultiIndexProject]
    )
    backend = DevSearchBackend()
    indexer = SearchIndexer(backend)

    indexer.reindex_manager_index(MultiIndexProject, "global")

    assert backend.search("global", "Alpha", filters={"status": "public"}).total == 1
    assert backend.search("private", "public", filters={"status": "public"}).total == 0


class CompositeProjectInterface(ProjectInterface):
    """Non-ORM test interface with a composite public identification."""

    input_fields: ClassVar[dict[str, Input]] = {
        "tenant": Input(str),
        "id": Input(int),
    }
    constructed: ClassVar[list[dict[str, object]]] = []

    def __init__(self, **kwargs: object) -> None:
        type(self).constructed.append(dict(kwargs))
        self.identification = dict(kwargs)

    def get_data(self, search_date=None):
        return {
            "name": f"{self.identification['tenant']}-{self.identification['id']}",
            "status": "public",
        }


class CompositeProject(GeneralManager):
    Interface = CompositeProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"])
        ]


def test_index_manager_index_batch_deduplicates_canonical_identities_first_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical duplicate identities produce one document in first-seen order."""
    GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
    backend = Mock()
    monkeypatch.setattr(indexer_module, "_ensure_index", Mock())

    count = SearchIndexer(backend).index_manager_index_batch(
        Project,
        "global",
        ({"id": 2}, {"id": 1}, {"id": 2}),
    )

    assert count == 2
    documents = backend.upsert.call_args.args[1]
    assert [document.identification for document in documents] == [
        {"id": 2},
        {"id": 1},
    ]


def test_index_manager_index_batch_only_writes_requested_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonempty batch ensures and upserts its exact named index once."""
    GeneralmanagerConfig.initialize_general_manager_classes(
        [MultiIndexProject], [MultiIndexProject]
    )
    backend = Mock()
    ensure = Mock()
    monkeypatch.setattr(indexer_module, "_ensure_index", ensure)

    count = SearchIndexer(backend).index_manager_index_batch(
        MultiIndexProject,
        "private",
        ({"id": 1}, {"id": 2}),
    )

    assert count == 2
    ensure.assert_called_once_with(backend, "private")
    backend.upsert.assert_called_once()
    assert backend.upsert.call_args.args[0] == "private"
    assert {document.index for document in backend.upsert.call_args.args[1]} == {
        "private"
    }


def test_index_manager_index_batch_keeps_shared_index_manager_types_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared physical index batch only serializes the passed manager type."""

    class OtherProjectInterface(ProjectInterface):
        pass

    class OtherProject(GeneralManager):
        Interface = OtherProjectInterface

        class SearchConfig:
            indexes: ClassVar[list[IndexConfig]] = [
                IndexConfig(name="global", fields=["name"])
            ]

    GeneralmanagerConfig.initialize_general_manager_classes(
        [Project, OtherProject], [Project, OtherProject]
    )
    backend = Mock()
    monkeypatch.setattr(indexer_module, "_ensure_index", Mock())

    SearchIndexer(backend).index_manager_index_batch(
        OtherProject, "global", ({"id": 1},)
    )

    documents = backend.upsert.call_args.args[1]
    assert [document.type for document in documents] == ["OtherProject"]
    backend.list_document_ids.assert_not_called()
    backend.delete.assert_not_called()


def test_index_manager_index_batch_bulk_loads_standard_orm_ids_once_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standard ORM ids use one manager filter and restore requested ordering."""
    GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
    backend = Mock()
    filters: list[dict[str, object]] = []

    def filter_projects(**kwargs: object) -> SimpleBucket:
        filters.append(kwargs)
        return SimpleBucket(Project, [Project(id=1), Project(id=2)])

    monkeypatch.setattr(indexer_module, "OrmInterfaceBase", BaseTestInterface)
    monkeypatch.setattr(Project, "filter", filter_projects)
    monkeypatch.setattr(indexer_module, "_ensure_index", Mock())

    SearchIndexer(backend).index_manager_index_batch(
        Project, "global", ({"id": 2}, {"id": 1})
    )

    assert filters == [{"pk__in": [2, 1]}]
    documents = backend.upsert.call_args.args[1]
    assert [document.identification for document in documents] == [
        {"id": 2},
        {"id": 1},
    ]


def test_index_manager_index_batch_missing_orm_owner_has_no_partial_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing bulk-loaded owners fail before any backend write."""
    GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
    backend = Mock()
    monkeypatch.setattr(indexer_module, "OrmInterfaceBase", BaseTestInterface)
    monkeypatch.setattr(
        Project,
        "filter",
        lambda **_kwargs: SimpleBucket(Project, [Project(id=1)]),
    )
    ensure = Mock()
    monkeypatch.setattr(indexer_module, "_ensure_index", ensure)

    with pytest.raises(LookupError, match="Project"):
        SearchIndexer(backend).index_manager_index_batch(
            Project, "global", ({"id": 1}, {"id": 2})
        )

    ensure.assert_not_called()
    backend.upsert.assert_not_called()


def test_index_manager_index_batch_composite_identity_reconstructs_each_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composite manager identities use bounded per-identification construction."""
    GeneralmanagerConfig.initialize_general_manager_classes(
        [CompositeProject], [CompositeProject]
    )
    CompositeProjectInterface.constructed = []
    backend = Mock()
    monkeypatch.setattr(indexer_module, "OrmInterfaceBase", BaseTestInterface)
    monkeypatch.setattr(indexer_module, "_ensure_index", Mock())

    count = SearchIndexer(backend).index_manager_index_batch(
        CompositeProject,
        "global",
        (
            {"tenant": "b", "id": 2},
            {"id": 1, "tenant": "a"},
            {"id": 2, "tenant": "b"},
        ),
    )

    assert count == 2
    assert CompositeProjectInterface.constructed == [
        {"tenant": "b", "id": 2},
        {"id": 1, "tenant": "a"},
    ]


def test_index_manager_index_batch_non_orm_reconstructs_each_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even simple-id non-ORM managers use per-identification construction."""
    GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
    backend = Mock()
    constructed: list[dict[str, object]] = []
    original_init = Project.__init__

    def record_init(self: Project, **kwargs: object) -> None:
        constructed.append(dict(kwargs))
        original_init(self, **kwargs)

    monkeypatch.setattr(Project, "__init__", record_init)
    monkeypatch.setattr(indexer_module, "_ensure_index", Mock())

    SearchIndexer(backend).index_manager_index_batch(
        Project, "global", ({"id": 1}, {"id": 2})
    )

    assert constructed == [{"id": 1}, {"id": 2}]


def test_index_manager_index_batch_empty_validates_without_backend_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty valid batch returns zero without querying or backend calls."""
    GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
    backend = Mock()
    manager_filter = Mock()
    monkeypatch.setattr(indexer_module, "OrmInterfaceBase", BaseTestInterface)
    monkeypatch.setattr(Project, "filter", manager_filter)

    assert SearchIndexer(backend).index_manager_index_batch(Project, "global", ()) == 0
    manager_filter.assert_not_called()
    backend.ensure_index.assert_not_called()
    backend.upsert.assert_not_called()


def test_index_manager_index_batch_rejects_unconfigured_index() -> None:
    """A named batch must target an index configured for its manager."""
    from general_manager.search.indexer import MissingIndexConfigurationError

    GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])

    with pytest.raises(MissingIndexConfigurationError):
        SearchIndexer(Mock()).index_manager_index_batch(Project, "missing", ())


def test_index_manager_index_batch_rejects_manager_without_search_config() -> None:
    """A manager without search configuration cannot accept a named batch."""
    from general_manager.search.indexer import MissingIndexConfigurationError

    class UnconfiguredProject(GeneralManager):
        Interface = ProjectInterface

    GeneralmanagerConfig.initialize_general_manager_classes(
        [UnconfiguredProject], [UnconfiguredProject]
    )

    with pytest.raises(MissingIndexConfigurationError):
        SearchIndexer(Mock()).index_manager_index_batch(
            UnconfiguredProject, "global", ()
        )


class SearchIndexerSignalStateTests(TestCase):
    def setUp(self) -> None:
        """Initialize manager classes for signal state tests."""
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])

    def test_post_change_marks_search_state_dirty(self) -> None:
        """Mark search state dirty after create or update signals."""
        from general_manager.search.invalidation import _handle_search_post_change

        change_context: dict[str, object] = {}

        _handle_search_post_change(
            sender=Project,
            instance=Project(id=1),
            action="update",
            change_context=change_context,
            database_alias="default",
        )

        state = SearchIndexState.objects.get(index_name="global")
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_INITIALIZATION

    def test_pre_delete_marks_search_state_dirty(self) -> None:
        """Pre-delete only captures immutable targets; post-delete marks state."""
        from general_manager.search.invalidation import (
            _handle_search_post_change,
            _handle_search_pre_change,
        )

        change_context: dict[str, object] = {}
        instance = Project(id=1)

        _handle_search_pre_change(
            sender=Project,
            instance=instance,
            action="delete",
            change_context=change_context,
        )
        _handle_search_post_change(
            sender=Project,
            instance=None,
            previous_instance=instance,
            action="delete",
            change_context=change_context,
            database_alias="default",
        )

        state = SearchIndexState.objects.get(index_name="global")
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_INITIALIZATION

    def test_post_change_dispatches_when_dirty_marker_fails(self) -> None:
        """Dispatch immediate indexing even when dirty marking fails."""
        from general_manager.search.invalidation import _handle_search_post_change

        with (
            patch(
                "general_manager.search.invalidation.mark_search_index_dirty",
                side_effect=RuntimeError("state store unavailable"),
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_manager_batch"
            ) as dispatch,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                _handle_search_post_change(
                    sender=Project,
                    instance=Project(id=1),
                    action="update",
                    change_context={},
                    database_alias="default",
                )

        dispatch.assert_called_once()

    def test_delete_dispatches_when_dirty_marker_fails(self) -> None:
        """Dispatch deletion after commit even when dirty marking fails."""
        from general_manager.search.invalidation import (
            _handle_search_post_change,
            _handle_search_pre_change,
        )

        change_context: dict[str, object] = {}
        instance = Project(id=1)
        _handle_search_pre_change(
            sender=Project,
            instance=instance,
            action="delete",
            change_context=change_context,
        )

        with (
            patch(
                "general_manager.search.invalidation.mark_search_index_dirty",
                side_effect=RuntimeError("state store unavailable"),
            ),
            patch(
                "general_manager.search.invalidation.dispatch_delete_documents"
            ) as dispatch,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                _handle_search_post_change(
                    sender=Project,
                    instance=None,
                    previous_instance=instance,
                    action="delete",
                    change_context=change_context,
                    database_alias="default",
                )

        dispatch.assert_called_once()

    def test_failed_delete_capture_leaves_pair_dirty_without_acknowledgement(
        self,
    ) -> None:
        """Missing immutable delete IDs cannot be treated as successful work."""
        from general_manager.search.invalidation import (
            _DIRECT_SEARCH_CHANGE_CONTEXT,
            _PendingDirectSearchChange,
            _handle_search_post_change,
        )
        from general_manager.search.reconciliation import DirtySearchIndex

        token = DirtySearchIndex(
            state_id=1,
            manager_path="tests.Project",
            index_name="global",
            generation=1,
            acknowledgeable=True,
        )
        change_context: dict[str, object] = {
            _DIRECT_SEARCH_CHANGE_CONTEXT: _PendingDirectSearchChange(action="delete")
        }
        with (
            patch(
                "general_manager.search.invalidation.mark_search_index_dirty",
                return_value=token,
            ),
            patch(
                "general_manager.search.invalidation.dispatch_delete_documents"
            ) as dispatch,
            patch(
                "general_manager.search.invalidation.acknowledge_search_index_dirty"
            ) as acknowledge,
            self.captureOnCommitCallbacks(execute=True),
        ):
            _handle_search_post_change(
                sender=Project,
                instance=None,
                action="delete",
                change_context=change_context,
                database_alias="default",
            )

        dispatch.assert_not_called()
        acknowledge.assert_not_called()
