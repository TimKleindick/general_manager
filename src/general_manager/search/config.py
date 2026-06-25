"""Search configuration helpers for external indexing backends."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast

from general_manager.manager.general_manager import GeneralManager


class InvalidFieldBoostError(ValueError):
    """Raised when a field boost is invalid."""

    def __init__(self) -> None:
        """Initialize the invalid field-boost error message."""
        super().__init__("FieldConfig.boost must be greater than zero.")


class InvalidIndexBoostError(ValueError):
    """Raised when an index boost is invalid."""

    def __init__(self) -> None:
        """Initialize the invalid index-boost error message."""
        super().__init__("IndexConfig.boost must be greater than zero.")


class InvalidIndexMinScoreError(ValueError):
    """Raised when an index min score is invalid."""

    def __init__(self) -> None:
        """Initialize the invalid index minimum-score error message."""
        super().__init__("IndexConfig.min_score must be non-negative.")


@dataclass(frozen=True)
class FieldConfig:
    """Describe a searchable field and its optional boost weight.

    Args:
        name: Manager attribute name serialized into search documents.
        boost: Optional positive search boost for this field.

    Raises:
        InvalidFieldBoostError: If `boost` is not `None` and is less than or
            equal to zero.
    """

    name: str
    boost: float | None = None

    def __post_init__(self) -> None:
        """Validate the configured boost value after initialization."""
        if self.boost is not None and self.boost <= 0:
            raise InvalidFieldBoostError


@dataclass(frozen=True)
class IndexConfig:
    """Describe how a manager contributes documents to a search index.

    Args:
        name: Search index name.
        fields: Searchable field names or `FieldConfig` entries, in priority
            order.
        filters: Field names allowed for backend filtering.
        sorts: Field names allowed for backend sorting.
        boost: Optional positive index-level boost.
        min_score: Optional non-negative minimum relevance score.

    Raises:
        InvalidIndexBoostError: If `boost` is not `None` and is less than or
            equal to zero.
        InvalidIndexMinScoreError: If `min_score` is not `None` and is less
            than zero.
    """

    name: str
    fields: Sequence[str | FieldConfig]
    filters: Sequence[str] = field(default_factory=tuple)
    sorts: Sequence[str] = field(default_factory=tuple)
    boost: float | None = None
    min_score: float | None = None

    def __post_init__(self) -> None:
        """Validate index-level boost and minimum score after initialization."""
        if self.boost is not None and self.boost <= 0:
            raise InvalidIndexBoostError
        if self.min_score is not None and self.min_score < 0:
            raise InvalidIndexMinScoreError

    def iter_fields(self) -> tuple[FieldConfig, ...]:
        """
        Normalize this index's field entries into FieldConfig objects.

        String entries are converted to `FieldConfig(name=entry)`.
        `FieldConfig` entries are returned unchanged. The returned tuple
        preserves original field order.

        Returns:
            Normalized field configuration objects.

        Raises:
            InvalidFieldBoostError: If converting a string entry somehow creates
                an invalid field config. Existing `FieldConfig` entries have
                already been validated at construction time.
        """
        normalized: list[FieldConfig] = []
        for entry in self.fields:
            if isinstance(entry, FieldConfig):
                normalized.append(entry)
            else:
                normalized.append(FieldConfig(name=entry))
        return tuple(normalized)

    def field_boosts(self) -> dict[str, float]:
        """
        Map configured field names to their boost values.

        Returns:
            Mapping of field name to boost for fields with explicit boosts.
            Duplicate field names keep the last boosted entry in `fields` order.
        """
        boosts: dict[str, float] = {}
        for field_config in self.iter_fields():
            if field_config.boost is not None:
                boosts[field_config.name] = field_config.boost
        return boosts


class SearchConfigProtocol(Protocol):
    """Structural protocol for manager-level search configuration.

    `document_id` and `to_document` receive the manager instance being indexed.
    `document_id` must return a stable document id string. `to_document` must
    return a mapping of document field names to arbitrary payload values.
    """

    indexes: Sequence[IndexConfig]
    document_id: Callable[[GeneralManager], str] | None
    type_label: str | None
    to_document: Callable[[GeneralManager], Mapping[str, object]] | None
    update_strategy: str | None


@dataclass(frozen=True)
class SearchConfigSpec:
    """Resolved configuration from a manager's `SearchConfig` class.

    Attributes:
        indexes: Required tuple of configured search indexes; there is no
            constructor default.
        document_id: Optional callable receiving a manager instance and
            returning a stable document id string.
        type_label: Optional searchable type label; registries fall back to the
            manager class name when omitted.
        to_document: Optional callable receiving a manager instance and
            returning a mapping of document field names to payload values.
        update_strategy: Optional backend/indexer strategy marker.
    """

    indexes: tuple[IndexConfig, ...]
    document_id: Callable[[GeneralManager], str] | None = None
    type_label: str | None = None
    to_document: Callable[[GeneralManager], Mapping[str, object]] | None = None
    update_strategy: str | None = None


def resolve_search_config(config: object | None) -> SearchConfigSpec | None:
    """
    Normalize a search configuration object into a `SearchConfigSpec`.

    If `config` is `None`, returns `None`. If `config` is already a
    `SearchConfigSpec`, returns it unchanged. Otherwise, extracts `indexes`,
    `document_id`, `type_label`, `to_document`, and `update_strategy` attributes
    from `config`, using default values when attributes are missing. Missing
    `indexes` resolves to an empty tuple.

    Args:
        config: Configuration object or resolved spec to normalize.

    Returns:
        A resolved spec, or `None` when `config` is `None`.

    Notes:
        This helper does not validate that `document_id` or `to_document` are
        callable and does not validate the element type of `indexes`; invalid
        values fail later where the indexer or registry uses them. Missing
        optional attributes default to `None`. Attribute access errors from
        unusual config objects propagate.
    """
    if config is None:
        return None
    if isinstance(config, SearchConfigSpec):
        return config

    indexes = tuple(getattr(config, "indexes", ()))
    document_id = cast(
        Callable[[GeneralManager], str] | None,
        getattr(config, "document_id", None),
    )
    type_label = getattr(config, "type_label", None)
    to_document = cast(
        Callable[[GeneralManager], Mapping[str, object]] | None,
        getattr(config, "to_document", None),
    )
    update_strategy = getattr(config, "update_strategy", None)

    return SearchConfigSpec(
        indexes=indexes,
        document_id=document_id,
        type_label=type_label,
        to_document=to_document,
        update_strategy=update_strategy,
    )


def iter_index_names(config: SearchConfigSpec | None) -> Iterable[str]:
    """
    Return index names from a resolved search config.

    Args:
        config: Resolved search configuration or `None`.

    Returns:
        A list of index names in the same order as `config.indexes`, or an empty
        list when `config` is `None`.
    """
    if config is None:
        return []
    return [index.name for index in config.indexes]
