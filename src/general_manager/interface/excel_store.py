"""Disposable Django-cache snapshots and filesystem coordination for Excel."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any
from weakref import WeakKeyDictionary

from django.core.cache import caches
from filelock import FileLock

from general_manager.interface.excel import ExcelRowSnapshot, ExcelSyncDelta
from general_manager.interface.excel_workbook import WorkbookFingerprint
from general_manager.logging import get_logger

logger = get_logger("interface.excel")
_locks: dict[str, FileLock] = {}
_locks_guard = Lock()


@dataclass(slots=True)
class ExcelMirror:
    rows: dict[Any, ExcelRowSnapshot] = field(default_factory=dict)
    fingerprint: WorkbookFingerprint | None = None
    structure_error: Exception | None = None


def _schema_value(value: Any) -> str:
    if callable(value):
        return f"{getattr(value, '__module__', type(value).__module__)}.{getattr(value, '__qualname__', type(value).__qualname__)}"
    return repr(value)


def mirror_cache_key(interface_cls: Any) -> str:
    """Stable across processes; field schemas and workbook locations cannot collide."""
    meta = asdict(interface_cls.excel_meta)
    meta["workbook"] = str(Path(meta["workbook"]).resolve())
    field_schema = []
    for name, excel_field in sorted(interface_cls.excel_fields.items()):
        attributes = {
            definition.name: getattr(excel_field, definition.name)
            for definition in fields(excel_field)
        }
        attributes.update(getattr(excel_field, "__dict__", {}))
        field_schema.append(
            (
                name,
                _schema_value(type(excel_field)),
                tuple(
                    (key, _schema_value(value))
                    for key, value in sorted(attributes.items())
                ),
            )
        )
    identity = (
        interface_cls.__module__,
        interface_cls.__qualname__,
        sorted(meta.items()),
        field_schema,
    )
    digest = sha256(repr(identity).encode()).hexdigest()
    return f"general_manager:excel:v1:{digest}"


class ExcelWorkbookStore:
    """Use shared snapshots when available, retaining local state for graceful fallback."""

    def __init__(self) -> None:
        self._mirrors: WeakKeyDictionary[type, ExcelMirror] = WeakKeyDictionary()

    def lock_for(self, workbook: str) -> FileLock:
        """Return the filesystem lock coordinating access to a workbook path."""
        # A persistent sidecar survives atomic replacement of the xlsx inode.
        path = str(Path(workbook).resolve()) + ".gm.lock"
        with _locks_guard:
            return _locks.setdefault(path, FileLock(path, timeout=30))

    def mirror_for(self, interface_cls: Any) -> ExcelMirror:
        """Return the local mirror, refreshing it from shared cache when available."""
        mirror = self._mirrors.setdefault(interface_cls, ExcelMirror())
        try:
            cached = caches[interface_cls.excel_meta.cache_alias].get(
                mirror_cache_key(interface_cls)
            )
            if (
                isinstance(cached, ExcelMirror)
                and cached.fingerprint != mirror.fingerprint
            ):
                snapshot = deepcopy(cached)
                mirror.rows = snapshot.rows
                mirror.fingerprint = snapshot.fingerprint
                mirror.structure_error = None
        except Exception:
            # Cache storage is an optimization, never workbook write authorization.
            logger.exception("Excel mirror cache read failed; using workbook.")
        return mirror

    def replace(
        self,
        interface_cls: Any,
        *,
        rows: dict[Any, ExcelRowSnapshot],
        fingerprint: WorkbookFingerprint,
        publish: bool = True,
    ) -> ExcelSyncDelta:
        """Replace an interface mirror and optionally publish it to the cache."""
        mirror = self._mirrors.setdefault(interface_cls, ExcelMirror())
        old_rows = mirror.rows
        created = tuple(row for key, row in rows.items() if key not in old_rows)
        updated = tuple(
            (old_rows[key], row)
            for key, row in rows.items()
            if key in old_rows and old_rows[key].fingerprint != row.fingerprint
        )
        deleted = tuple(row for key, row in old_rows.items() if key not in rows)
        mirror.rows = dict(rows)
        mirror.fingerprint = fingerprint
        mirror.structure_error = None
        if publish:
            self.publish(interface_cls)
        return ExcelSyncDelta(created=created, updated=updated, deleted=deleted)

    def publish(self, interface_cls: Any) -> None:
        """Publish the current interface mirror to the configured cache."""
        mirror = self._mirrors[interface_cls]
        try:
            caches[interface_cls.excel_meta.cache_alias].set(
                mirror_cache_key(interface_cls), deepcopy(mirror), timeout=None
            )
        except Exception:
            logger.exception("Excel mirror cache write failed; using local snapshot.")

    def set_error(self, interface_cls: type, error: Exception) -> None:
        """Record a workbook structure error on the local mirror."""
        self._mirrors.setdefault(interface_cls, ExcelMirror()).structure_error = error


DEFAULT_EXCEL_STORE = ExcelWorkbookStore()


def row_fingerprint(values: dict[str, Any]) -> str:
    """Return a stable fingerprint for a parsed Excel row."""
    payload = repr(sorted(values.items())).encode("utf-8")
    return sha256(payload).hexdigest()
