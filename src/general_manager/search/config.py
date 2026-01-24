"""Search configuration helpers for external indexing backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


class InvalidFieldBoostError(ValueError):
    """Raised when a field boost is invalid."""

    def __init__(self) -> None:
        super().__init__("FieldConfig.boost must be greater than zero.")


class InvalidIndexBoostError(ValueError):
    """Raised when an index boost is invalid."""

    def __init__(self) -> None:
        super().__init__("IndexConfig.boost must be greater than zero.")


class InvalidIndexMinScoreError(ValueError):
    """Raised when an index min score is invalid."""

    def __init__(self) -> None:
        super().__init__("IndexConfig.min_score must be non-negative.")


@dataclass(frozen=True)
class FieldConfig:
    """Describe a searchable field and its optional boost weight."""

    name: str
    boost: float | None = None

    def __post_init__(self) -> None:
        if self.boost is not None and self.boost <= 0:
            raise InvalidFieldBoostError


@dataclass(frozen=True)
class IndexConfig:
    """Describe how a manager contributes documents to a search index."""

    name: str
    fields: Sequence[str | FieldConfig]
    filters: Sequence[str] = field(default_factory=tuple)
    sorts: Sequence[str] = field(default_factory=tuple)
    boost: float | None = None
    min_score: float | None = None

    def __post_init__(self) -> None:
        if self.boost is not None and self.boost <= 0:
            raise InvalidIndexBoostError
        if self.min_score is not None and self.min_score < 0:
            raise InvalidIndexMinScoreError

    def iter_fields(self) -> tuple[FieldConfig, ...]:
        """Return normalized FieldConfig entries for every configured field."""
        normalized: list[FieldConfig] = []
        for entry in self.fields:
            if isinstance(entry, FieldConfig):
                normalized.append(entry)
            else:
                normalized.append(FieldConfig(name=entry))
        return tuple(normalized)

    def field_boosts(self) -> dict[str, float]:
        """Return a mapping of field names to boost values when configured."""
        boosts: dict[str, float] = {}
        for field_config in self.iter_fields():
            if field_config.boost is not None:
                boosts[field_config.name] = field_config.boost
        return boosts


class SearchConfigProtocol(Protocol):
    """Structural protocol for manager-level search configuration."""

    indexes: Sequence[IndexConfig]
    document_id: Callable[[Any], str] | None
    type_label: str | None
    to_document: Callable[[Any], Mapping[str, Any]] | None
    update_strategy: str | None


@dataclass(frozen=True)
class SearchConfigSpec:
    """Resolved configuration from a manager's SearchConfig class."""

    indexes: tuple[IndexConfig, ...]
    document_id: Callable[[Any], str] | None = None
    type_label: str | None = None
    to_document: Callable[[Any], Mapping[str, Any]] | None = None
    update_strategy: str | None = None


def resolve_search_config(config: object | None) -> SearchConfigSpec | None:
    """Normalize a SearchConfig class or spec into a SearchConfigSpec instance."""
    if config is None:
        return None
    if isinstance(config, SearchConfigSpec):
        return config

    indexes = tuple(getattr(config, "indexes", ()))
    document_id = getattr(config, "document_id", None)
    type_label = getattr(config, "type_label", None)
    to_document = getattr(config, "to_document", None)
    update_strategy = getattr(config, "update_strategy", None)

    return SearchConfigSpec(
        indexes=indexes,
        document_id=document_id,
        type_label=type_label,
        to_document=to_document,
        update_strategy=update_strategy,
    )


def iter_index_names(config: SearchConfigSpec | None) -> Iterable[str]:
    """Yield index names from a resolved SearchConfigSpec."""
    if config is None:
        return []
    return [index.name for index in config.indexes]
