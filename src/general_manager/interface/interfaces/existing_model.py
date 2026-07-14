"""Interface for integrating existing Django models with GeneralManager."""

from __future__ import annotations

from typing import ClassVar, TypeVar, cast

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
type ExistingInterfaceClass = type["ExistingModelInterface[models.Model]"]

__all__ = ["ExistingModelInterface"]


class ExistingModelInterface(OrmInterfaceBase[ExistingModelT]):
    """Writable ORM interface backed by a pre-existing Django model.

    Subclasses declare ``model`` as either a Django model class or an app-label
    string accepted by ``django.apps.apps.get_model()``, such as
    ``settings.AUTH_USER_MODEL``. During manager class creation the
    ``existing_model_resolution`` lifecycle capability resolves that reference,
    caches the resolved model on the concrete interface, auto-registers
    database-aware history when needed, applies interface rules, and builds a
    factory for the legacy model. Auto-registration includes local many-to-many
    fields. A pre-registered tracker remains compatible on the default database;
    on a configured non-default alias, its generated history model must carry
    GeneralManager's database-aware marker or manager creation raises
    ``UnsafeHistoryConfigurationError``. Interface rules are the optional
    ``Meta.rules`` sequence on the interface. Soft delete is enabled when the
    resolved model exposes an ``is_active`` attribute.

    Construction and row loading are inherited from ``OrmInterfaceBase``: the
    default public input is the wrapped row ``id`` parsed by ``Input(int)`` plus
    optional ``search_date: datetime | None``. Naive search dates are made aware
    with Django's current timezone, and historical lookups follow
    ``OrmInterfaceBase.historical_lookup_buffer_seconds``. Missing rows
    propagate the wrapped model's ``DoesNotExist`` exception. Invalid or missing
    model declarations raise the existing-model configuration errors from the
    resolution capability. Creation, update, delete, query, history, and
    factory APIs are inherited from the configured writable ORM capabilities and
    the owning ``GeneralManager`` class; this shell class only selects the
    existing-model lifecycle.
    """

    _interface_type: ClassVar[str] = "existing"
    model: ClassVar[type[models.Model] | str | None] = None

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        EXISTING_MODEL_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = (
        "existing_model_resolution"
    )

    @classmethod
    def get_field_type(cls, field_name: str) -> type[object]:
        """Retrieve the effective type for a wrapped model attribute.

        The method first ensures the legacy model has been resolved and cached
        on this interface class, then delegates to the inherited read-capability
        lookup. Resolution is class-local: subclasses that declare their own
        ``model`` do not reuse a parent interface's cached ``_model`` value.
        Stored Django fields return the Django field class. Managed relations
        are relation fields whose related model exposes
        ``_general_manager_class``; those return that manager class. Generated
        descriptors are entries in ORM support's descriptor map, including
        custom fields and generated relation helpers; those return their
        descriptor metadata ``"type"`` value.

        Args:
            field_name: Name of the field or manager attribute to inspect.

        Returns:
            The effective field, manager, or descriptor type exposed for
            ``field_name``.

        Raises:
            MissingModelConfigurationError: If no ``model`` is configured.
            InvalidModelReferenceError: If ``model`` is not a Django model class
                or resolvable app-label string.
            FieldDoesNotExist: If neither the wrapped model nor generated
                descriptors can describe the field.
            CapabilityNotAvailableError: If required capabilities are absent.
            TypeError: If the configured resolution capability is not an
                ``ExistingModelResolutionCapability`` or the read capability is
                incompatible.
        """
        cls._ensure_model_loaded()
        return cast(type[object], super().get_field_type(field_name))

    @classmethod
    def _resolve_model_class(cls) -> type[models.Model]:
        """Resolve, cache, and return the Django model backing this interface.

        This is an internal lifecycle helper. Public code normally declares
        ``model`` on an ``ExistingModelInterface`` subclass and lets
        ``GeneralManager`` class creation call the lifecycle capability.

        Returns:
            The resolved Django model class.

        Raises:
            MissingModelConfigurationError: If no ``model`` is configured.
            InvalidModelReferenceError: If ``model`` is not a Django model class
                or resolvable app-label string.
            TypeError: If the configured lifecycle capability is not an
                ``ExistingModelResolutionCapability``.
        """
        resolver = cls._resolution_capability()
        return resolver.resolve_model(cast(ExistingInterfaceClass, cls))

    @classmethod
    def _resolution_capability(cls) -> ExistingModelResolutionCapability:
        """Return the lifecycle capability that resolves existing models.

        The capability is required under the ``"existing_model_resolution"``
        name and must be an ``ExistingModelResolutionCapability`` instance.

        Returns:
            The configured existing-model resolution capability.

        Raises:
            CapabilityNotAvailableError: If the capability is not configured.
            TypeError: If the configured capability has the wrong runtime type.
        """
        return cast(
            ExistingModelResolutionCapability,
            cls.require_capability(
                "existing_model_resolution",
                expected_type=ExistingModelResolutionCapability,
            ),
        )

    @classmethod
    def _ensure_model_loaded(cls) -> type[ExistingModelT]:
        """Return this interface's resolved model, resolving it if needed.

        The cache is checked on ``cls.__dict__`` rather than through
        ``hasattr()`` so subclasses with their own ``model`` declaration resolve
        their own model instead of inheriting a parent interface's cached model.
        The resolution capability stores both ``_model`` and ``model`` on the
        class. No database query is performed by this helper.

        Returns:
            The resolved Django model class for this exact interface class.

        Raises:
            MissingModelConfigurationError: If no ``model`` is configured.
            InvalidModelReferenceError: If ``model`` is not a Django model class
                or resolvable app-label string.
            TypeError: If the configured lifecycle capability is incompatible.
        """
        if "_model" not in cls.__dict__:
            resolver = cls._resolution_capability()
            model = resolver.resolve_model(cast(ExistingInterfaceClass, cls))
            cls._model = cast(type[ExistingModelT], model)
            cls.model = model
        return cls._model
