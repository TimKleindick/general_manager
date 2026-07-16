"""Tests for bounded upload-intent reconciliation and cleanup."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from io import StringIO
import os
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.core.management import CommandError, call_command
from django.db import models
from django.db import connection
from django.db import OperationalError
from django.test import SimpleTestCase
from django.utils import timezone

from general_manager.uploads import finalization
from general_manager.uploads.finalization import run_upload_cleanup
from general_manager.uploads.errors import UploadObjectMissingError
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.types import ObjectVersion, UploadIntentState


sqlite_only = pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="exercises SQLite-specific locking or transaction behavior",
)


def _sqlite_subprocess_environment(settings_module: str) -> dict[str, str]:
    return {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": settings_module,
        "GENERAL_MANAGER_TEST_DATABASE": "sqlite",
        "PYTHONPATH": os.pathsep.join((os.path.join(os.getcwd(), "src"), os.getcwd())),
    }


def _intent(
    *,
    state: UploadIntentState,
    expires_delta: int = -60,
    staging_key: str = "gm-staging/intent/object",
    transfer_attempt_count: int = 0,
) -> UploadIntent:
    now = timezone.now()
    return UploadIntent.objects.create(
        token_digest="a" * 64,
        manager_name="CleanupManager",
        field_name="file",
        operation="create",
        adapter_id="proxy",
        adapter_version="1",
        storage_fingerprint="sha256:" + "b" * 64,
        staging_key=staging_key,
        transfer_attempt_count=transfer_attempt_count,
        declared_size=1,
        declared_content_type="application/octet-stream",
        declared_checksum_sha256="c" * 64,
        state=state.value,
        expires_at=now + timedelta(seconds=expires_delta),
    )


_VERSION = ObjectVersion(
    version_id="v1",
    etag="etag",
    checksum_sha256="c" * 64,
    size=1,
    content_type="application/octet-stream",
)


class RecordingCleanupAdapter:
    adapter_id = "proxy"
    adapter_version = 1

    def __init__(self) -> None:
        self.deleted_stages: list[tuple[str, ObjectVersion | None]] = []
        self.deleted_final: list[str] = []
        self.fail_stage: Exception | None = None
        self.fail_inspect: Exception | None = None

    @property
    def supports_public_urls(self) -> bool:
        return False

    def delete_stage(self, key: str, version: ObjectVersion | None = None) -> None:
        if self.fail_stage is not None:
            error, self.fail_stage = self.fail_stage, None
            raise error
        self.deleted_stages.append((key, version))

    def inspect_staged(self, key: str) -> ObjectVersion:
        del key
        if self.fail_inspect is not None:
            error, self.fail_inspect = self.fail_inspect, None
            raise error
        return _VERSION

    def inspect_materialized(
        self, final_key: str, source_version: ObjectVersion, *, intent_id: UUID
    ) -> ObjectVersion:
        del final_key, intent_id
        return source_version

    def delete_materialized(
        self, final_key: str, final_version: ObjectVersion, *, intent_id: UUID
    ) -> None:
        del final_version, intent_id
        self.deleted_final.append(final_key)

    def delete_object(self, key: str, version: ObjectVersion) -> None:
        del key, version

    def inspect_replaced_object(self, key: str) -> ObjectVersion:
        del key
        return _VERSION

    def plan_replaced_object_claim(self, key, version, *, cleanup_id):
        del key, version, cleanup_id
        raise AssertionError

    def claim_replaced_object(self, key, claimed, *, cleanup_id) -> None:
        del key, claimed, cleanup_id
        raise AssertionError

    def delete_claimed_object(self, claimed, *, cleanup_id) -> None:
        del claimed, cleanup_id
        raise AssertionError


def _cleanup_dependency_patches(adapter: RecordingCleanupAdapter):
    return (
        patch(
            "general_manager.uploads.finalization.services._resolve_manager",
            return_value=SimpleNamespace(Interface=SimpleNamespace),
        ),
        patch(
            "general_manager.uploads.finalization.services._resolve_file_field",
            return_value=(UploadIntent, models.FileField()),
        ),
        patch(
            "general_manager.uploads.finalization.services._resolve_intent_adapter",
            return_value=adapter,
        ),
    )


@pytest.mark.django_db(transaction=True)
def test_cleanup_reconciles_finalizing_before_expiring_pending() -> None:
    finalizing = _intent(state=UploadIntentState.FINALIZING)
    pending = _intent(state=UploadIntentState.PENDING)
    order: list[str] = []

    def reconcile(
        _intent_id,
        *,
        database_alias,
    ):
        del database_alias
        order.append("finalizing")
        return UploadIntentState.CONSUMED.value

    def cleanup(intent_id, *, alias, at):
        del alias, at
        order.append("expired")
        UploadIntent.objects.filter(pk=intent_id).update(
            cleanup_completed_at=timezone.now()
        )
        return True

    with (
        patch(
            "general_manager.uploads.finalization.finalize_upload_intent",
            side_effect=reconcile,
        ),
        patch(
            "general_manager.uploads.finalization._cleanup_terminal_intent",
            side_effect=cleanup,
        ),
    ):
        counts = run_upload_cleanup(batch_size=10, older_than_seconds=1)

    pending.refresh_from_db()
    assert finalizing.pk
    assert pending.state == UploadIntentState.EXPIRED.value
    assert order == ["finalizing", "expired"]
    assert counts.reconciled == 1
    assert counts.expired == 1


@pytest.mark.django_db(transaction=True)
def test_cleanup_dry_run_has_zero_database_or_storage_mutation() -> None:
    pending = _intent(state=UploadIntentState.PENDING)

    with (
        patch(
            "general_manager.uploads.finalization.finalize_upload_intent"
        ) as finalizer,
        patch(
            "general_manager.uploads.finalization._cleanup_terminal_intent"
        ) as cleanup,
    ):
        counts = run_upload_cleanup(
            batch_size=10,
            older_than_seconds=1,
            dry_run=True,
        )

    pending.refresh_from_db()
    assert pending.state == UploadIntentState.PENDING.value
    assert counts.expired == 1
    finalizer.assert_not_called()
    cleanup.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_cleanup_never_disrupts_an_active_transfer_lease() -> None:
    intent = _intent(state=UploadIntentState.TRANSFERRING)
    intent.transfer_lease_expires_at = timezone.now() + timedelta(minutes=5)
    intent.save(update_fields=("transfer_lease_expires_at",))

    counts = run_upload_cleanup(batch_size=10, older_than_seconds=1)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.TRANSFERRING.value
    assert counts.expired == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "state",
    (
        UploadIntentState.PENDING,
        UploadIntentState.UPLOADED,
        UploadIntentState.TRANSFERRING,
    ),
)
def test_cleanup_expires_every_abandoned_active_state(state) -> None:
    intent = _intent(state=state)
    if state is UploadIntentState.TRANSFERRING:
        intent.transfer_lease_expires_at = timezone.now() - timedelta(seconds=1)
        intent.save(update_fields=("transfer_lease_expires_at",))
    with patch(
        "general_manager.uploads.finalization._cleanup_terminal_intent",
        return_value=True,
    ):
        counts = run_upload_cleanup(batch_size=10, older_than_seconds=1)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.EXPIRED.value
    assert counts.expired == 1


@pytest.mark.django_db(transaction=True)
def test_cleanup_retries_rejected_terminal_artifacts() -> None:
    intent = _intent(state=UploadIntentState.REJECTED)
    UploadIntent.objects.filter(pk=intent.pk).update(
        updated_at=timezone.now() - timedelta(hours=2)
    )
    with patch(
        "general_manager.uploads.finalization._cleanup_terminal_intent",
        return_value=True,
    ) as cleanup:
        counts = run_upload_cleanup(batch_size=10, older_than_seconds=1)

    assert counts.cleaned == 1
    cleanup.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_cleanup_deletes_all_proxy_attempt_keys_and_exact_current_version() -> None:
    intent = _intent(
        state=UploadIntentState.EXPIRED,
        staging_key="gm-staging/base.proxy-attempt-3",
        transfer_attempt_count=3,
    )
    intent.object_version = asdict(_VERSION)
    intent.save(update_fields=("object_version",))
    adapter = RecordingCleanupAdapter()
    manager_patch, field_patch, adapter_patch = _cleanup_dependency_patches(adapter)
    with manager_patch, field_patch, adapter_patch:
        assert finalization._cleanup_terminal_intent(intent.id, alias="default")

    assert adapter.deleted_stages == [
        ("gm-staging/base.proxy-attempt-3", _VERSION),
        ("gm-staging/base", _VERSION),
        ("gm-staging/base.proxy-attempt-1", _VERSION),
        ("gm-staging/base.proxy-attempt-2", _VERSION),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "state", (UploadIntentState.CONSUMED, UploadIntentState.SUPERSEDED)
)
def test_cleanup_terminal_retry_is_idempotent_after_storage_failure(state) -> None:
    intent = _intent(state=state)
    intent.object_version = asdict(_VERSION)
    if state is UploadIntentState.SUPERSEDED:
        intent.final_key = "owned/final"
        intent.final_object_version = asdict(_VERSION)
    intent.save(update_fields=("object_version", "final_key", "final_object_version"))
    adapter = RecordingCleanupAdapter()
    adapter.fail_stage = OSError("storage-secret")
    manager_patch, field_patch, adapter_patch = _cleanup_dependency_patches(adapter)
    with manager_patch, field_patch, adapter_patch:
        assert not finalization._cleanup_terminal_intent(intent.id, alias="default")
    intent.refresh_from_db()
    assert intent.cleanup_completed_at is None
    assert intent.cleanup_error_code == "UPLOAD_STORAGE_ERROR"

    manager_patch, field_patch, adapter_patch = _cleanup_dependency_patches(adapter)
    with manager_patch, field_patch, adapter_patch:
        assert finalization._cleanup_terminal_intent(intent.id, alias="default")
    intent.refresh_from_db()
    assert intent.cleanup_completed_at is not None
    assert intent.cleanup_error_code == ""
    if state is UploadIntentState.SUPERSEDED:
        assert adapter.deleted_final == ["owned/final"]


@pytest.mark.django_db(transaction=True)
def test_cleanup_tolerates_already_missing_owned_objects() -> None:
    intent = _intent(state=UploadIntentState.EXPIRED)
    adapter = RecordingCleanupAdapter()
    adapter.fail_stage = UploadObjectMissingError("missing-secret")
    manager_patch, field_patch, adapter_patch = _cleanup_dependency_patches(adapter)
    with manager_patch, field_patch, adapter_patch:
        assert finalization._cleanup_terminal_intent(intent.id, alias="default")

    intent.refresh_from_db()
    assert intent.cleanup_completed_at is not None


@pytest.mark.django_db(transaction=True)
def test_cleanup_retries_ambiguous_raw_file_not_found_failure() -> None:
    intent = _intent(state=UploadIntentState.EXPIRED)
    adapter = RecordingCleanupAdapter()
    adapter.fail_stage = FileNotFoundError("ambiguous-outage-secret")
    manager_patch, field_patch, adapter_patch = _cleanup_dependency_patches(adapter)
    with manager_patch, field_patch, adapter_patch:
        assert not finalization._cleanup_terminal_intent(intent.id, alias="default")

    intent.refresh_from_db()
    assert intent.cleanup_completed_at is None
    assert intent.cleanup_error_code == "UPLOAD_STORAGE_ERROR"


@pytest.mark.django_db(transaction=True)
def test_cleanup_inspects_unrecorded_stage_before_exact_delete() -> None:
    intent = _intent(state=UploadIntentState.EXPIRED)
    adapter = RecordingCleanupAdapter()
    manager_patch, field_patch, adapter_patch = _cleanup_dependency_patches(adapter)
    with manager_patch, field_patch, adapter_patch:
        assert finalization._cleanup_terminal_intent(intent.id, alias="default")

    assert adapter.deleted_stages == [(intent.staging_key, _VERSION)]


@pytest.mark.django_db(transaction=True)
def test_cleanup_retains_finalizing_failures_for_reconciliation() -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    with patch(
        "general_manager.uploads.finalization.finalize_upload_intent",
        return_value="failed",
    ) as finalizer:
        counts = run_upload_cleanup(batch_size=1, older_than_seconds=1)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.FINALIZING.value
    assert counts.reconciled == 0
    assert counts.failed == 1
    assert finalizer.call_args.args == (intent.id,)
    assert finalizer.call_args.kwargs["database_alias"] == "default"
    assert "reconciliation_lease_expires_at" not in finalizer.call_args.kwargs


@pytest.mark.django_db(transaction=True)
def test_cleanup_shared_batch_budget_reserves_later_phase_work() -> None:
    _intent(state=UploadIntentState.FINALIZING)
    _intent(state=UploadIntentState.FINALIZING)
    pending = _intent(state=UploadIntentState.PENDING)
    with (
        patch(
            "general_manager.uploads.finalization.finalize_upload_intent",
            return_value="failed",
        ),
        patch(
            "general_manager.uploads.finalization._cleanup_terminal_intent",
            return_value=True,
        ),
    ):
        counts = run_upload_cleanup(batch_size=2, older_than_seconds=1)

    pending.refresh_from_db()
    assert counts.reconciled == 0
    assert counts.failed == 1
    assert counts.expired == 1
    assert pending.state == UploadIntentState.EXPIRED.value


@pytest.mark.django_db(transaction=True)
def test_finalizing_cleanup_reports_failure_without_preleasing_the_row() -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    seen_leases: list[object] = []

    def fail(
        _intent_id,
        *,
        database_alias,
    ):
        del database_alias
        current = UploadIntent.objects.get(pk=intent.pk)
        seen_leases.append(
            (current.cleanup_lease_expires_at, current.cleanup_lease_token)
        )
        return "failed"

    with patch(
        "general_manager.uploads.finalization.finalize_upload_intent",
        side_effect=fail,
    ):
        counts = run_upload_cleanup(batch_size=2, older_than_seconds=1)

    intent.refresh_from_db()
    assert seen_leases == [(None, "")]
    assert intent.cleanup_lease_expires_at is None
    assert counts.reconciled == 0
    assert counts.failed == 1


@pytest.mark.django_db(transaction=True)
def test_finalizing_batch_claims_each_row_only_immediately_before_work() -> None:
    first = _intent(state=UploadIntentState.FINALIZING)
    second = _intent(state=UploadIntentState.FINALIZING)
    observations: list[tuple[object, object]] = []

    def finalize(intent_id, **_kwargs):
        other_id = second.id if intent_id == first.id else first.id
        other = UploadIntent.objects.get(pk=other_id)
        observations.append((other.cleanup_lease_expires_at, other.cleanup_lease_token))
        return "failed"

    with patch(
        "general_manager.uploads.finalization.finalize_upload_intent",
        side_effect=finalize,
    ):
        run_upload_cleanup(batch_size=8, older_than_seconds=1)

    assert observations[0] == (None, "")


@pytest.mark.django_db(transaction=True)
def test_start_finalization_rejects_matching_but_expired_expected_lease() -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    expired = timezone.now() - timedelta(seconds=1)
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_lease_expires_at=expired,
        cleanup_lease_token="expired-owner",  # noqa: S106 - opaque owner fixture
    )

    claim = finalization._start_finalization_attempt(
        intent.id,
        alias="default",
        expected_lease_expires_at=expired,
        expected_lease_token="expired-owner",  # noqa: S106 - opaque owner fixture
    )

    assert claim is None
    intent.refresh_from_db()
    assert intent.finalization_attempt_count == 0


@pytest.mark.django_db(transaction=True)
def test_finalizing_phase_reserves_budget_for_later_expiry_work() -> None:
    poison = _intent(state=UploadIntentState.FINALIZING)
    _intent(state=UploadIntentState.FINALIZING)
    pending = _intent(state=UploadIntentState.PENDING)

    with (
        patch(
            "general_manager.uploads.finalization.finalize_upload_intent",
            return_value="failed",
        ) as finalizer,
        patch(
            "general_manager.uploads.finalization._cleanup_terminal_intent",
            return_value=True,
        ),
    ):
        counts = run_upload_cleanup(batch_size=2, older_than_seconds=1)

    pending.refresh_from_db()
    assert finalizer.call_count == 1
    assert finalizer.call_args.args == (poison.id,)
    assert counts.failed == 1
    assert counts.expired == 1
    assert pending.state == UploadIntentState.EXPIRED.value


@pytest.mark.django_db(transaction=True)
def test_shared_budget_reserves_work_for_every_cleanup_phase() -> None:
    _intent(state=UploadIntentState.FINALIZING)
    for _index in range(3):
        _intent(state=UploadIntentState.PENDING)
    terminal = _intent(state=UploadIntentState.REJECTED)
    deletable = _intent(state=UploadIntentState.EXPIRED)
    old = timezone.now() - timedelta(days=2)
    UploadIntent.objects.filter(pk__in=(terminal.pk, deletable.pk)).update(
        updated_at=old
    )
    UploadIntent.objects.filter(pk=deletable.pk).update(cleanup_completed_at=old)

    with (
        patch(
            "general_manager.uploads.finalization.finalize_upload_intent",
            return_value="failed",
        ),
        patch(
            "general_manager.uploads.finalization._cleanup_terminal_intent",
            return_value=True,
        ),
    ):
        counts = run_upload_cleanup(batch_size=4, older_than_seconds=1)

    assert counts.failed == 1
    assert counts.expired == 1
    assert counts.cleaned == 2  # expired artifact plus one pre-existing terminal row
    assert counts.deleted == 1


@pytest.mark.django_db(transaction=True)
def test_active_finalization_lease_prevents_a_second_worker_claim() -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)

    first = finalization._start_finalization_attempt(
        intent.id,
        alias="default",
        expected_lease_expires_at=None,
        expected_lease_token=None,
    )
    second = finalization._start_finalization_attempt(
        intent.id,
        alias="default",
        expected_lease_expires_at=None,
        expected_lease_token=None,
    )

    assert first is not None
    assert second is None


@pytest.mark.django_db(transaction=True)
@sqlite_only
def test_finalizing_selection_retries_sqlite_worker_contention(monkeypatch) -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    selected = [intent.id]
    operation = patch(
        "general_manager.uploads.finalization._cleanup_candidate_ids_once",
        side_effect=[OperationalError("database is locked"), selected],
    )
    monkeypatch.setattr(
        "general_manager.uploads.finalization.time.sleep", lambda _delay: None
    )
    with operation as attempt:
        result = finalization._cleanup_candidate_ids(
            UploadIntent.objects.filter(state=UploadIntentState.FINALIZING.value),
            limit=1,
        )

    assert result == selected
    assert attempt.call_count == 2


@pytest.mark.django_db(transaction=True)
def test_finalizing_claim_lease_uses_fresh_time_after_slow_selection(
    monkeypatch,
) -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    claim_time = timezone.now() + timedelta(minutes=20)
    monkeypatch.setattr(finalization.timezone, "now", lambda: claim_time)

    claim = finalization._start_finalization_attempt(
        intent.id,
        alias="default",
        expected_lease_expires_at=None,
        expected_lease_token=None,
    )

    assert claim is not None
    assert claim[0] > claim_time


@pytest.mark.django_db(transaction=True)
@sqlite_only
def test_terminal_claim_retries_sqlite_worker_contention(monkeypatch) -> None:
    intent = _intent(state=UploadIntentState.EXPIRED)
    operation = patch(
        "general_manager.uploads.finalization._claim_terminal_cleanup_once",
        side_effect=[OperationalError("database is locked"), None],
    )
    monkeypatch.setattr(finalization.time, "sleep", lambda _delay: None)
    with operation as attempt:
        assert not finalization._cleanup_terminal_intent(intent.id, alias="default")

    assert attempt.call_count == 2


@pytest.mark.django_db(transaction=True)
def test_cleanup_counts_non_busy_database_failure_without_retry_or_abort() -> None:
    _intent(state=UploadIntentState.FINALIZING)
    operation = patch(
        "general_manager.uploads.finalization._cleanup_candidate_ids_once",
        side_effect=OperationalError("disk I/O error with hostile-secret"),
    )

    with operation as attempt:
        counts = run_upload_cleanup(batch_size=1, older_than_seconds=1)

    assert attempt.call_count == 1
    assert counts.failed == 1


class SQLiteUploadCleanupIntegrationTests(SimpleTestCase):
    def test_two_file_backed_sqlite_workers_finish_without_lock_errors(self) -> None:
        script = textwrap.dedent(
            """
            from concurrent.futures import ThreadPoolExecutor
            from datetime import timedelta
            from io import StringIO
            import re
            import sys
            from threading import Barrier

            from tests import test_settings

            test_settings.DATABASES["default"]["NAME"] = sys.argv[1]

            import django
            django.setup()

            from django.db import connection
            assert connection.vendor == "sqlite", connection.settings_dict

            from django.core.management import call_command
            from django.db import close_old_connections
            from django.test import override_settings
            from django.utils import timezone
            from general_manager.uploads.models import UploadIntent
            from general_manager.uploads.types import UploadIntentState

            call_command("migrate", verbosity=0, interactive=False)
            old = timezone.now() - timedelta(days=2)
            for index in range(20):
                intent = UploadIntent.objects.create(
                    token_digest=f"{index:064x}",
                    manager_name="CleanupManager",
                    field_name="file",
                    operation="create",
                    adapter_id="proxy",
                    adapter_version="1",
                    storage_fingerprint="sha256:" + "b" * 64,
                    staging_key=f"gm-staging/{index}",
                    declared_size=1,
                    declared_content_type="application/octet-stream",
                    declared_checksum_sha256="c" * 64,
                    state=UploadIntentState.REJECTED.value,
                    expires_at=old,
                    cleanup_completed_at=old,
                )
                UploadIntent.objects.filter(pk=intent.pk).update(updated_at=old)

            barrier = Barrier(2)
            def cleanup(_worker):
                close_old_connections()
                output = StringIO()
                try:
                    barrier.wait(timeout=5)
                    call_command(
                        "cleanup_upload_intents",
                        batch_size=20,
                        older_than=1,
                        stdout=output,
                    )
                    return output.getvalue()
                finally:
                    close_old_connections()

            settings = {
                "FILE_UPLOADS": {
                    "ENABLED": True,
                    "TERMINAL_RETENTION_SECONDS": 1,
                    "DOWNLOAD_URL_TTL_SECONDS": 1,
                }
            }
            with override_settings(GENERAL_MANAGER=settings):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outputs = list(executor.map(cleanup, range(2)))

            assert UploadIntent.objects.count() == 0, outputs
            totals = []
            for output in outputs:
                values = [int(value) for value in re.findall(r"=(\\d+)", output)]
                assert len(values) == 6, output
                assert sum(values) <= 20, output
                totals.append(values)
            assert sum(values[3] for values in totals) == 20, outputs
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "upload-cleanup.sqlite3")
            result = subprocess.run(  # noqa: S603
                [sys.executable, "-c", script, database_path],
                cwd=os.getcwd(),
                env=_sqlite_subprocess_environment("tests.test_settings"),
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout or "SQLite cleanup check failed")


def _sqlite_only_mark(test: object) -> Any:
    marks = [mark for mark in getattr(test, "pytestmark", ()) if mark.name == "skipif"]
    assert len(marks) == 1
    return marks[0]


def test_sqlite_cleanup_contention_tests_are_backend_scoped() -> None:
    for test in (
        test_finalizing_selection_retries_sqlite_worker_contention,
        test_terminal_claim_retries_sqlite_worker_contention,
    ):
        mark = _sqlite_only_mark(test)
        assert mark.args == (connection.vendor != "sqlite",)
        assert (
            mark.kwargs["reason"]
            == "exercises SQLite-specific locking or transaction behavior"
        )

    assert all(
        mark.name != "skipif"
        for mark in getattr(
            test_sqlite_busy_retry_preserves_exact_finalization_lease_owner,
            "pytestmark",
            (),
        )
    )


def test_sqlite_cleanup_subprocess_environment_forces_sqlite() -> None:
    child_env = _sqlite_subprocess_environment("tests.test_settings")

    assert child_env["DJANGO_SETTINGS_MODULE"] == "tests.test_settings"
    assert child_env["GENERAL_MANAGER_TEST_DATABASE"] == "sqlite"


@pytest.mark.django_db(transaction=True)
def test_terminal_rows_delete_only_after_cleanup_and_retention() -> None:
    retained = _intent(state=UploadIntentState.EXPIRED)
    deletable = _intent(state=UploadIntentState.EXPIRED)
    old = timezone.now() - timedelta(days=2)
    UploadIntent.objects.filter(pk=deletable.pk).update(
        cleanup_completed_at=old,
        updated_at=old,
    )

    counts = run_upload_cleanup(batch_size=10, older_than_seconds=1)

    assert UploadIntent.objects.filter(pk=retained.pk).exists()
    assert not UploadIntent.objects.filter(pk=deletable.pk).exists()
    assert counts.deleted == 1


@pytest.mark.django_db(transaction=True)
def test_stale_cleanup_worker_cannot_complete_a_reclaimed_lease() -> None:
    intent = _intent(state=UploadIntentState.EXPIRED)

    def reclaim(_intent, *, alias):
        UploadIntent.objects.using(alias).filter(pk=intent.pk).update(
            cleanup_lease_expires_at=timezone.now() + timedelta(hours=1)
        )

    with patch(
        "general_manager.uploads.finalization._delete_intent_owned_objects",
        side_effect=reclaim,
    ):
        assert not finalization._cleanup_terminal_intent(intent.id, alias="default")

    intent.refresh_from_db()
    assert intent.cleanup_completed_at is None


@pytest.mark.django_db(transaction=True)
def test_unowned_finalization_failure_cannot_overwrite_active_worker_lease() -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    active_lease = timezone.now() + timedelta(minutes=5)
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_lease_expires_at=active_lease,
        finalization_error_code="",
    )

    recorded = finalization._record_finalization_failure(
        intent.id,
        alias="default",
        error=OSError("hostile-secret"),
        lease_expires_at=None,
    )

    intent.refresh_from_db()
    assert recorded is False
    assert intent.cleanup_lease_expires_at == active_lease
    assert intent.finalization_error_code == ""


@pytest.mark.django_db(transaction=True)
def test_failure_cooldown_lease_cannot_be_bypassed_without_exact_owner() -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    cooldown = timezone.now() + timedelta(minutes=5)
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_lease_expires_at=cooldown,
        finalization_error_code="UPLOAD_STORAGE_ERROR",
    )

    outcome = finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    assert outcome == "skipped"
    assert intent.cleanup_lease_expires_at == cooldown
    assert intent.finalization_attempt_count == 0


@pytest.mark.django_db(transaction=True)
def test_sqlite_busy_retry_preserves_exact_finalization_lease_owner(
    monkeypatch,
) -> None:
    intent = _intent(state=UploadIntentState.FINALIZING)
    lease = timezone.now() + timedelta(minutes=1)
    expected_owner = uuid4().hex
    busy = finalization._FinalizationBusy(lease, expected_owner)
    monkeypatch.setattr(
        "general_manager.uploads.finalization.time.sleep", lambda _delay: None
    )
    with patch(
        "general_manager.uploads.finalization._finalize_upload_intent",
        side_effect=[busy, UploadIntentState.CONSUMED.value],
    ) as attempt:
        outcome = finalization.finalize_upload_intent(intent.id)

    assert outcome == UploadIntentState.CONSUMED.value
    assert attempt.call_count == 2
    assert attempt.call_args_list[1].kwargs["reconciliation_lease_expires_at"] == lease
    assert (
        attempt.call_args_list[1].kwargs["reconciliation_lease_token"] == expected_owner
    )


@pytest.mark.django_db(transaction=True)
def test_finalization_lease_resume_keeps_token_without_double_counting_attempt() -> (
    None
):
    intent = _intent(state=UploadIntentState.FINALIZING)
    first = finalization._start_finalization_attempt(
        intent.id,
        alias="default",
        expected_lease_expires_at=None,
        expected_lease_token=None,
    )
    assert first is not None

    resumed = finalization._start_finalization_attempt(
        intent.id,
        alias="default",
        expected_lease_expires_at=first[0],
        expected_lease_token=first[1],
    )

    assert resumed == first
    intent.refresh_from_db()
    assert intent.finalization_attempt_count == 1


@pytest.mark.django_db(transaction=True)
def test_cleanup_selection_uses_skip_locked_when_supported(monkeypatch) -> None:
    selected: list[dict[str, bool]] = []
    value = _intent(state=UploadIntentState.EXPIRED).id

    class FakeQuerySet:
        db = "default"

        def order_by(self, *fields):
            assert fields == ("created_at", "id")
            return self

        def select_for_update(self, **kwargs):
            selected.append(kwargs)
            return self

        def values_list(self, *fields, **kwargs):
            assert fields == ("id",)
            assert kwargs == {"flat": True}
            return self

        def __getitem__(self, value_slice):
            assert value_slice == slice(None, 1)
            return [value]

    monkeypatch.setattr(connection.features, "has_select_for_update_skip_locked", True)
    monkeypatch.setattr(connection.features, "has_select_for_update", True)

    assert finalization._cleanup_candidate_ids(FakeQuerySet(), limit=1) == [value]  # type: ignore[arg-type]
    assert selected == [{"skip_locked": True}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("target_state", ("missing", "changed", "unplanned"))
def test_consumed_cleanup_safely_resolves_unowned_old_file(
    target_state, settings
) -> None:
    settings.GENERAL_MANAGER = {
        "FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}
    }
    intent = _intent(state=UploadIntentState.CONSUMED)
    intent.old_key = "shared/old.bin"
    intent.final_key = "owned/new.bin"
    intent.final_target_pk = "1"
    intent.object_version = asdict(_VERSION)
    intent.save(
        update_fields=("old_key", "final_key", "final_target_pk", "object_version")
    )
    adapter = RecordingCleanupAdapter()

    class TargetManager:
        def using(self, alias):
            assert alias == "default"
            return self

        def get(self, **kwargs):
            assert kwargs == {"pk": 1}
            if target_state == "missing":
                raise UploadIntent.DoesNotExist
            return SimpleNamespace(avatar="other/newer.bin")

    model = SimpleNamespace(_base_manager=TargetManager())
    field = SimpleNamespace(name="avatar")
    plan = (
        None if target_state == "unplanned" else ("shared/old.bin", SimpleNamespace())
    )
    if target_state == "unplanned":
        model = SimpleNamespace(
            _base_manager=SimpleNamespace(
                using=lambda _alias: SimpleNamespace(
                    get=lambda **_kwargs: SimpleNamespace(avatar="owned/new.bin")
                )
            )
        )
    with (
        patch(
            "general_manager.uploads.finalization._plan_old_cleanup_claim",
            return_value=plan,
        ),
        patch("general_manager.uploads.finalization._parse_target_pk", return_value=1),
    ):
        finalization._delete_consumed_old_object(
            adapter,
            intent,
            model=model,  # type: ignore[arg-type]
            model_field=field,  # type: ignore[arg-type]
            alias="default",
        )

    intent.refresh_from_db()
    assert intent.old_cleanup_completed_at is not None


@pytest.mark.django_db(transaction=True)
def test_cleanup_command_validates_positive_options() -> None:
    with pytest.raises(CommandError, match="--batch-size must be a positive integer"):
        call_command("cleanup_upload_intents", batch_size=0)
    with pytest.raises(CommandError, match="--older-than must be a positive integer"):
        call_command("cleanup_upload_intents", older_than=0)


@pytest.mark.django_db(transaction=True)
def test_cleanup_command_outputs_safe_aggregate_counts_only() -> None:
    intent = _intent(state=UploadIntentState.PENDING)
    output = StringIO()
    with patch(
        "general_manager.uploads.finalization._cleanup_terminal_intent",
        return_value=False,
    ):
        call_command(
            "cleanup_upload_intents",
            batch_size=10,
            older_than=1,
            stdout=output,
        )

    rendered = output.getvalue()
    assert "expired=1" in rendered
    assert str(intent.pk) not in rendered
    assert intent.staging_key not in rendered
    assert intent.token_digest not in rendered
