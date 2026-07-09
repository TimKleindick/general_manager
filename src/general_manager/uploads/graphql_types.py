"""Stable GraphQL schema types for uploaded and stored files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphql import Undefined
from graphql.language import StringValueNode

from general_manager.uploads.types import StoredFileStatus

if TYPE_CHECKING:

    class _GrapheneMountedType:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

    class Scalar(_GrapheneMountedType):
        """Typed stand-in for Graphene's untyped scalar base."""

    class ObjectType:
        """Typed stand-in for Graphene's untyped object base."""

    class Enum:
        """Typed stand-in for Graphene's untyped enum base."""

        @classmethod
        def from_enum(cls, enum: type[StoredFileStatus]) -> type[Enum]: ...

    class Field(_GrapheneMountedType): ...

    class String(_GrapheneMountedType): ...

    class Int(_GrapheneMountedType): ...

    class DateTime(_GrapheneMountedType): ...

else:
    from graphene import (  # type: ignore[import-untyped]
        DateTime,
        Enum,
        Field,
        Int,
        ObjectType,
        Scalar,
        String,
    )


class _UploadTokenTypeError(TypeError):
    """Raised when an upload token is not a string."""

    def __init__(self) -> None:
        super().__init__("UploadToken must be a string.")


class _EmptyUploadTokenError(ValueError):
    """Raised when an upload token is empty."""

    def __init__(self) -> None:
        super().__init__("UploadToken must not be empty.")


class UploadToken(Scalar):
    """Opaque, non-empty upload token accepted by generated mutations."""

    @staticmethod
    def parse_value(value: object) -> str:
        return UploadToken._validate(value)

    @staticmethod
    def parse_literal(node: object, _variables: object = None) -> str | object:
        if not isinstance(node, StringValueNode):
            return Undefined
        return UploadToken._validate(node.value)

    @staticmethod
    def _validate(value: object) -> str:
        if not isinstance(value, str):
            raise _UploadTokenTypeError
        if value == "":
            raise _EmptyUploadTokenError
        return value


StoredFileStatusEnum = Enum.from_enum(StoredFileStatus)


class StoredFile(ObjectType):
    """Client-visible metadata for a stored file."""

    name = String(required=True)
    size = Int()
    content_type = String()
    download_url = String()
    expires_at = DateTime()
    status = Field(StoredFileStatusEnum, required=True)


class StoredImage(StoredFile):
    """Stored-file metadata with optional image dimensions."""

    width = Int()
    height = Int()
