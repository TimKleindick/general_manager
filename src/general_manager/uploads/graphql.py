"""Graphene contract for beginning one secure file upload."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import cast, TYPE_CHECKING

import graphene
from graphql import GraphQLError

from general_manager.api.graphql_errors import BigIntScalar
from general_manager.uploads.config import get_file_upload_settings
from general_manager.uploads.errors import UploadError, stable_upload_error
from general_manager.uploads.services import (
    BeginFileUploadRequest,
    UploadChecksum,
    begin_file_upload,
)
from general_manager.uploads.types import (
    ChecksumAlgorithm,
    UploadOperation,
    UploadTransport,
)

if TYPE_CHECKING:
    from graphene import ResolveInfo


BEGIN_FILE_UPLOAD_MUTATION_NAME = "beginFileUpload"
_FILE_UPLOAD_MUTATION_MARKER = "_general_manager_file_upload_mutation"


class FileUploadMutationRegistrationError(ValueError):
    """Raised when an application mutation occupies the reserved upload name."""

    def __init__(self) -> None:
        super().__init__(
            "The GraphQL mutation name 'beginFileUpload' is reserved for file uploads."
        )


UploadOperationEnum = graphene.Enum.from_enum(
    UploadOperation,
    name="UploadOperation",
)
ChecksumAlgorithmEnum = graphene.Enum.from_enum(
    ChecksumAlgorithm,
    name="ChecksumAlgorithm",
)
UploadTransportEnum = graphene.Enum.from_enum(
    UploadTransport,
    name="UploadTransport",
)


class ChecksumInput(graphene.InputObjectType):  # type: ignore[misc]
    """Required SHA-256 declaration for the bytes to be transferred."""

    algorithm = ChecksumAlgorithmEnum(required=True)
    digest = graphene.String(required=True)


class UploadHeader(graphene.ObjectType):  # type: ignore[misc]
    """One client-safe request header required by the transfer adapter."""

    name = graphene.String(required=True)
    value = graphene.String(required=True)


class BeginFileUpload(graphene.Mutation):  # type: ignore[misc]
    """Create one owner-, manager-, field-, and operation-bound upload intent."""

    class Arguments:
        manager = graphene.String(required=True)
        field = graphene.String(required=True)
        operation = UploadOperationEnum(required=True)
        object_id = graphene.ID()
        filename = graphene.String(required=True)
        size = BigIntScalar(required=True)
        content_type = graphene.String(required=True)
        checksum = graphene.Argument(ChecksumInput, required=True)

    token = graphene.String(required=True)
    transport = graphene.Field(UploadTransportEnum, required=True)
    upload_url = graphene.String(required=True)
    method = graphene.String(required=True)
    headers = graphene.List(graphene.NonNull(UploadHeader), required=True)
    expires_at = graphene.DateTime(required=True)
    _general_manager_file_upload_mutation = True

    @staticmethod
    def mutate(
        _root: object,
        info: ResolveInfo,
        *,
        manager: str,
        field: str,
        operation: UploadOperation | str,
        filename: str,
        size: int,
        content_type: str,
        checksum: object,
        object_id: object | None = None,
    ) -> BeginFileUpload:
        """Delegate validation/persistence and map only expected safe errors."""

        checksum_algorithm = (
            checksum.get("algorithm")
            if isinstance(checksum, Mapping)
            else getattr(checksum, "algorithm", None)
        )
        checksum_digest = (
            checksum.get("digest")
            if isinstance(checksum, Mapping)
            else getattr(checksum, "digest", None)
        )
        graphql_error: GraphQLError | None = None
        try:
            result = begin_file_upload(
                user=getattr(info.context, "user", None),
                request=BeginFileUploadRequest(
                    manager=manager,
                    field=field,
                    operation=operation,
                    object_id=object_id,
                    filename=filename,
                    size=size,
                    content_type=content_type,
                    checksum=UploadChecksum(
                        algorithm=cast(
                            ChecksumAlgorithm | str,
                            checksum_algorithm,
                        ),
                        digest=cast(str, checksum_digest),
                    ),
                ),
            )
        except UploadError as error:
            public_error = stable_upload_error(error)
            graphql_error = GraphQLError(
                public_error.default_message,
                extensions={"code": public_error.code},
            )

        if graphql_error is not None:
            # Raise outside the handler so GraphQL never retains the service
            # exception (or an unsafe chain supplied by a custom integration).
            raise graphql_error

        instructions = result.instructions
        return BeginFileUpload(
            token=result.token,
            transport=instructions.transport,
            upload_url=instructions.url,
            method=instructions.method,
            headers=[
                UploadHeader(name=name, value=value)
                for name, value in instructions.headers.items()
            ],
            expires_at=result.expires_at,
        )


def register_file_upload_mutation(
    mutations: MutableMapping[str, type[graphene.Mutation]],
) -> None:
    """Idempotently synchronize the framework-owned mutation registration."""

    existing = mutations.get(BEGIN_FILE_UPLOAD_MUTATION_NAME)
    owned = bool(
        existing is not None
        and getattr(existing, _FILE_UPLOAD_MUTATION_MARKER, False) is True
    )
    if not get_file_upload_settings().enabled:
        if owned:
            mutations.pop(BEGIN_FILE_UPLOAD_MUTATION_NAME, None)
        return
    if existing is not None and not owned:
        raise FileUploadMutationRegistrationError
    mutations[BEGIN_FILE_UPLOAD_MUTATION_NAME] = BeginFileUpload
