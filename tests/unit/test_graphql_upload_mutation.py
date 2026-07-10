"""Tests for the generic GraphQL begin-file-upload mutation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import graphene
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from general_manager import bootstrap
from general_manager.api.graphql import GraphQL
from general_manager.uploads.adapters import UploadInstructions
from general_manager.uploads.graphql import BeginFileUpload
from general_manager.uploads.services import BeginFileUploadResult
from general_manager.uploads.types import UploadTransport


def _build_schema() -> graphene.Schema:
    query = type(
        "Query",
        (graphene.ObjectType,),
        {"ready": graphene.Boolean(default_value=True)},
    )
    mutation = type(
        "Mutation",
        (graphene.ObjectType,),
        {
            name: mutation_type.Field()
            for name, mutation_type in GraphQL._mutations.items()
        },
    )
    return graphene.Schema(query=query, mutation=mutation)


class GraphQLUploadMutationTests(SimpleTestCase):
    def tearDown(self) -> None:
        GraphQL.reset_registry()

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}})
    def test_disabled_feature_registers_no_mutation(self) -> None:
        GraphQL.register_file_upload_mutation()

        assert "beginFileUpload" not in GraphQL._mutations

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}})
    def test_disabled_registration_removes_only_framework_owned_entry(self) -> None:
        GraphQL._mutations["beginFileUpload"] = BeginFileUpload

        GraphQL.register_file_upload_mutation()

        assert "beginFileUpload" not in GraphQL._mutations

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}})
    def test_disabled_registration_preserves_an_unrelated_name_collision(self) -> None:
        class CustomBegin(graphene.Mutation):
            ok = graphene.Boolean()

            @staticmethod
            def mutate(root: object, info: object) -> CustomBegin:
                del root, info
                return CustomBegin(ok=True)

        GraphQL._mutations["beginFileUpload"] = CustomBegin

        GraphQL.register_file_upload_mutation()

        assert GraphQL._mutations["beginFileUpload"] is CustomBegin

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    def test_registration_is_enabled_idempotent_and_reset_safe(self) -> None:
        GraphQL.register_file_upload_mutation()
        first = GraphQL._mutations["beginFileUpload"]
        GraphQL.register_file_upload_mutation()

        assert GraphQL._mutations == {"beginFileUpload": first}

        GraphQL.reset_registry()
        GraphQL.register_file_upload_mutation()
        assert "beginFileUpload" in GraphQL._mutations

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    def test_enabled_registration_rejects_an_unmarked_name_collision(self) -> None:
        class CustomBegin(graphene.Mutation):
            ok = graphene.Boolean()

            @staticmethod
            def mutate(root: object, info: object) -> CustomBegin:
                del root, info
                return CustomBegin(ok=True)

        GraphQL._mutations["beginFileUpload"] = CustomBegin

        with pytest.raises(ValueError, match="beginFileUpload"):
            GraphQL.register_file_upload_mutation()

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    def test_schema_has_exact_begin_arguments_and_payload(self) -> None:
        GraphQL.register_file_upload_mutation()
        schema = _build_schema()
        mutation = schema.graphql_schema.mutation_type
        assert mutation is not None
        begin = mutation.fields["beginFileUpload"]

        assert set(begin.args) == {
            "manager",
            "field",
            "operation",
            "objectId",
            "filename",
            "size",
            "contentType",
            "checksum",
        }
        assert str(begin.args["size"].type) == "BigIntScalar!"
        assert str(begin.args["operation"].type) == "UploadOperation!"
        assert str(begin.args["checksum"].type) == "ChecksumInput!"
        assert set(begin.type.fields) == {
            "token",
            "transport",
            "uploadUrl",
            "method",
            "headers",
            "expiresAt",
        }
        header_type = begin.type.fields["headers"].type
        while hasattr(header_type, "of_type"):
            header_type = header_type.of_type
        assert set(header_type.fields) == {
            "name",
            "value",
        }

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    def test_mutation_uses_variables_and_returns_typed_safe_instructions(self) -> None:
        GraphQL.register_file_upload_mutation()
        schema = _build_schema()
        expires_at = timezone.now()
        result = BeginFileUploadResult(
            intent_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
            token="one-time-token",  # noqa: S106 - verifies opaque response token
            instructions=UploadInstructions(
                transport=UploadTransport.PROXY,
                method="PUT",
                url="/gm/uploads/opaque",
                headers={"X-Upload": "transfer-credential"},
            ),
            expires_at=expires_at,
        )
        query = """
            mutation Begin(
              $requestManager: String!
              $digest: String!
              $size: BigIntScalar!
            ) {
              beginFileUpload(
                manager: $requestManager
                field: "avatar"
                operation: CREATE
                filename: "avatar.png"
                size: $size
                contentType: "image/png"
                checksum: {algorithm: SHA256, digest: $digest}
              ) {
                token transport uploadUrl method
                headers { name value }
                expiresAt
              }
            }
        """

        with patch(
            "general_manager.uploads.graphql.begin_file_upload",
            return_value=result,
        ) as service:
            response = schema.execute(
                query,
                variable_values={
                    "requestManager": "UploadProfile",
                    "digest": "a" * 64,
                    "size": "3",
                },
                context_value=SimpleNamespace(user=SimpleNamespace(pk=1)),
            )

        assert response.errors is None
        assert response.data == {
            "beginFileUpload": {
                "token": "one-time-token",
                "transport": "PROXY",
                "uploadUrl": "/gm/uploads/opaque",
                "method": "PUT",
                "headers": [{"name": "X-Upload", "value": "transfer-credential"}],
                "expiresAt": expires_at.isoformat(),
            }
        }
        request = service.call_args.kwargs["request"]
        assert request.manager == "UploadProfile"
        assert request.size == 3
        assert request.checksum.digest == "a" * 64

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    def test_direct_put_returns_only_executable_headers_without_a_stage_key(
        self,
    ) -> None:
        GraphQL.register_file_upload_mutation()
        schema = _build_schema()
        expires_at = timezone.now()
        result = BeginFileUploadResult(
            intent_id=UUID("00000000-0000-0000-0000-000000000002"),
            token="direct-one-time-token",  # noqa: S106 - opaque response token
            instructions=UploadInstructions(
                transport=UploadTransport.DIRECT,
                method="PUT",
                url="https://signed.example.test/upload?signature=secret",
                headers={
                    "Content-Type": "image/png",
                    "x-amz-checksum-sha256": "signed-checksum",
                },
            ),
            expires_at=expires_at,
        )

        with patch(
            "general_manager.uploads.graphql.begin_file_upload",
            return_value=result,
        ):
            response = schema.execute(
                """
                mutation Begin($manager: String!, $digest: String!, $size: BigIntScalar!) {
                  beginFileUpload(
                    manager: $manager
                    field: "avatar"
                    operation: CREATE
                    filename: "avatar.png"
                    size: $size
                    contentType: "image/png"
                    checksum: {algorithm: SHA256, digest: $digest}
                  ) {
                    transport uploadUrl method headers { name value }
                  }
                }
                """,
                variable_values={
                    "manager": "UploadProfile",
                    "digest": "a" * 64,
                    "size": "3",
                },
                context_value=SimpleNamespace(user=SimpleNamespace(pk=1)),
            )

        assert response.errors is None
        payload = response.data["beginFileUpload"]  # type: ignore[index]
        assert payload == {
            "transport": "DIRECT",
            "uploadUrl": "https://signed.example.test/upload?signature=secret",
            "method": "PUT",
            "headers": [
                {"name": "Content-Type", "value": "image/png"},
                {
                    "name": "x-amz-checksum-sha256",
                    "value": "signed-checksum",
                },
            ],
        }
        assert "gm-staging" not in str(payload)

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    def test_expected_upload_errors_have_safe_stable_graphql_codes(self) -> None:
        GraphQL.register_file_upload_mutation()
        schema = _build_schema()
        response = schema.execute(
            """
            mutation Begin(
              $manager: String!
              $digest: String!
              $size: BigIntScalar!
            ) {
              beginFileUpload(
                manager: $manager
                field: "avatar"
                operation: CREATE
                filename: "avatar.png"
                size: $size
                contentType: "image/png"
                checksum: {algorithm: SHA256, digest: $digest}
              ) { token }
            }
            """,
            variable_values={
                "manager": "SecretManagerName",
                "digest": "a" * 64,
                "size": "3",
            },
            context_value=SimpleNamespace(user=AnonymousUser()),
        )

        assert response.data == {"beginFileUpload": None}
        assert response.errors is not None
        error = response.errors[0]
        assert error.extensions == {"code": "UNAUTHENTICATED"}
        assert "SecretManagerName" not in error.message

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
    @patch.object(bootstrap, "add_graphql_url")
    def test_bootstrap_registers_upload_root_without_manager_crud_mutations(
        self,
        add_graphql_url: object,
    ) -> None:
        del add_graphql_url

        bootstrap.handle_graph_ql([])

        assert GraphQL._mutation_class is not None
        schema = GraphQL.get_schema()
        assert schema is not None
        mutation = schema.graphql_schema.mutation_type
        assert mutation is not None
        assert "beginFileUpload" in mutation.fields
