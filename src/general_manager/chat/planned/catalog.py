"""Validation and normalization for application manager catalogs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any, cast

from django.utils.module_loading import import_string

from general_manager.chat.settings import ChatConfigurationError


_REQUIRED_FIELDS = ("domain", "aliases", "use_when", "distinguish_from")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC = re.compile(r"(?:_|[^\w])+", re.UNICODE)
_SOURCE_INVALID = "catalog must be a mapping or importable callable."
_SOURCE_CALL_FAILED = "catalog callable could not be loaded."
_SOURCE_NOT_MAPPING = "catalog must be a mapping or callable returning a mapping."
_NON_EMPTY_TEXT = "catalog {field} must be a non-empty string."
_SEQUENCE_REQUIRED = "catalog {field} must be a sequence."
_ALIASES_INVALID = "catalog aliases must contain non-empty strings."
_DISTINGUISH_INVALID = "catalog distinguish_from must contain manager names."
_SELF_DISTINGUISH = "catalog manager cannot distinguish itself."
_DISTINGUISH_HIDDEN = "catalog distinguish_from target {target!r} is not chat-exposed."
_MANAGER_NAME_INVALID = "catalog manager names must be non-empty strings."
_MANAGER_HIDDEN = "catalog manager {manager_name!r} is not chat-exposed in the schema."
_ENTRY_MAPPING = "catalog entry for {manager_name!r} must be a mapping."
_ENTRY_MISSING_FIELDS = (
    "catalog entry for {manager_name!r} is missing fields: {fields}."
)


@dataclass(frozen=True)
class ManagerCatalogEntry:
    """Normalized metadata used to rank one chat-exposed manager."""

    domain: str
    aliases: tuple[str, ...]
    use_when: str
    distinguish_from: tuple[str, ...]


@dataclass(frozen=True)
class ManagerCatalog:
    """Immutable validated catalog and its canonical content fingerprint."""

    entries: Mapping[str, ManagerCatalogEntry]
    fingerprint: str


def normalize_match_text(value: str) -> str:
    """Normalize free text into comparable case-insensitive words."""
    spaced = _CAMEL_BOUNDARY.sub(" ", value)
    return " ".join(_NON_ALPHANUMERIC.sub(" ", spaced).casefold().split())


def _error(detail: str) -> ChatConfigurationError:
    return ChatConfigurationError.invalid_planned_settings(detail)


def _load_source(source: object) -> Mapping[object, object]:
    if source is None:
        return {}
    resolved = source
    if isinstance(source, str):
        try:
            resolved = import_string(source)
        except Exception as exc:
            raise _error(_SOURCE_INVALID) from exc
        if not callable(resolved):
            raise _error(_SOURCE_INVALID)
    if callable(resolved):
        try:
            resolved = resolved()
        except Exception as exc:
            raise _error(_SOURCE_CALL_FAILED) from exc
    if not isinstance(resolved, Mapping):
        raise _error(_SOURCE_NOT_MAPPING)
    return cast(Mapping[object, object], resolved)


def _normalized_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _error(_NON_EMPTY_TEXT.format(field=field))
    normalized = " ".join(value.split())
    if not normalized:
        raise _error(_NON_EMPTY_TEXT.format(field=field))
    return normalized


def _sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _error(_SEQUENCE_REQUIRED.format(field=field))
    return value


def _normalize_aliases(value: object) -> tuple[str, ...]:
    aliases: set[str] = set()
    for alias in _sequence(value, "aliases"):
        if not isinstance(alias, str):
            raise _error(_ALIASES_INVALID)
        normalized = normalize_match_text(alias)
        if not normalized:
            raise _error(_ALIASES_INVALID)
        aliases.add(normalized)
    return tuple(sorted(aliases))


def _normalize_distinguish_from(
    value: object,
    manager_name: str,
    exposed_names: set[str],
) -> tuple[str, ...]:
    targets: set[str] = set()
    for target in _sequence(value, "distinguish_from"):
        if not isinstance(target, str) or not target:
            raise _error(_DISTINGUISH_INVALID)
        if target == manager_name:
            raise _error(_SELF_DISTINGUISH)
        if target not in exposed_names:
            raise _error(_DISTINGUISH_HIDDEN.format(target=target))
        targets.add(target)
    return tuple(sorted(targets))


def _canonical_fingerprint(entries: Mapping[str, ManagerCatalogEntry]) -> str:
    payload = {
        manager: {
            "aliases": entry.aliases,
            "distinguish_from": entry.distinguish_from,
            "domain": entry.domain,
            "use_when": entry.use_when,
        }
        for manager, entry in sorted(entries.items())
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def load_manager_catalog(
    source: object,
    schema_index: Mapping[str, Mapping[str, Any]],
) -> ManagerCatalog:
    """Load a catalog without changing the live chat schema index.

    Validation intentionally trusts only ``schema_index`` membership. Catalog
    metadata can improve ranking, but cannot expose a hidden manager.
    """
    raw_entries = _load_source(source)
    exposed_names = set(schema_index)
    entries: dict[str, ManagerCatalogEntry] = {}
    for manager_name, raw_entry in raw_entries.items():
        if not isinstance(manager_name, str) or not manager_name:
            raise _error(_MANAGER_NAME_INVALID)
        if manager_name not in exposed_names:
            raise _error(_MANAGER_HIDDEN.format(manager_name=manager_name))
        if not isinstance(raw_entry, Mapping):
            raise _error(_ENTRY_MAPPING.format(manager_name=manager_name))
        missing = [field for field in _REQUIRED_FIELDS if field not in raw_entry]
        if missing:
            raise _error(
                _ENTRY_MISSING_FIELDS.format(
                    manager_name=manager_name, fields=", ".join(missing)
                )
            )
        entries[manager_name] = ManagerCatalogEntry(
            domain=_normalized_text(raw_entry["domain"], "domain"),
            aliases=_normalize_aliases(raw_entry["aliases"]),
            use_when=_normalized_text(raw_entry["use_when"], "use_when"),
            distinguish_from=_normalize_distinguish_from(
                raw_entry["distinguish_from"], manager_name, exposed_names
            ),
        )
    frozen_entries = MappingProxyType(dict(sorted(entries.items())))
    return ManagerCatalog(
        entries=frozen_entries,
        fingerprint=_canonical_fingerprint(frozen_entries),
    )
