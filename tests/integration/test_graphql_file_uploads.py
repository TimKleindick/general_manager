# type: ignore
"""End-to-end coverage for generated GraphQL file uploads and downloads."""

from __future__ import annotations

import hashlib
import base64
from io import BytesIO
from pathlib import Path
import tempfile
from typing import Any, ClassVar, cast
from unittest.mock import patch
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db.models import CharField, FileField, ImageField
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.test import override_settings

from general_manager import api
from general_manager.api.graphql import GraphQL
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.uploads.adapters import (
    ExactPublicDownloadAdapter,
    ProxyUploadAdapter,
    UploadAdapter,
    UploadInstructions,
)
from general_manager.uploads.config import FileUploadPolicy
from general_manager.uploads.errors import UploadStorageError
from general_manager.uploads.finalization import finalize_upload_intent
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.types import (
    ObjectVersion,
    UploadIntentState,
    UploadTransport,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1Pe"
    "AAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class _DirectStorage(FileSystemStorage):
    """Filesystem-backed test storage advertised as direct-capable."""


class _DirectRaceAdapter(ProxyUploadAdapter):
    adapter_id = "tests.direct-race"
    adapter_version = 1
    versions: ClassVar[dict[str, str]] = {}
    replace_on_open: ClassVar[bool] = False
    replacement_body: ClassVar[bytes] = b"replacement-race"
    events: ClassVar[list[str]] = []

    @property
    def supports_public_urls(self) -> bool:
        return True

    @classmethod
    def supports_direct(cls, storage: object) -> bool:
        return isinstance(storage, _DirectStorage)

    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return UploadInstructions(
            transport=UploadTransport.DIRECT,
            method="PUT",
            url="https://uploads.example.test/exact-version",
            headers={"Content-Type": "text/plain"},
        )

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        type(self).events.append(f"inspect:{self.versions.get(stage_key)}")
        inspected = super().inspect_staged(stage_key)
        version_id = self.versions.get(stage_key)
        if version_id is None:
            return inspected
        return ObjectVersion(
            version_id=version_id,
            etag=inspected.etag,
            checksum_sha256=inspected.checksum_sha256,
            size=inspected.size,
            content_type="text/plain",
        )

    def open_stage(self, stage_key: str, version: ObjectVersion) -> BytesIO:
        type(self).events.append(f"open:{self.versions.get(stage_key)}")
        if self.versions.get(stage_key) != version.version_id:
            raise UploadStorageError()
        opened = super().open_stage(stage_key, version)
        try:
            data = opened.read()
        finally:
            opened.close()
        if type(self).replace_on_open:
            type(self).replace_on_open = False
            self.storage.delete(stage_key)
            saved = self.storage.save(
                stage_key,
                ContentFile(type(self).replacement_body, name=stage_key),
            )
            assert saved == stage_key
            type(self).versions[stage_key] = "v2"
        return BytesIO(data)

    def materialize(
        self,
        stage_key: str,
        version: ObjectVersion,
        final_key: str,
        *,
        intent_id: UUID,
    ) -> str:
        if self.inspect_staged(stage_key) != version:
            type(self).events.append("materialize:mismatch")
            raise UploadStorageError()
        type(self).events.append("materialize:copy")
        return super().materialize(
            stage_key,
            version,
            final_key,
            intent_id=intent_id,
        )

    def inspect_materialized(
        self,
        final_key: str,
        source_version: ObjectVersion,
        *,
        intent_id: UUID,
    ) -> ObjectVersion:
        type(self).events.append("inspect-final")
        inspected = super().inspect_materialized(
            final_key,
            source_version,
            intent_id=intent_id,
        )
        return ObjectVersion(
            version_id=source_version.version_id,
            etag=inspected.etag,
            checksum_sha256=inspected.checksum_sha256,
            size=inspected.size,
            content_type=source_version.content_type,
        )

    def public_download_url(
        self,
        key: str,
        *,
        version: ObjectVersion,
    ) -> str:
        return f"https://cdn.test/{key}?versionId={version.version_id}"


def test_public_api_exports_upload_policy_and_adapter_registration() -> None:
    from general_manager.uploads.errors import UploadObjectMissingError

    assert api.FileUploadPolicy is FileUploadPolicy
    assert callable(api.register_upload_adapter)
    assert api.UploadAdapter is UploadAdapter
    assert api.ExactPublicDownloadAdapter is ExactPublicDownloadAdapter
    assert api.UploadObjectMissingError is UploadObjectMissingError

    class CustomStorage(FileSystemStorage):
        pass

    from general_manager.uploads.services import upload_adapter_registry

    previous = dict(upload_adapter_registry._registrations)
    try:
        api.register_upload_adapter(
            CustomStorage,
            lambda storage: ProxyUploadAdapter(storage),
        )
        resolved = upload_adapter_registry.resolve(CustomStorage())
        assert isinstance(resolved, ProxyUploadAdapter)
    finally:
        with upload_adapter_registry._lock:
            upload_adapter_registry._registrations.clear()
            upload_adapter_registry._registrations.update(previous)


def test_public_adapter_registration_rejects_invalid_storage_classes() -> None:
    with pytest.raises(TypeError, match=r"django\.core\.files\.storage\.Storage"):
        api.register_upload_adapter(str, lambda storage: ProxyUploadAdapter(storage))


class GraphQLFileUploadIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise the browser-visible workflow through the generated schema."""

    media_directory: ClassVar[tempfile.TemporaryDirectory[str]]
    storage: ClassVar[FileSystemStorage]
    direct_storage: ClassVar[_DirectStorage]
    upload_to_calls: ClassVar[list[tuple[str, str]]]

    @classmethod
    def setUpClass(cls) -> None:
        # The GeneralManager test metaclass builds the schema before Django's
        # class-level override_settings hook, so enable uploads explicitly for
        # that bootstrap window and restore them in tearDownClass.
        cls._upload_settings_override = override_settings(
            GENERAL_MANAGER={
                "FILE_UPLOADS": {
                    "ENABLED": True,
                    "ALLOW_INSECURE_HTTP": True,
                    "MAX_BEGIN_ATTEMPTS_PER_USER": 100,
                    "MAX_BEGIN_ATTEMPTS_GLOBAL": 1000,
                    "MAX_TRANSFER_ATTEMPTS_PER_USER": 100,
                    "MAX_TRANSFER_ATTEMPTS_GLOBAL": 1000,
                }
            }
        )
        cls._upload_settings_override.enable()
        cls.media_directory = tempfile.TemporaryDirectory(
            prefix="general-manager-graphql-upload-"
        )
        cls.storage = FileSystemStorage(location=Path(cls.media_directory.name))
        cls.direct_storage = _DirectStorage(
            location=Path(cls.media_directory.name) / "direct"
        )
        from general_manager.uploads.services import upload_adapter_registry

        cls._upload_adapter_registry = upload_adapter_registry
        cls._previous_upload_adapter_registrations = dict(
            upload_adapter_registry._registrations
        )
        api.register_upload_adapter(
            _DirectStorage,
            lambda storage: _DirectRaceAdapter(storage),
        )
        cls.upload_to_calls = []

        def avatar_upload_to(instance: object, filename: str) -> str:
            title = str(cast(Any, instance).title)
            cls.upload_to_calls.append((title, filename))
            return f"profiles/{title.lower().replace(' ', '-')}/{filename}"

        class UploadProfile(GeneralManager):
            class Interface(DatabaseInterface):
                title = CharField(max_length=100)
                document = FileField(
                    storage=cls.storage,
                    upload_to="documents/",
                    blank=True,
                )
                avatar = ImageField(
                    storage=cls.storage,
                    upload_to=avatar_upload_to,
                    blank=True,
                )
                direct_file = FileField(
                    storage=cls.direct_storage,
                    upload_to="direct/",
                    blank=True,
                )

                class Meta:
                    app_label = "general_manager"

            class FileUploads:
                fields: ClassVar[dict[str, FileUploadPolicy]] = {
                    "avatar": FileUploadPolicy(
                        allowed_content_types=("image/png",),
                        allowed_extensions=(".png",),
                    ),
                    "direct_file": FileUploadPolicy(
                        allowed_content_types=("text/plain",),
                        allowed_extensions=(".txt",),
                        content_inspector=lambda _inspection: "text/plain",
                        public=True,
                    ),
                }

        cls.UploadProfile = UploadProfile
        cls.general_manager_classes = [UploadProfile]

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            super().tearDownClass()
        finally:
            cls.media_directory.cleanup()
            with cls._upload_adapter_registry._lock:
                cls._upload_adapter_registry._registrations.clear()
                cls._upload_adapter_registry._registrations.update(
                    cls._previous_upload_adapter_registrations
                )
            GraphQL.reset_registry()
            cls._upload_settings_override.disable()

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(20)
        self.user = get_user_model().objects.create_user(
            username=f"upload-owner-{get_random_string(8)}",
            password=password,
        )
        self.client.force_login(self.user)

    def _begin(
        self,
        *,
        field: str,
        filename: str,
        body: bytes,
        content_type: str = "image/png",
        operation: str = "CREATE",
        object_id: str | None = None,
        manager: str = "UploadProfile",
    ) -> dict[str, object]:
        response = self._begin_response(
            field=field,
            filename=filename,
            body=body,
            content_type=content_type,
            operation=operation,
            object_id=object_id,
            manager=manager,
        )
        self.assertResponseNoErrors(response)
        return response.json()["data"]["beginFileUpload"]

    def _begin_response(
        self,
        *,
        field: str,
        filename: str,
        body: bytes,
        content_type: str = "image/png",
        operation: str = "CREATE",
        object_id: str | None = None,
        manager: str = "UploadProfile",
    ) -> HttpResponse:
        return self.query(
            """
            mutation BeginUpload(
              $manager: String!
              $field: String!
              $filename: String!
              $size: BigIntScalar!
              $digest: String!
              $contentType: String!
              $operation: UploadOperation!
              $objectId: ID
            ) {
              beginFileUpload(
                manager: $manager
                field: $field
                operation: $operation
                objectId: $objectId
                filename: $filename
                size: $size
                contentType: $contentType
                checksum: {algorithm: SHA256, digest: $digest}
              ) {
                token transport uploadUrl method
                headers { name value }
              }
            }
            """,
            variables={
                "field": field,
                "filename": filename,
                "size": str(len(body)),
                "digest": hashlib.sha256(body).hexdigest(),
                "contentType": content_type,
                "operation": operation,
                "objectId": object_id,
                "manager": manager,
            },
        )

    def _transfer(
        self,
        begun: dict[str, object],
        body: bytes,
        *,
        content_type: str = "image/png",
    ) -> None:
        headers = {
            f"HTTP_{entry['name'].upper().replace('-', '_')}": entry["value"]
            for entry in begun["headers"]
        }
        response = self.client.put(
            str(begun["uploadUrl"]),
            data=body,
            content_type=content_type,
            **headers,
        )
        self.assertEqual(response.status_code, 204, response.content)

    def test_begin_proxy_create_query_and_signed_get_head_download(self) -> None:
        begun = self._begin(field="avatar", filename="portrait.png", body=_PNG)
        self.assertEqual(begun["transport"], "PROXY")
        self.assertEqual(begun["method"], "PUT")
        self._transfer(begun, _PNG)

        created = self.query(
            """
            mutation CreateUploadProfile($avatar: UploadToken!) {
              createUploadProfile(title: "Ada Lovelace", avatar: $avatar) {
                success
                UploadProfile {
                  id title
                  avatar { name contentType size width height status downloadUrl expiresAt }
                  document { name status }
                }
              }
            }
            """,
            variables={"avatar": begun["token"]},
        )
        self.assertResponseNoErrors(created)
        payload = created.json()["data"]["createUploadProfile"]
        self.assertTrue(payload["success"])
        profile = payload["UploadProfile"]
        self.assertIsNone(profile["document"])
        self.assertEqual(profile["avatar"]["name"], "portrait.png")
        self.assertEqual(profile["avatar"]["contentType"], "image/png")
        self.assertEqual(profile["avatar"]["size"], str(len(_PNG)))
        self.assertEqual(profile["avatar"]["width"], 1)
        self.assertEqual(profile["avatar"]["height"], 1)
        self.assertEqual(profile["avatar"]["status"], "AVAILABLE")
        self.assertIsNotNone(profile["avatar"]["expiresAt"])
        self.assertEqual(self.upload_to_calls[0][0], "Ada Lovelace")

        queried = self.query(
            """
            query Profile($id: ID!) {
              uploadProfile(id: $id) {
                id title
                avatar { name status downloadUrl expiresAt }
              }
            }
            """,
            variables={"id": profile["id"]},
        )
        self.assertResponseNoErrors(queried)
        avatar = queried.json()["data"]["uploadProfile"]["avatar"]
        download_url = avatar["downloadUrl"]
        self.assertNotIn("profiles/", download_url)

        head = self.client.head(download_url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head["X-Content-Type-Options"], "nosniff")
        downloaded = self.client.get(download_url)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b"".join(downloaded.streaming_content), _PNG)

        replay = self.query(
            """
            mutation Replay($avatar: UploadToken!) {
              createUploadProfile(title: "Replay", avatar: $avatar) { success }
            }
            """,
            variables={"avatar": begun["token"]},
        )
        self.assertResponseHasErrors(replay)
        self.assertEqual(
            replay.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_ALREADY_CONSUMED",
        )
        self.assertEqual(
            UploadIntent.objects.get(original_filename="portrait.png").state,
            UploadIntentState.CONSUMED.value,
        )

    def test_file_field_and_update_omission_are_distinct_from_explicit_null(
        self,
    ) -> None:
        body = b"%PDF-1.4\nminimal integration fixture\n%%EOF\n"
        begun = self._begin(
            field="document",
            filename="notes.pdf",
            body=body,
            content_type="application/pdf",
        )
        self._transfer(begun, body, content_type="application/pdf")
        created = self.query(
            """
            mutation Create($document: UploadToken!) {
              createUploadProfile(title: "Document owner", document: $document) {
                success
                UploadProfile { id document { name contentType status } }
              }
            }
            """,
            variables={"document": begun["token"]},
        )
        self.assertResponseNoErrors(created)
        profile = created.json()["data"]["createUploadProfile"]["UploadProfile"]
        self.assertEqual(profile["document"]["name"], "notes.pdf")
        self.assertEqual(profile["document"]["contentType"], "application/pdf")

        omitted = self.query(
            """
            mutation Rename($id: Int!) {
              updateUploadProfile(id: $id, title: "Renamed") {
                success
                UploadProfile { id title document { name status } }
              }
            }
            """,
            variables={"id": profile["id"]},
        )
        self.assertResponseNoErrors(omitted)
        unchanged = omitted.json()["data"]["updateUploadProfile"]["UploadProfile"]
        self.assertEqual(unchanged["document"]["name"], "notes.pdf")

        cleared = self.query(
            """
            mutation Clear($id: Int!) {
              updateUploadProfile(id: $id, document: null) {
                success
                UploadProfile { id document { name } }
              }
            }
            """,
            variables={"id": profile["id"]},
        )
        self.assertResponseNoErrors(cleared)
        self.assertIsNone(
            cleared.json()["data"]["updateUploadProfile"]["UploadProfile"]["document"]
        )

    def test_owner_field_operation_target_expiry_and_quota_are_enforced(self) -> None:
        unknown_manager = self._begin_response(
            manager="MissingUploadManager",
            field="avatar",
            filename="unknown.png",
            body=_PNG,
        )
        self.assertResponseHasErrors(unknown_manager)
        self.assertEqual(
            unknown_manager.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_MANAGER_INVALID",
        )

        begun = self._begin(field="avatar", filename="bound.png", body=_PNG)
        self._transfer(begun, _PNG)

        other = get_user_model().objects.create_user(
            username=f"upload-other-{get_random_string(8)}"
        )
        self.client.force_login(other)
        wrong_owner = self.query(
            """
            mutation WrongOwner($avatar: UploadToken!) {
              createUploadProfile(title: "Wrong owner", avatar: $avatar) { success }
            }
            """,
            variables={"avatar": begun["token"]},
        )
        self.assertResponseHasErrors(wrong_owner)
        self.assertEqual(
            wrong_owner.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_TOKEN_INVALID",
        )

        self.client.force_login(self.user)
        wrong_field = self.query(
            """
            mutation WrongField($document: UploadToken!) {
              createUploadProfile(title: "Wrong field", document: $document) { success }
            }
            """,
            variables={"document": begun["token"]},
        )
        self.assertResponseHasErrors(wrong_field)
        self.assertEqual(
            wrong_field.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_BINDING_MISMATCH",
        )

        target = self.UploadProfile.create(
            creator_id=self.user.pk,
            title="Existing",
            ignore_permission=True,
        )
        wrong_operation = self.query(
            """
            mutation WrongOperation($id: Int!, $avatar: UploadToken!) {
              updateUploadProfile(id: $id, avatar: $avatar) { success }
            }
            """,
            variables={"id": target.id, "avatar": begun["token"]},
        )
        self.assertResponseHasErrors(wrong_operation)
        self.assertEqual(
            wrong_operation.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_BINDING_MISMATCH",
        )

        other_target = self.UploadProfile.create(
            creator_id=self.user.pk,
            title="Other target",
            ignore_permission=True,
        )
        update_bound = self._begin(
            field="avatar",
            filename="update-bound.png",
            body=_PNG,
            operation="UPDATE",
            object_id=str(target.id),
        )
        self._transfer(update_bound, _PNG)
        wrong_target = self.query(
            """
            mutation WrongTarget($id: Int!, $avatar: UploadToken!) {
              updateUploadProfile(id: $id, avatar: $avatar) { success }
            }
            """,
            variables={"id": other_target.id, "avatar": update_bound["token"]},
        )
        self.assertResponseHasErrors(wrong_target)
        self.assertEqual(
            wrong_target.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_BINDING_MISMATCH",
        )

        intent = UploadIntent.objects.get(original_filename="bound.png")
        intent.expires_at = timezone.now()
        intent.save(update_fields=("expires_at", "updated_at"))
        expired = self.query(
            """
            mutation Expired($avatar: UploadToken!) {
              createUploadProfile(title: "Expired", avatar: $avatar) { success }
            }
            """,
            variables={"avatar": begun["token"]},
        )
        self.assertResponseHasErrors(expired)
        self.assertEqual(
            expired.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_EXPIRED",
        )

        with override_settings(
            GENERAL_MANAGER={
                "FILE_UPLOADS": {
                    "ENABLED": True,
                    "ALLOW_INSECURE_HTTP": True,
                    # The unexpired update-bound intent above still counts;
                    # admit one more, then reject the next.
                    "MAX_PENDING_INTENTS_PER_USER": 2,
                    "MAX_PENDING_BYTES_PER_USER": 10_000,
                    "MAX_PENDING_INTENTS_GLOBAL": 100,
                    "MAX_PENDING_BYTES_GLOBAL": 100_000,
                    "MAX_BEGIN_ATTEMPTS_PER_USER": 100,
                    "MAX_BEGIN_ATTEMPTS_GLOBAL": 1000,
                }
            }
        ):
            first = self._begin_response(
                field="document",
                filename="pending-one.txt",
                body=b"one",
                content_type="text/plain",
            )
            self.assertResponseNoErrors(first)
            second = self._begin_response(
                field="document",
                filename="pending-two.txt",
                body=b"two",
                content_type="text/plain",
            )
        self.assertResponseHasErrors(second)
        self.assertEqual(
            second.json()["errors"][0]["extensions"]["code"],
            "UPLOAD_QUOTA_EXCEEDED",
        )

    def test_replacement_failure_is_pollable_retryable_and_invalidates_old_url(
        self,
    ) -> None:
        original = self._begin(field="avatar", filename="original.png", body=_PNG)
        self._transfer(original, _PNG)
        created = self.query(
            """
            mutation Create($avatar: UploadToken!) {
              createUploadProfile(title: "Replace me", avatar: $avatar) {
                success
                UploadProfile { id avatar { downloadUrl status } }
              }
            }
            """,
            variables={"avatar": original["token"]},
        )
        self.assertResponseNoErrors(created)
        profile = created.json()["data"]["createUploadProfile"]["UploadProfile"]
        old_url = profile["avatar"]["downloadUrl"]
        model = self.UploadProfile.Interface._model
        old_key = model.objects.get(pk=profile["id"]).avatar.name

        replacement = self._begin(
            field="avatar",
            filename="replacement.png",
            body=_PNG,
            operation="UPDATE",
            object_id=str(profile["id"]),
        )
        self._transfer(replacement, _PNG)
        with patch.object(
            ProxyUploadAdapter,
            "materialize",
            side_effect=UploadStorageError(),
        ):
            updated = self.query(
                """
                mutation Replace($id: Int!, $avatar: UploadToken!) {
                  updateUploadProfile(id: $id, avatar: $avatar) {
                    success
                    UploadProfile { id avatar { name status downloadUrl } }
                  }
                }
                """,
                variables={"id": profile["id"], "avatar": replacement["token"]},
            )
        self.assertResponseNoErrors(updated)
        failed = updated.json()["data"]["updateUploadProfile"]["UploadProfile"]
        self.assertEqual(failed["avatar"]["status"], "FAILED")
        self.assertIsNone(failed["avatar"]["downloadUrl"])
        self.assertEqual(self.client.get(old_url).status_code, 404)
        self.assertTrue(self.storage.exists(old_key))

        intent = UploadIntent.objects.get(original_filename="replacement.png")
        self.assertEqual(intent.state, UploadIntentState.FINALIZING.value)
        self.assertEqual(intent.finalization_error_code, "UPLOAD_STORAGE_ERROR")
        intent.cleanup_lease_expires_at = None
        intent.cleanup_lease_token = ""
        intent.save(
            update_fields=(
                "cleanup_lease_expires_at",
                "cleanup_lease_token",
                "updated_at",
            )
        )
        self.assertEqual(
            finalize_upload_intent(intent.id),
            "consumed",
        )
        polled = self.query(
            """
            query Poll($id: ID!) {
              uploadProfile(id: $id) {
                avatar { name status downloadUrl expiresAt }
              }
            }
            """,
            variables={"id": profile["id"]},
        )
        self.assertResponseNoErrors(polled)
        avatar = polled.json()["data"]["uploadProfile"]["avatar"]
        self.assertEqual(avatar["status"], "AVAILABLE")
        self.assertIsNotNone(avatar["downloadUrl"])
        self.assertTrue(self.storage.exists(old_key))

    def test_fake_direct_adapter_rejects_a_staged_version_replacement_race(
        self,
    ) -> None:
        body = b"immutable direct version"
        begun = self._begin(
            field="direct_file",
            filename="race.txt",
            body=body,
            content_type="text/plain",
        )
        self.assertEqual(begun["transport"], "DIRECT")
        intent = UploadIntent.objects.get(original_filename="race.txt")
        saved = self.direct_storage.save(
            intent.staging_key,
            ContentFile(body, name=intent.staging_key),
        )
        self.assertEqual(saved, intent.staging_key)
        _DirectRaceAdapter.versions[intent.staging_key] = "v1"
        _DirectRaceAdapter.events.clear()
        _DirectRaceAdapter.replace_on_open = True

        created = self.query(
            """
            mutation Direct($file: UploadToken!) {
              createUploadProfile(title: "Direct race", directFile: $file) {
                success
                UploadProfile { id directFile { name status downloadUrl } }
              }
            }
            """,
            variables={"file": begun["token"]},
        )
        self.assertResponseNoErrors(created)
        profile = created.json()["data"]["createUploadProfile"]["UploadProfile"]
        self.assertEqual(profile["directFile"]["status"], "FAILED")
        self.assertIsNone(profile["directFile"]["downloadUrl"])
        intent.refresh_from_db()
        self.assertEqual(intent.state, UploadIntentState.FINALIZING.value)
        self.assertEqual(
            _DirectRaceAdapter.versions[intent.staging_key],
            "v2",
            intent.finalization_error_code,
        )
        self.assertFalse(self.direct_storage.exists(intent.final_key))

        self.direct_storage.delete(intent.staging_key)
        restored = self.direct_storage.save(
            intent.staging_key,
            ContentFile(body, name=intent.staging_key),
        )
        self.assertEqual(restored, intent.staging_key)
        _DirectRaceAdapter.versions[intent.staging_key] = "v1"
        intent.cleanup_lease_expires_at = None
        intent.cleanup_lease_token = ""
        intent.save(
            update_fields=(
                "cleanup_lease_expires_at",
                "cleanup_lease_token",
                "updated_at",
            )
        )
        self.assertEqual(
            finalize_upload_intent(intent.id),
            "consumed",
            _DirectRaceAdapter.events,
        )
        intent.refresh_from_db()
        self.assertEqual(intent.state, UploadIntentState.CONSUMED.value)
        queried = self.query(
            """
            query DirectPublic($id: ID!) {
              uploadProfile(id: $id) {
                directFile { status downloadUrl expiresAt }
              }
            }
            """,
            variables={"id": profile["id"]},
        )
        self.assertResponseNoErrors(queried)
        direct_file = queried.json()["data"]["uploadProfile"]["directFile"]
        self.assertEqual(direct_file["status"], "AVAILABLE")
        self.assertIn("?versionId=v1", direct_file["downloadUrl"])
        self.assertIsNone(direct_file["expiresAt"])

    def test_permission_denial_happens_before_orm_claim(self) -> None:
        begun = self._begin(field="avatar", filename="denied.png", body=_PNG)
        self._transfer(begun, _PNG)
        model = self.UploadProfile.Interface._model

        with patch.object(
            self.UploadProfile.Permission,
            "check_create_permission",
            side_effect=PermissionError("denied by integration policy"),
        ) as permission:
            denied = self.query(
                """
                mutation Denied($avatar: UploadToken!) {
                  createUploadProfile(title: "Denied", avatar: $avatar) { success }
                }
                """,
                variables={"avatar": begun["token"]},
            )

        self.assertResponseHasErrors(denied)
        permission.assert_called_once()
        self.assertEqual(model.objects.count(), 0)
        intent = UploadIntent.objects.get(original_filename="denied.png")
        self.assertEqual(intent.state, UploadIntentState.UPLOADED.value)
