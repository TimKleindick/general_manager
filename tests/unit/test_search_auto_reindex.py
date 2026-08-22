from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from django.test import SimpleTestCase, override_settings

from general_manager import apps as gm_apps
from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.async_tasks import dispatch_index_update
from general_manager.search.backend import SearchDocument
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from tests.unit.test_search_indexer import Project, ProjectInterface

TEST_TIMEOUT_SECONDS = 2.0


def _search_document(index_name: str) -> SearchDocument:
    """Build one document whose query behavior proves hydration completed."""
    return SearchDocument(
        id=f"Project:{index_name}",
        type="Project",
        identification={"id": 1},
        index=index_name,
        data={"name": "Dockmaster"},
        field_boosts={"name": 1.0},
    )


class HydrationSourceUnavailableError(RuntimeError):
    """Raised by the test hydration source when its first read is unavailable."""

    def __init__(self) -> None:
        super().__init__("source unavailable")


class RecordingHydrationBackend(DevSearchBackend):
    """DevSearch double that populates a real document while hydrating."""

    def __init__(self, *, fail_once: bool = False) -> None:
        super().__init__(auto_reindex=True)
        self.hydrated_indexes: list[str] = []
        self.fail_once = fail_once

    def _reindex_configured_managers(self, index_name: str) -> None:
        self.hydrated_indexes.append(index_name)
        if self.fail_once:
            self.fail_once = False
            raise HydrationSourceUnavailableError
        self.upsert(index_name, [_search_document(index_name)])


class NestedHydrationBackend(RecordingHydrationBackend):
    """Exercise the guarded reentrant search path during hydration."""

    def _reindex_configured_managers(self, index_name: str) -> None:
        self.hydrated_indexes.append(index_name)
        assert self.search(index_name, "Dock").total == 0
        self.upsert(index_name, [_search_document(index_name)])


class ConcurrentHydrationBackend(RecordingHydrationBackend):
    """Hold one rebuild open while another search waits for the same index."""

    def __init__(self) -> None:
        super().__init__()
        self.hydration_started = Event()
        self.release_hydration = Event()

    def _reindex_configured_managers(self, index_name: str) -> None:
        self.hydrated_indexes.append(index_name)
        self.hydration_started.set()
        assert self.release_hydration.wait(timeout=TEST_TIMEOUT_SECONDS)
        self.upsert(index_name, [_search_document(index_name)])


class SearchAutoReindexRemovedTests(SimpleTestCase):
    def test_legacy_auto_reindex_helpers_are_removed(self) -> None:
        """Keep removed request-triggered auto-reindex helpers unavailable."""
        assert not hasattr(gm_apps, "_SEARCH_REINDEXED")
        assert not hasattr(gm_apps, "_auto_reindex_search")
        assert not hasattr(gm_apps, "install_search_auto_reindex")
        assert not hasattr(gm_apps.GeneralmanagerConfig, "install_search_auto_reindex")


def test_first_search_hydrates_index_once() -> None:
    """An opted-in index is populated once and its first query sees documents."""
    backend = RecordingHydrationBackend()

    assert backend.search("global", "Dock").total == 1
    assert backend.search("global", "Dock").total == 1
    assert backend.hydrated_indexes == ["global"]


def test_hydration_is_tracked_per_index() -> None:
    """Hydrating one logical index leaves another index pending."""
    backend = RecordingHydrationBackend()

    assert backend.search("global", "Dock").total == 1
    assert backend.search("private", "Dock").total == 1
    assert backend.hydrated_indexes == ["global", "private"]


def test_failed_hydration_propagates_and_retries() -> None:
    """A source failure is visible and does not mark the index complete."""
    backend = RecordingHydrationBackend(fail_once=True)

    with pytest.raises(RuntimeError, match="source unavailable"):
        backend.search("global", "Dock")

    assert backend.search("global", "Dock").total == 1
    assert backend.hydrated_indexes == ["global", "global"]


def test_nested_hydration_is_guarded() -> None:
    """Hydration can query its own index without recursively rebuilding it."""
    backend = NestedHydrationBackend()

    assert backend.search("global", "Dock").total == 1
    assert backend.hydrated_indexes == ["global"]


def test_concurrent_first_searches_share_one_completed_hydration() -> None:
    """Competing first searches wait for one rebuild and see its documents."""
    backend = ConcurrentHydrationBackend()
    searches_ready = Barrier(3)

    def search() -> int:
        searches_ready.wait(timeout=TEST_TIMEOUT_SECONDS)
        return backend.search("global", "Dock").total

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(search) for _index in range(2)]
        searches_ready.wait(timeout=TEST_TIMEOUT_SECONDS)
        try:
            assert backend.hydration_started.wait(timeout=TEST_TIMEOUT_SECONDS)
            assert not any(future.done() for future in futures)
        finally:
            backend.release_hydration.set()
        totals = [future.result(timeout=TEST_TIMEOUT_SECONDS) for future in futures]

    assert totals == [1, 1]
    assert backend.hydrated_indexes == ["global"]


def test_direct_backend_does_not_hydrate_without_opt_in() -> None:
    """Direct development backends retain their inert, isolated default."""
    backend = DevSearchBackend()

    assert backend.search("global", "").total == 0


class DevSearchLifecycleIntegrationTests(SimpleTestCase):
    """Exercise hydration and the synchronous update path with real managers."""

    def setUp(self) -> None:
        super().setUp()
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
        self.original_manager_classes = list(GeneralManagerMeta.all_classes)
        GeneralManagerMeta.all_classes[:] = [Project]
        self.backend = DevSearchBackend(auto_reindex=True)
        self.original_data_store = ProjectInterface.data_store.copy()
        configure_search_backend(self.backend)

    def tearDown(self) -> None:
        ProjectInterface.data_store = self.original_data_store
        GeneralManagerMeta.all_classes[:] = self.original_manager_classes
        configure_search_backend(None)
        super().tearDown()

    @override_settings(SEARCH_ASYNC=False)
    def test_hydration_then_synchronous_update_finds_new_source_record(self) -> None:
        """A hydrated serving backend receives later inline lifecycle writes."""
        initial = self.backend.search("global", "Alpha")
        assert [hit.identification for hit in initial.hits] == [{"id": 1}]

        ProjectInterface.data_store[3] = {
            "name": "Gamma",
            "status": "public",
            "secret": "hidden",
        }
        dispatch_index_update(
            action="index",
            manager_path="tests.unit.test_search_indexer.Project",
            identification={"id": 3},
            instance=Project(id=3),
            index_name="global",
        )

        updated = self.backend.search("global", "Gamma")
        assert [hit.identification for hit in updated.hits] == [{"id": 3}]
