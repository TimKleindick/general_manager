"""Excel-backed interface shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.bundles.excel import EXCEL_CAPABILITIES

if TYPE_CHECKING:
    from general_manager.bucket.base_bucket import Bucket
    from general_manager.interface.capabilities.base import CapabilityName
    from general_manager.interface.capabilities.configuration import (
        CapabilityConfigEntry,
    )
    from general_manager.interface.excel import (
        ExcelField,
        ExcelMeta,
        ExcelRowSnapshot,
        ExcelSyncDelta,
    )
    from general_manager.manager.input import Input


class _ExcelSyncHandler(Protocol):
    def sync_from_excel(
        self,
        interface_cls: type["ExcelInterface"],
        *,
        force: bool = False,
    ) -> ExcelSyncDelta: ...


class _ExcelQueryHandler(Protocol):
    def filter(
        self,
        interface_cls: type["ExcelInterface"],
        **kwargs: Any,
    ) -> Bucket[Any]: ...

    def exclude(
        self,
        interface_cls: type["ExcelInterface"],
        **kwargs: Any,
    ) -> Bucket[Any]: ...

    def all(self, interface_cls: type["ExcelInterface"]) -> Bucket[Any]: ...


class ExcelInterface(InterfaceBase):
    """Collaborative Excel-backed interface with an in-memory mirror."""

    _interface_type: ClassVar[str] = "excel"
    input_fields: ClassVar[dict[str, Input[Any]]]
    excel_meta: ClassVar[ExcelMeta]
    excel_fields: ClassVar[dict[str, ExcelField[Any]]]
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        EXCEL_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "excel_lifecycle"
    _excel_observed_snapshot: ExcelRowSnapshot | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._excel_observed_snapshot = self._initial_excel_observed_snapshot()

    def _initial_excel_observed_snapshot(self) -> ExcelRowSnapshot | None:
        from general_manager.interface.excel_store import DEFAULT_EXCEL_STORE

        key = self.identification[self.excel_meta.key]
        lock = DEFAULT_EXCEL_STORE.lock_for(self.excel_meta.workbook)
        with lock:
            mirror = DEFAULT_EXCEL_STORE.mirror_for(type(self))
            return mirror.rows.get(key)

    @classmethod
    def sync_from_excel(cls, *, force: bool = False) -> ExcelSyncDelta:
        handler = cast(_ExcelSyncHandler, cls.require_capability("excel_sync"))
        return handler.sync_from_excel(cls, force=force)

    @classmethod
    def filter(cls, **kwargs: Any) -> Bucket[Any]:
        handler = cast(_ExcelQueryHandler, cls.require_capability("query"))
        return handler.filter(cls, **kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Any]:
        handler = cast(_ExcelQueryHandler, cls.require_capability("query"))
        return handler.exclude(cls, **kwargs)

    @classmethod
    def all(cls) -> Bucket[Any]:
        handler = cast(_ExcelQueryHandler, cls.require_capability("query"))
        return handler.all(cls)
