"""Database-backed interface implementations configured entirely via capabilities."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Generic, Literal, Type, TypeVar, cast

from django.db import models
from django.utils import timezone

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.capabilities.orm import (
    OrmLifecycleCapability,
)
from general_manager.interface.utils.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
)
from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    FieldDescriptor,
)
from general_manager.manager.input import Input

HistoryModelT = TypeVar("HistoryModelT", bound=models.Model)


class OrmInterfaceBase(InterfaceBase, Generic[HistoryModelT]):
    """Common initialization and metadata for ORM-backed interfaces.

    Subclasses provide ORM lifecycle capabilities and a Django model type. The
    base interface defines one required public input field, ``id``, which is
    cast through ``Input(int)`` before becoming ``identification["id"]``. During
    construction, the interface stores that value as ``pk``, normalizes an
    optional historical ``search_date``, and loads the current or historical ORM
    row into the internal ``_instance`` attribute through the configured
    lifecycle capability. Subclasses that override :attr:`input_fields` must keep
    an ``"id"`` field unless they also override initialization and row loading.

    ``historical_lookup_buffer_seconds`` is consumed by ORM query support: a
    ``search_date`` must be at least this many seconds before ``timezone.now()``
    before history tables are queried instead of the current row.
    """

    _interface_type: ClassVar[str] = "database"
    _as_of_behavior: ClassVar[Literal["historical"]] = "historical"
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "orm_lifecycle"
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]]
    database: ClassVar[str | None] = None

    _model: Type[HistoryModelT]
    _active_manager: ClassVar[models.Manager[models.Model] | None] = None
    _field_descriptors: ClassVar[dict[str, "FieldDescriptor"] | None] = None

    historical_lookup_buffer_seconds: ClassVar[int] = 5
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {
        "id": cast(Input[type[object]], Input(int))
    }

    _search_date: datetime | None

    def __init__(
        self,
        *args: object,
        search_date: datetime | None = None,
        **kwargs: object,
    ) -> None:
        """Initialize the ORM-backed interface for a primary key.

        Positional and keyword inputs are parsed by :class:`InterfaceBase` using
        :attr:`input_fields`. The default shape accepts one ``id`` value. Naive
        ``search_date`` values are made timezone-aware with Django's current
        timezone before the row is loaded into the internal ``_instance`` cache.
        Subclasses overriding :attr:`input_fields` must preserve ``"id"`` unless
        they also replace this initializer.

        Args:
            *args: Raw input values consumed by ``InterfaceBase``.
            search_date: Optional point-in-time lookup date.
            **kwargs: Raw named input values consumed by ``InterfaceBase``.

        Raises:
            KeyError: If parsed identification does not contain ``"id"``.
            model.DoesNotExist: Propagated from ``get_data`` when no live or
                historical row exists for the parsed primary key.
            TypeError: Propagated from runtime values outside the typed
                ``datetime | None`` ``search_date`` contract or from capability
                lookup/configuration failures while loading ORM data.
            Exception: Propagates history capability and Django ORM lookup
                errors raised by ``get_data`` without wrapping.
        """
        super().__init__(*args, **kwargs)
        self.pk = self.identification["id"]
        self._search_date = self.normalize_search_date(search_date)
        self._instance = cast(HistoryModelT, self.get_data())

    @classmethod
    def _from_trusted_orm_instance(
        cls,
        instance: HistoryModelT,
        *,
        search_date: datetime | None = None,
    ) -> "OrmInterfaceBase[HistoryModelT]":
        """
        Build an interface from an ORM-loaded row without public input validation.

        This is an internal extension hook used by bucket/query hydration.
        Callers must only pass model or historical rows that came from Django ORM
        querysets owned by this interface. External/API/user payloads must
        continue through __init__. If a subclass replaces ``__init__``, trusted
        hydration calls that constructor with the row primary key and optional
        ``search_date``; otherwise it bypasses construction and installs the
        trusted instance, identification, primary key, and normalized search date
        directly.
        """
        pk: object = instance.pk
        if cls.__init__ is not OrmInterfaceBase.__init__:
            if search_date is None:
                return cls(pk)
            return cls(pk, search_date=search_date)

        interface = cls.__new__(cls)
        identification = {"id": pk}
        interface.identification = identification
        interface.pk = pk
        interface._search_date = cls.normalize_search_date(search_date)
        interface._instance = instance
        return interface

    @staticmethod
    def normalize_search_date(search_date: datetime | None) -> datetime | None:
        """Return ``search_date`` as a timezone-aware datetime.

        Naive values are converted with Django's current timezone. Aware values
        and ``None`` are returned unchanged.

        Args:
            search_date: Datetime to normalize, or ``None``.

        Returns:
            The timezone-aware datetime, existing aware datetime, or ``None``.
        """
        if search_date is not None and timezone.is_naive(search_date):
            search_date = timezone.make_aware(search_date)
        return search_date

    @staticmethod
    def _default_base_model_class() -> type[GeneralManagerBasisModel]:
        """Return the internal default base Django model class.

        This is an internal extension hook for interface creation; normal user
        code should configure concrete interfaces instead of calling it.
        """
        return GeneralManagerModel

    @classmethod
    def handle_custom_fields(
        cls,
        model: type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        """Return custom field names and generated ignore markers for ``model``.

        The result is delegated to the configured ``orm_lifecycle`` capability.
        The first list contains discovered custom field names. The second list
        contains generated ignore markers such as ``"<field>_value"`` and
        ``"<field>_unit"``.

        Args:
            model: Model class or model instance to inspect.

        Returns:
            ``(field_names, ignore_markers)``.

        Raises:
            CapabilityNotAvailableError: If the ORM lifecycle capability is not
                configured.
            TypeError: If the configured capability is not an
                ``OrmLifecycleCapability``.
        """
        lifecycle = cast(
            OrmLifecycleCapability,
            cls.require_capability(
                "orm_lifecycle",
                expected_type=OrmLifecycleCapability,
            ),
        )
        return lifecycle.describe_custom_fields(model)
