"""Database-backed interface implementations configured entirely via capabilities."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Generic, Type, TypeVar, cast

from django.db import models
from django.utils import timezone

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.capabilities.orm import (
    OrmHistoryCapability,
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
    """Common initialization + metadata shared by ORM-backed interfaces."""

    _interface_type: ClassVar[str] = "database"
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "orm_lifecycle"
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]]
    database: ClassVar[str | None] = None

    _model: Type[HistoryModelT]
    _active_manager: ClassVar[models.Manager[models.Model] | None] = None
    _field_descriptors: ClassVar[dict[str, "FieldDescriptor"] | None] = None

    historical_lookup_buffer_seconds: ClassVar[int] = 5
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

    _search_date: datetime | None

    def __init__(
        self,
        *args: object,
        search_date: datetime | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.pk = self.identification["id"]
        self._search_date = self.normalize_search_date(search_date)
        self._instance: HistoryModelT = self.get_data()

    @staticmethod
    def normalize_search_date(search_date: datetime | None) -> datetime | None:
        """Ensure `search_date` is timezone-aware when provided."""
        if search_date is not None and timezone.is_naive(search_date):
            search_date = timezone.make_aware(search_date)
        return search_date

    @staticmethod
    def _default_base_model_class() -> type[GeneralManagerBasisModel]:
        return GeneralManagerModel

    @classmethod
    def handle_custom_fields(
        cls,
        model: type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        """Expose custom-field metadata through the lifecycle capability."""
        lifecycle = cast(
            OrmLifecycleCapability,
            cls.require_capability(
                "orm_lifecycle",
                expected_type=OrmLifecycleCapability,
            ),
        )
        return lifecycle.describe_custom_fields(model)
