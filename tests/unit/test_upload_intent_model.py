"""Tests for durable file upload intents and opaque tokens."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import timedelta
from importlib import import_module
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import IntegrityError, migrations, models, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from general_manager.uploads.models import UploadIntent, UploadQuotaLock
from general_manager.uploads.tokens import (
    digest_upload_token,
    issue_upload_token,
    verify_upload_token,
)
from general_manager.uploads.types import UploadIntentState, UploadOperation


def _intent_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "token_digest": "a" * 64,
        "manager_name": "Profile",
        "field_name": "avatar",
        "operation": UploadOperation.CREATE,
        "adapter_id": "django-proxy",
        "adapter_version": "1",
        "storage_fingerprint": "storage:primary",
        "staging_key": "uploads/staging/intent/avatar.png",
        "original_filename": "avatar.png",
        "declared_size": 3,
        "declared_content_type": "image/png",
        "declared_checksum_sha256": "b" * 64,
        "expires_at": timezone.now() + timedelta(minutes=15),
    }
    data.update(overrides)
    return data


def test_upload_tokens_are_nondeterministic_and_store_sha256_digests() -> None:
    """Issue independent high-entropy tokens and deterministic SHA-256 digests."""
    first_token, first_digest = issue_upload_token()
    second_token, second_digest = issue_upload_token()

    assert first_token != second_token
    assert first_digest != second_digest
    assert len(first_token) >= 43
    assert len(first_digest) == 64
    assert first_digest == digest_upload_token(first_token)
    assert first_token != first_digest


@pytest.mark.parametrize("invalid", [None, b"token", object(), "", "not-a-digest"])
def test_upload_token_verification_safely_rejects_invalid_values(
    invalid: object,
) -> None:
    """Treat malformed tokens and digests as non-matches without raising."""
    token, digest = issue_upload_token()

    assert verify_upload_token(invalid, digest) is False
    assert verify_upload_token(token, invalid) is False


class UploadIntentModelTests(TestCase):
    """Exercise upload intent persistence against the configured database."""

    @classmethod
    def setUpTestData(cls) -> None:
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="upload-owner")

    def make_intent(self, **overrides: Any) -> UploadIntent:
        return UploadIntent.objects.create(
            user=self.user,
            **_intent_data(**overrides),
        )

    def test_quota_lock_is_seeded_and_constrained_to_one_fixed_row(self) -> None:
        """Keep global admission serialization independent of user ordering."""
        lock, _created = UploadQuotaLock.objects.get_or_create(pk=1)

        assert lock.generation == 0
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UploadQuotaLock.objects.create(pk=2)

    def test_intent_stores_only_token_digest_and_matches_raw_token(self) -> None:
        """Persist a one-way digest while accepting only the issued raw token."""
        token, digest = issue_upload_token()
        intent = self.make_intent(token_digest=digest)
        persisted = UploadIntent.objects.values().get(pk=intent.pk)

        assert persisted["token_digest"] == digest
        assert token not in {str(value) for value in persisted.values()}
        assert intent.matches_token(token)
        assert not intent.matches_token(token + "x")
        assert not intent.matches_token(None)

    def test_defaults_choices_indexes_and_swappable_user_are_stable(self) -> None:
        """Expose enum-derived choices and operational persistence defaults."""
        intent = self.make_intent()

        assert intent.state == UploadIntentState.PENDING
        assert intent.object_version == {}
        assert intent.finalization_attempt_count == 0
        assert intent.finalization_error_code == ""
        assert intent.target_id is None
        assert intent.final_key is None
        assert intent.old_key is None
        assert intent.transfer_lease_expires_at is None
        assert intent.uploaded_at is None
        assert intent.consumed_at is None

        operation = UploadIntent._meta.get_field("operation")
        state = UploadIntent._meta.get_field("state")
        assert tuple(value for value, _label in operation.choices) == tuple(
            member.value for member in UploadOperation
        )
        assert tuple(value for value, _label in state.choices) == tuple(
            member.value for member in UploadIntentState
        )
        assert {tuple(index.fields) for index in UploadIntent._meta.indexes} == {
            ("state", "expires_at"),
            ("user", "state"),
        }
        assert (
            UploadIntent._meta.get_field("user").remote_field.model is get_user_model()
        )

    def test_user_field_avoids_reverse_relation_and_redundant_index(self) -> None:
        """Keep the swappable user relation nullable, private, and index-efficient."""
        user_field = UploadIntent._meta.get_field("user")

        assert user_field.null is True
        assert user_field.blank is True
        assert user_field.remote_field.on_delete is models.SET_NULL
        assert user_field.remote_field.related_name == "+"
        assert user_field.db_index is False

    def test_canonical_target_id_round_trips_beyond_255_characters(self) -> None:
        """Persist long serialized canonical IDs without a varchar ceiling."""
        target_id = "canonical:" + "segment/" * 80
        intent = self.make_intent(target_id=target_id)
        intent.refresh_from_db()

        assert isinstance(UploadIntent._meta.get_field("target_id"), models.TextField)
        assert len(target_id) > 255
        assert intent.target_id == target_id

    def test_database_rejects_negative_sizes_and_attempt_counts(self) -> None:
        """Enforce non-negative byte counts and retry counters in the database."""
        invalid_values = (
            {"declared_size": -1},
            {"verified_size": -1},
            {"finalization_attempt_count": -1},
        )

        for invalid in invalid_values:
            with self.subTest(invalid=invalid), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self.make_intent(**invalid)

    def test_string_representations_do_not_expose_sensitive_metadata(self) -> None:
        """Keep digests, object keys, and checksums out of logs and debugging."""
        sensitive_values = {
            "token_digest": "digest-secret-" + "a" * 50,
            "staging_key": "secret/staging/key",
            "final_key": "secret/final/key",
            "old_key": "secret/old/key",
            "declared_checksum_sha256": "declared-checksum-secret",
            "verified_checksum_sha256": "verified-checksum-secret",
        }
        intent = self.make_intent(**sensitive_values)

        representations = f"{intent!s}\n{intent!r}"
        assert str(intent.pk) in representations
        assert intent.state in representations
        for value in sensitive_values.values():
            assert value not in representations

    def test_expiry_and_terminal_state_checks_accept_explicit_time(self) -> None:
        """Classify expiry boundaries and all terminal enum states."""
        now = timezone.now()
        intent = self.make_intent(expires_at=now)

        assert intent.is_expired(at=now)
        assert not intent.is_expired(at=now - timedelta(microseconds=1))
        assert not intent.is_terminal

        for state in (
            UploadIntentState.CONSUMED,
            UploadIntentState.SUPERSEDED,
            UploadIntentState.REJECTED,
            UploadIntentState.EXPIRED,
        ):
            intent.state = state
            assert intent.is_terminal

        for state in (
            UploadIntentState.PENDING,
            UploadIntentState.TRANSFERRING,
            UploadIntentState.UPLOADED,
            UploadIntentState.FINALIZING,
        ):
            intent.state = state
            assert not intent.is_terminal

    def test_finalizing_intent_retains_stage_and_recovery_metadata(self) -> None:
        """Persist enough immutable metadata to recover interrupted finalization."""
        object_version = {
            "version_id": "version-7",
            "etag": "etag-7",
            "checksum_sha256": "c" * 64,
            "size": 3,
        }
        intent = self.make_intent(
            operation=UploadOperation.UPDATE,
            target_id="profile:7",
            state=UploadIntentState.FINALIZING,
            final_key="profiles/7/avatar.png",
            old_key="profiles/7/old-avatar.png",
            verified_size=3,
            verified_content_type="image/png",
            verified_checksum_sha256="c" * 64,
            object_version=object_version,
            finalization_error_code="COPY_INTERRUPTED",
            finalization_attempt_count=2,
        )
        intent.refresh_from_db()

        assert intent.state == UploadIntentState.FINALIZING
        assert intent.staging_key == "uploads/staging/intent/avatar.png"
        assert intent.final_key == "profiles/7/avatar.png"
        assert intent.old_key == "profiles/7/old-avatar.png"
        assert intent.object_version == object_version
        assert intent.finalization_error_code == "COPY_INTERRUPTED"
        assert intent.finalization_attempt_count == 2

    def test_migration_depends_on_swappable_user_and_latest_app_migration(
        self,
    ) -> None:
        """Keep migration ordering safe for projects with custom user models."""
        migration = import_module(
            "general_manager.migrations.0007_upload_intent"
        ).Migration

        assert ("general_manager", "0006_chat_pending_confirmation_scoped_ids") in (
            migration.dependencies
        )
        assert migrations.swappable_dependency(settings.AUTH_USER_MODEL) in (
            migration.dependencies
        )


class UploadIntentSwappableUserMigrationTests(SimpleTestCase):
    """Verify upload intent migration behavior in an isolated Django process."""

    def test_migration_resolves_custom_user_foreign_key(self) -> None:
        """Apply 0007 with a custom user and inspect its actual database FK."""
        script = textwrap.dedent(
            """
            import django

            django.setup()

            from django.contrib.auth import get_user_model
            from django.core.management import call_command
            from django.db import connection, models
            from general_manager.uploads.models import UploadIntent, UploadQuotaLock

            call_command("migrate", run_syncdb=True, verbosity=0, interactive=False)

            upload_intent = UploadIntent
            custom_user = get_user_model()
            user_field = upload_intent._meta.get_field("user")

            assert user_field.remote_field.model is custom_user
            assert user_field.null is True
            assert user_field.remote_field.on_delete is models.SET_NULL
            assert upload_intent._meta.db_table in connection.introspection.table_names()
            assert UploadQuotaLock.objects.filter(pk=1, generation=0).exists()

            owner = custom_user.objects.create_user(username="deleted-upload-owner")
            intent = UploadIntent.objects.create(
                user=owner,
                token_digest="a" * 64,
                manager_name="Profile",
                field_name="avatar",
                operation="create",
                adapter_id="proxy",
                adapter_version="1",
                storage_fingerprint="sha256:" + "b" * 64,
                staging_key="uploads/staging/intent/avatar.png",
                original_filename="avatar.png",
                declared_size=3,
                declared_content_type="image/png",
                declared_checksum_sha256="c" * 64,
                expires_at=django.utils.timezone.now(),
            )
            intent_id = intent.pk
            owner.delete()
            persisted = UploadIntent.objects.get(pk=intent_id)
            assert persisted.user is None
            assert persisted.staging_key == "uploads/staging/intent/avatar.png"

            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor,
                    upload_intent._meta.db_table,
                )
            foreign_keys = {
                value["foreign_key"]
                for value in constraints.values()
                if value["foreign_key"] is not None
            }
            assert (
                custom_user._meta.db_table,
                custom_user._meta.pk.column,
            ) in foreign_keys
            """
        )
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tests.swappable_user_settings",
            "PYTHONPATH": os.pathsep.join(
                (
                    os.path.join(os.getcwd(), "src"),
                    os.getcwd(),
                    os.environ.get("PYTHONPATH", ""),
                )
            ),
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
            self.fail(result.stderr or result.stdout or "custom user migration failed")
