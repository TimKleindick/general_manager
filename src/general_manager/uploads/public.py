"""Supported extension points for GraphQL file upload storage adapters."""

from __future__ import annotations

from django.core.files.storage import Storage

from general_manager.uploads.adapters import UploadAdapterFactory


class _InvalidStorageClassError(TypeError):
    def __init__(self) -> None:
        super().__init__(
            "storage_class must be a django.core.files.storage.Storage subclass."
        )


class _InvalidAdapterFactoryError(TypeError):
    def __init__(self) -> None:
        super().__init__("factory must be callable.")


def register_upload_adapter(
    storage_class: type[Storage],
    factory: UploadAdapterFactory,
) -> None:
    """Register one process-wide adapter factory for a Django storage class.

    Registration is intended to happen during application startup, before any
    upload intents are created. The most-specific registered storage class wins;
    registering the same class twice is rejected by the registry.
    """

    if not isinstance(storage_class, type) or not issubclass(storage_class, Storage):
        raise _InvalidStorageClassError
    if not callable(factory):
        raise _InvalidAdapterFactoryError

    # Import lazily so this public extension seam does not force upload models or
    # Django's app registry to initialize when ``general_manager.api`` is imported.
    from general_manager.uploads.services import upload_adapter_registry

    upload_adapter_registry.register(storage_class, factory)
