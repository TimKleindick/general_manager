"""Interface for integrating existing Django models with GeneralManager."""

from __future__ import annotations

from typing import ClassVar, TypeVar

from django.db import models
from general_manager.interface.orm_interface import (
    OrmInterfaceBase,
)
from general_manager.interface.bundles.database import EXISTING_MODEL_CAPABILITIES
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.capabilities.existing_model import (
    ExistingModelResolutionCapability,
)

ExistingModelT = TypeVar("ExistingModelT", bound=models.Model)


class ExistingModelInterface(OrmInterfaceBase[ExistingModelT]):
    """Interface that reuses an existing Django model instead of generating a new one."""

    _interface_type: ClassVar[str] = "existing"
    model: ClassVar[type[models.Model] | str | None] = None

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        EXISTING_MODEL_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = (
        "existing_model_resolution"
    )

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Retrieve the Python type for a named field on the wrapped Django model.

        Parameters:
            field_name (str): Name of the field on the underlying Django model.

        Returns:
            type: The Python type corresponding to the specified model field.
        """
        cls._ensure_model_loaded()
        return super().get_field_type(field_name)

    @classmethod
    def _resolve_model_class(cls) -> type[models.Model]:
        """
        Resolve and return the Django model class backing this interface.

        Returns:
            The Django model class (subclass of django.db.models.Model) used by this interface.
        """
        resolver = cls._resolution_capability()
        return resolver.resolve_model(cls)

    @classmethod
    def _resolution_capability(cls) -> ExistingModelResolutionCapability:
        """
        Retrieve the ExistingModelResolutionCapability used to resolve the underlying Django model.

        Returns:
            ExistingModelResolutionCapability: The capability instance responsible for resolving the existing model.
        """
        return cls.require_capability(  # type: ignore[return-value]
            "existing_model_resolution",
            expected_type=ExistingModelResolutionCapability,
        )

    @classmethod
    def _ensure_model_loaded(cls) -> type[models.Model]:
        """
        Lazily resolve and cache the underlying Django model class for this interface.

        If the model has not been resolved yet, obtains the configured resolution capability, resolves the model for this interface, caches it on `cls._model` and updates `cls.model`. Subsequent calls return the cached model.

        Returns:
            type[models.Model]: The resolved Django model class.
        """
        if not hasattr(cls, "_model"):
            resolver = cls._resolution_capability()
            model = resolver.resolve_model(cls)
            cls._model = model  # type: ignore[assignment]
            cls.model = model
        return cls._model
