"""Excel snapshots must be reusable across workers and disposable like any cache."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from django.core.cache import cache, caches
from django.test import override_settings

from general_manager.interface.excel_store import ExcelWorkbookStore
from tests.unit.test_excel_interface import (
    build_product_manager,
    write_product_workbook,
    set_product_workbook_value,
)


@pytest.fixture
def product(tmp_path):
    cache.clear()
    path = tmp_path / "products.xlsx"
    write_product_workbook(path, [["SKU-1", "Alpha"]])
    return build_product_manager(path), path


def test_new_store_reuses_published_rows(product):
    manager, _ = product
    manager.sync_excel()
    fresh = ExcelWorkbookStore().mirror_for(manager.Interface)
    assert fresh.rows["SKU-1"].values["name"] == "Alpha"


def test_shared_snapshot_is_not_mutated_by_reader(product):
    manager, _ = product
    manager.sync_excel()
    first = ExcelWorkbookStore().mirror_for(manager.Interface)
    first.rows["SKU-1"].values["name"] = "corrupted"
    assert (
        ExcelWorkbookStore().mirror_for(manager.Interface).rows["SKU-1"].values["name"]
        == "Alpha"
    )


def test_unchanged_reads_do_not_parse_workbook(product):
    manager, _ = product
    manager.sync_excel()
    with patch(
        "general_manager.interface.excel_workbook.load_workbook",
        side_effect=AssertionError("Unchanged workbook was parsed"),
    ):
        assert manager(sku="SKU-1").name == "Alpha"
        assert manager.all().count() == 1


def test_dummy_cache_supports_reads_and_writes(tmp_path):
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    ):
        path = tmp_path / "dummy.xlsx"
        write_product_workbook(path, [["SKU-1", "Alpha"]])
        manager = build_product_manager(path)
        assert manager(sku="SKU-1").name == "Alpha"
        manager(sku="SKU-1").update(name="Beta", ignore_permission=True)
        assert manager(sku="SKU-1").name == "Beta"


def test_mirror_cache_failure_falls_back_to_workbook(product):
    manager, path = product
    # Separate alias keeps the project's dependency cache healthy.
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "excel-dependencies",
            },
            "excel": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "excel-mirror",
            },
        }
    ):
        manager.Interface.excel_meta = replace(
            manager.Interface.excel_meta, cache_alias="excel"
        )
        with (
            patch.object(
                caches["excel"], "get", side_effect=ConnectionError("offline")
            ),
            patch.object(
                caches["excel"], "set", side_effect=ConnectionError("offline")
            ),
        ):
            assert manager(sku="SKU-1").name == "Alpha"
            set_product_workbook_value(path, "SKU-1", "Beta")
            assert manager(sku="SKU-1").name == "Beta"


def test_evicted_mirror_rebuilds_without_losing_rows(product):
    manager, _ = product
    manager.sync_excel()
    cache.clear()
    with patch(
        "general_manager.interface.capabilities.excel.DEFAULT_EXCEL_STORE",
        ExcelWorkbookStore(),
    ):
        assert manager.all().count() == 1
        assert manager(sku="SKU-1").name == "Alpha"


def test_existing_manager_reads_external_edits(product):
    manager, path = product
    row = manager(sku="SKU-1")
    assert row.name == "Alpha"
    set_product_workbook_value(path, "SKU-1", "Beta")
    assert row.name == "Beta"


def test_cold_mirror_invalidates_cached_results_for_deleted_rows(product):
    from general_manager.cache.cache_decorator import cached
    from general_manager.interface.excel_store import mirror_cache_key
    from tests.unit.test_excel_interface import delete_product_workbook_row

    manager, path = product
    manager.sync_excel()

    @cached(cache="dependency")
    def alpha_count():
        return manager.filter(name="Alpha").count()

    assert alpha_count() == 1
    delete_product_workbook_row(path, "SKU-1")
    cache.delete(mirror_cache_key(manager.Interface))
    with patch(
        "general_manager.interface.capabilities.excel.DEFAULT_EXCEL_STORE",
        ExcelWorkbookStore(),
    ):
        manager.sync_excel()
    assert alpha_count() == 0


def _run_worker(script, *arguments):
    """Run a genuinely fresh interpreter against this checkout's source."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    environment = dict(
        os.environ,
        PYTHONPATH=str(root / "src"),
        DJANGO_SETTINGS_MODULE="tests.test_settings",
    )
    return (
        subprocess.run(  # noqa: S603 - fixed interpreter and test-owned script
            [sys.executable, "-c", script, *map(str, arguments)],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )


@pytest.mark.parametrize("backend", ["file", "redis"])
def test_shared_cache_snapshot_is_read_and_refreshed_by_another_process(
    tmp_path, backend
):
    import json
    import os
    import uuid

    path = tmp_path / "shared.xlsx"
    cache_dir = tmp_path / "cache"
    config = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(cache_dir),
        }
    }
    if backend == "redis":
        url = os.environ.get("GENERAL_MANAGER_TEST_REDIS_URL")
        if not url:
            pytest.skip("Set GENERAL_MANAGER_TEST_REDIS_URL to exercise real Redis")
        pytest.importorskip("redis")
        config = {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": url,
            }
        }
    config["default"]["KEY_PREFIX"] = "excel-test-" + uuid.uuid4().hex
    with override_settings(CACHES=config):
        write_product_workbook(path, [["SKU-1", "Alpha"]])
        manager = build_product_manager(path)
        manager.sync_excel()
        result = _run_worker(
            """
import sys
import json
import django
django.setup()
from django.test import override_settings
from unittest.mock import patch
from pathlib import Path
from tests.unit.test_excel_interface import build_product_manager
with override_settings(CACHES=json.loads(sys.argv[2])):
    manager = build_product_manager(Path(sys.argv[1]))
    with patch('general_manager.interface.excel_workbook.load_workbook', side_effect=AssertionError('Shared snapshot was not reused')):
        assert manager(sku='SKU-1').name == 'Alpha'
    manager(sku='SKU-1').update(name='Worker edit', ignore_permission=True)
    print('updated')
""",
            path,
            json.dumps(config),
        )
        assert result == "updated"
        with patch(
            "general_manager.interface.excel_workbook.load_workbook",
            side_effect=AssertionError("Parent did not reuse worker snapshot"),
        ):
            assert manager(sku="SKU-1").name == "Worker edit"


def test_workbook_lock_excludes_other_processes_even_with_dummy_cache(product):
    _, path = product
    script = """
import sys
import django
django.setup()
from filelock import Timeout
from django.test import override_settings
from general_manager.interface.excel_store import ExcelWorkbookStore
with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}):
    try:
        with ExcelWorkbookStore().lock_for(sys.argv[1]).acquire(timeout=0.05):
            print('acquired')
    except Timeout:
        print('blocked')
"""
    with ExcelWorkbookStore().lock_for(str(path)):
        assert _run_worker(script, path) == "blocked"
    assert _run_worker(script, path) == "acquired"


def test_cache_identity_separates_field_configuration(product):
    from general_manager.interface.excel import ExcelCharField

    manager, path = product
    manager.sync_excel()
    other = build_product_manager(path)
    other.Interface.excel_fields = dict(
        other.Interface.excel_fields, name=ExcelCharField(max_length=2)
    )
    assert ExcelWorkbookStore().mirror_for(other.Interface).fingerprint is None


def test_cache_identity_separates_inherited_field_options(product):
    from general_manager.interface.excel import ExcelCharField

    manager, path = product
    manager.sync_excel()
    other = build_product_manager(path)
    other.Interface.excel_fields = dict(
        other.Interface.excel_fields, name=ExcelCharField(header="Other Header")
    )
    assert ExcelWorkbookStore().mirror_for(other.Interface).fingerprint is None


def test_plain_excel_field_supports_shared_cache(product):
    from general_manager.interface.excel import ExcelField

    manager, _ = product
    manager.Interface.excel_fields = dict(
        manager.Interface.excel_fields, name=ExcelField(str)
    )
    manager.sync_excel()
    assert (
        ExcelWorkbookStore().mirror_for(manager.Interface).rows["SKU-1"].values["name"]
        == "Alpha"
    )


def test_invalidation_failure_does_not_commit_local_snapshot(product):
    from general_manager.cache.cache_decorator import cached

    manager, path = product
    # Keep the dependency result in the same cache throughout failure and retry.
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "excel-retry-dependencies",
            },
            "excel": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
        }
    ):
        cache.clear()
        manager.Interface.excel_meta = replace(
            manager.Interface.excel_meta, cache_alias="excel"
        )
        manager.sync_excel()
        calls = {"count": 0}

        @cached(cache="dependency")
        def alpha_count():
            calls["count"] += 1
            return manager.filter(name="Alpha").count()

        assert alpha_count() == 1
        set_product_workbook_value(path, "SKU-1", "Beta")
        with patch(
            "general_manager.interface.capabilities.excel._invalidate_dependency_cache_from_delta",
            side_effect=RuntimeError("failed invalidation"),
        ):
            with pytest.raises(RuntimeError):
                manager.sync_excel()
        delta = manager.sync_excel()
        assert len(delta.updated) == 1
        assert alpha_count() == 0
        assert alpha_count() == 0
        assert calls["count"] == 2


@pytest.mark.django_db(transaction=True)
def test_database_cache_can_share_excel_snapshot(tmp_path):
    from django.core.management import call_command
    from django.db import connection

    config = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "excel_test_cache",
        }
    }
    with override_settings(CACHES=config):
        call_command("createcachetable", verbosity=0)
        try:
            path = tmp_path / "database-cache.xlsx"
            write_product_workbook(path, [["SKU-1", "Alpha"]])
            manager = build_product_manager(path)
            manager.sync_excel()
            assert (
                ExcelWorkbookStore()
                .mirror_for(manager.Interface)
                .rows["SKU-1"]
                .values["name"]
                == "Alpha"
            )
            manager(sku="SKU-1").update(name="Beta", ignore_permission=True)
            assert (
                ExcelWorkbookStore()
                .mirror_for(manager.Interface)
                .rows["SKU-1"]
                .values["name"]
                == "Beta"
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE excel_test_cache")
