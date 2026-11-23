"""Short-lived workbook read snapshots for exact, authorized query operands."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from threading import get_ident
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from general_manager.interface.excel import ExcelMeta, ExcelRowSnapshot
    from general_manager.interface.excel_workbook import WorkbookFingerprint
    from general_manager.interface.interfaces.excel import ExcelInterface
    from general_manager.manager.general_manager import GeneralManager


@dataclass
class _OperandRead:
    interface: ExcelInterface
    snapshot: ExcelRowSnapshot
    owner: int
    active: bool = True


_CURRENT: ContextVar[_OperandRead | None] = ContextVar(
    "_excel_query_operand_snapshot", default=None
)


def snapshot_for_excel_query_operand(
    interface: ExcelInterface,
) -> ExcelRowSnapshot | None:
    """Return a synchronized row during a single exact query-operand read.

    Custom Excel readers may opt in only when their ordinary reads use these same
    rows, with no extra per-read validation, external effects or reentrant work.
    They still own defensive copying and observation on a hit. A miss must run the
    existing read path. Do not use this in sync, writes, or conflict detection.
    """
    read = _CURRENT.get()
    return (
        read.snapshot
        if read is not None
        and read.active
        and read.owner == get_ident()
        and read.interface is interface
        else None
    )


def read_excel_operand(
    manager: GeneralManager, field: str, snapshot: ExcelRowSnapshot
) -> object:
    """Use the actual getter, but never expose the snapshot to comparisons."""
    from general_manager.interface.interfaces.excel import ExcelInterface

    interface = manager._interface
    if not isinstance(interface, ExcelInterface):
        return getattr(manager, field)
    read = _OperandRead(interface, snapshot, get_ident())
    token = _CURRENT.set(read)
    try:
        return getattr(manager, field)
    finally:
        read.active = False
        _CURRENT.reset(token)


def collect_excel_operand_rows(
    interface: type[ExcelInterface],
    instances: Iterable[GeneralManager],
    source: ExcelMeta,
    fingerprint: WorkbookFingerprint,
    fields: frozenset[str],
) -> dict[int, ExcelRowSnapshot] | None:
    """Refresh once, then retain only exact authorized rows in that generation."""
    from filelock import Timeout

    from general_manager.interface.excel import ExcelStructureError
    from general_manager.interface.excel_store import DEFAULT_EXCEL_STORE

    try:
        # Respect the installed sync's ordering/locking contract. Do not nest a
        # custom sync under a lock not owned by that implementation.
        interface.sync_from_excel()
        with DEFAULT_EXCEL_STORE.lock_for(source.workbook):
            if interface.excel_meta is not source:
                return None
            mirror = DEFAULT_EXCEL_STORE.mirror_for(interface)
            if mirror.structure_error is not None or mirror.fingerprint != fingerprint:
                return None
            rows = mirror.rows
            index = {}
            for manager in instances:
                instance = manager._interface
                if type(instance) is not interface:
                    return None
                key = instance.identification[interface.excel_meta.key]
                if key not in rows:
                    return None
                snapshot = rows[key]
                if any(
                    field not in snapshot.values
                    or id(type(snapshot.values[field])) not in _PASSIVE_VALUES
                    for field in fields
                ):
                    return None
                index[id(manager)] = snapshot
            return index
    except (ExcelStructureError, OSError, Timeout):
        # Ordinary getters remain solely responsible for error presentation.
        return None


_PASSIVE_VALUES = frozenset(map(id, (str, int, bool, float, type(None))))


def excel_operand_revision_is_current(
    interface: type[ExcelInterface], source: ExcelMeta, fingerprint: WorkbookFingerprint
) -> bool:
    """Detect external edits during local predicate execution before using it."""
    from general_manager.interface.excel_workbook import workbook_fingerprint

    try:
        return (
            interface.excel_meta is source
            and workbook_fingerprint(source.workbook) == fingerprint
            and interface.excel_meta is source
        )
    except OSError:
        return False
