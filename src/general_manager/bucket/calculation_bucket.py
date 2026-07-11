"""Bucket implementation that enumerates calculation interface combinations."""

from __future__ import annotations
from abc import ABCMeta
from collections.abc import Hashable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
import inspect
import struct
from types import UnionType
from itertools import islice
from typing import (
    Type,
    TYPE_CHECKING,
    Union,
    Optional,
    Generator,
    List,
    TypedDict,
    get_origin,
    get_args,
    cast,
    Protocol,
)
from uuid import UUID
from operator import attrgetter
from copy import deepcopy
from django.core.exceptions import EmptyResultSet, FieldError
from django.db.models import Value
from django.db.models.expressions import Col
from django.db.models.lookups import Exact, In
from django.db.models.query import ModelIterable, QuerySet
from django.db.models.sql.datastructures import BaseTable
from django.db.models.sql.query import Query
from django.db.models.sql.where import WhereNode
from general_manager.interface.base_interface import (
    InterfaceBase,
    generalManagerClassName,
    GeneralManagerType,
    _trusted_enumeration_scope,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.manager.input import (
    DateRangeDomain,
    Input,
    InputDomain,
    NumericRangeDomain,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.filter_parser import ParsedFilters, parse_filters

if TYPE_CHECKING:
    from general_manager.api.property import GraphQLProperty


type Combination = dict[str, object]
type RawFilterDefinitions = dict[str, object]

_INPUT_BEHAVIOR_OVERRIDE_NAMES = frozenset(
    {
        "resolve_possible_values",
        "normalize",
        "cast",
        "validate_bounds",
        "validate_with_callable",
        "_build_dependency_values",
    }
)
_INPUT_STATE_NAMES = frozenset(
    {
        "type",
        "possible_values",
        "required",
        "min_value",
        "max_value",
        "validator",
        "normalizer",
        "is_manager",
        "depends_on",
    }
)
_NUMERIC_RANGE_STATE_NAMES = frozenset({"kind", "min_value", "max_value", "step"})
_DATE_RANGE_STATE_NAMES = frozenset({"kind", "start", "end", "frequency", "step"})
_DATABASE_BUCKET_STATE_NAMES = frozenset(
    {
        "_data",
        "_manager_class",
        "filters",
        "excludes",
        "_search_date",
        "_sort_keys",
        "_sort_reverse",
        "_run_scoped_cacheable",
        "_query_signature_cache",
        "_trusted_query_signature",
    }
)
_QUERYSET_STATE_NAMES = frozenset(
    {
        "model",
        "_db",
        "_hints",
        "_query",
        "_result_cache",
        "_sticky_filter",
        "_for_write",
        "_prefetch_related_lookups",
        "_prefetch_done",
        "_known_related_objects",
        "_iterable_class",
        "_fields",
        "_defer_next_filter",
        "_deferred_filter",
    }
)
_QUERY_STATE_NAMES = frozenset(
    {
        "model",
        "alias_refcount",
        "alias_map",
        "alias_cols",
        "external_aliases",
        "table_map",
        "used_aliases",
        "where",
        "annotations",
        "extra",
        "_filtered_relations",
        "_annotation_select_cache",
        "filter_is_sticky",
        "_lookup_joins",
        "order_by",
        "extra_order_by",
        "base_table",
    }
)
_DATABASE_BUCKET_HOOK_NAMES = (
    "__contains__",
    "_contains_all_primary_keys",
    "_track_effective_dependencies",
    "_query_signature",
)
_CANONICAL_DATABASE_BUCKET_HOOKS = tuple(
    inspect.getattr_static(DatabaseBucket, name) for name in _DATABASE_BUCKET_HOOK_NAMES
)


@dataclass(frozen=True, slots=True)
class _TrustedToken:
    """Comparison-safe token for one exact immutable scalar value."""

    kind: str
    payload: object


@dataclass(frozen=True, slots=True)
class _ImmutableCollectionIdentity:
    """Hold an immutable SQL parameter collection while comparing it in O(1)."""

    collection: tuple[object, ...] | frozenset[object] = field(compare=False)
    identity: int
    length: int
    kind: type[tuple[object, ...]] | type[frozenset[object]]


def _trusted_immutable_state(value: object) -> bool:
    """Screen a deeply immutable built-in tuple without retaining a deep token."""
    leaf_token = _trusted_database_state_token(value)
    if (
        type(leaf_token) is tuple
        and leaf_token
        and leaf_token[0] in {"class", "scalar", "none"}
    ):
        return True
    return type(value) is tuple and all(
        _trusted_immutable_state(item) for item in cast(tuple[object, ...], value)
    )


def _immutable_state_identity(
    value: object,
    *,
    screen: bool,
) -> object | None:
    """Return an O(1)-comparable strong witness for immutable source metadata."""
    if value is None:
        return ("none",)
    if type(value) is not tuple:
        return None
    immutable_value = cast(tuple[object, ...], value)
    if screen and not _trusted_immutable_state(immutable_value):
        return None
    return _ImmutableCollectionIdentity(
        immutable_value,
        id(immutable_value),
        len(immutable_value),
        tuple,
    )


class _EnumerationWitness(Protocol):
    """Validate that an enumerated candidate remains backed by its source."""

    def authorizes(self, value: object) -> bool: ...

    def track_membership_dependency(self) -> None: ...


class _StaticEnumerationWitness:
    """Base witness for sources with no external cache dependency to track."""

    __slots__ = ()

    def track_membership_dependency(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _SequenceEnumerationWitness(_StaticEnumerationWitness):
    """Prove that the exact sequence slot still contains the emitted object."""

    source: list[object] | tuple[object, ...]
    source_index: int
    candidate: object
    candidate_token: _TrustedToken

    def authorizes(self, value: object) -> bool:
        if value is not self.candidate:
            return False
        try:
            current = self.source[self.source_index]
        except IndexError:
            return False
        return (
            current is self.candidate
            and _trusted_candidate_token(current) == self.candidate_token
        )


@dataclass(frozen=True, slots=True)
class _NumericRangeEnumerationWitness(_StaticEnumerationWitness):
    """Prove that an exact numeric range still has its immutable configuration."""

    source: NumericRangeDomain
    configuration: tuple[_TrustedToken, _TrustedToken, _TrustedToken]
    candidate: int | float

    def authorizes(self, _value: object) -> bool:
        configuration = _numeric_range_configuration(self.source)
        return configuration == self.configuration


@dataclass(frozen=True, slots=True)
class _DateRangeEnumerationWitness(_StaticEnumerationWitness):
    """Prove that an exact date range still has its immutable configuration."""

    source: DateRangeDomain
    configuration: tuple[
        _TrustedToken,
        _TrustedToken,
        _TrustedToken,
        _TrustedToken,
    ]
    candidate: date

    def authorizes(self, _value: object) -> bool:
        configuration = _date_range_configuration(self.source)
        return configuration == self.configuration


@dataclass(frozen=True, slots=True)
class _EnumerationEvidence:
    """Conservative proof that one candidate came from one static input source."""

    input_field: Input[type[object]]
    provider: object
    dependency_names: tuple[str, ...]
    dependency_tokens: tuple[_TrustedToken, ...]
    candidate_token: _TrustedToken
    witness: _EnumerationWitness

    def authorizes(
        self,
        input_field: Input[type[object]],
        value: object,
        identification: Mapping[str, object],
    ) -> bool:
        if input_field is not self.input_field:
            return False
        if not _input_has_exact_standard_state(input_field):
            return False
        if _input_has_behavior_override(input_field):
            return False
        if input_field.possible_values is not self.provider:
            return False
        if type(input_field.depends_on) is not list:
            return False
        if not _trusted_dependency_names_match(
            input_field.depends_on,
            self.dependency_names,
        ):
            return False
        dependency_snapshot = _trusted_dependency_snapshot(
            self.dependency_names, identification
        )
        if dependency_snapshot != self.dependency_tokens:
            return False
        if _trusted_candidate_token(value) != self.candidate_token:
            return False
        return self.witness.authorizes(value)

    def track_membership_dependency(self) -> None:
        self.witness.track_membership_dependency()


def _trusted_database_state_token(value: object) -> object | None:
    """Freeze exact built-in database bucket state without invoking value hooks."""
    try:
        class_mro = type.__getattribute__(value, "__mro__")
    except (AttributeError, TypeError):
        class_mro = None
    if type(class_mro) is tuple and class_mro and class_mro[0] is value:
        return ("class", id(value))
    scalar_token = _trusted_candidate_token(value)
    if scalar_token is not None:
        return ("scalar", scalar_token)
    if value is None:
        return ("none",)
    if type(value) is tuple:
        tuple_tokens = tuple(_trusted_database_state_token(item) for item in value)
        if any(item is None for item in tuple_tokens):
            return None
        return ("tuple", id(value), tuple_tokens)
    if type(value) is list:
        list_tokens = tuple(_trusted_database_state_token(item) for item in value)
        if any(item is None for item in list_tokens):
            return None
        return ("list", id(value), list_tokens)
    if type(value) is dict:
        mapping = cast(dict[object, object], value)
        mapping_items: list[tuple[str, object]] = []
        for key, item_value in mapping.items():
            if type(key) is not str:
                return None
            item_token = _trusted_database_state_token(item_value)
            if item_token is None:
                return None
            mapping_items.append((key, item_token))
        return ("dict", id(value), tuple(mapping_items))
    return None


def _database_expression_token(value: object) -> object | None:
    """Tokenize only exact Django expression nodes audited for membership."""
    if type(value) is Col:
        state = object.__getattribute__(value, "__dict__")
        allowed_names = {
            "_constructor_args",
            "output_field",
            "alias",
            "target",
            "contains_aggregate",
            "contains_over_clause",
            "identity",
        }
        if type(state) is not dict or not set(state).issubset(allowed_names):
            return None
        alias = state.get("alias")
        target = state.get("target")
        output_field = state.get("output_field")
        if type(alias) is not str or target is None or output_field is None:
            return None
        identity_token = _trusted_database_state_token(state.get("identity"))
        if "identity" in state and identity_token is None:
            return None
        return (
            "col",
            alias,
            id(target),
            type(target),
            id(output_field),
            state.get("contains_aggregate") is True,
            state.get("contains_over_clause") is True,
            identity_token,
        )
    if type(value) is Value:
        state = object.__getattribute__(value, "__dict__")
        allowed_names = {
            "_constructor_args",
            "value",
            "is_summary",
            "for_save",
            "contains_aggregate",
            "contains_over_clause",
            "output_field",
        }
        if type(state) is not dict or not set(state).issubset(allowed_names):
            return None
        value_token = _trusted_database_state_token(state.get("value"))
        if value_token is None:
            return None
        return (
            "value",
            value_token,
            state.get("is_summary") is True,
            state.get("for_save") is True,
            state.get("contains_aggregate") is True,
            state.get("contains_over_clause") is True,
            id(state.get("output_field")),
        )
    return None


def _database_where_token(
    where: object,
    *,
    screen_candidate_collections: bool,
) -> object | None:
    """Tokenize an exact, flat WhereNode containing audited built-in lookups."""
    if type(where) is not WhereNode:
        return None
    state = object.__getattribute__(where, "__dict__")
    allowed_names = {
        "children",
        "connector",
        "negated",
        "contains_aggregate",
        "contains_over_clause",
    }
    if type(state) is not dict or not set(state).issubset(allowed_names):
        return None
    children = state["children"]
    connector = state["connector"]
    negated = state["negated"]
    if (
        type(children) is not list
        or type(connector) is not str
        or type(negated) is not bool
    ):
        return None
    child_tokens: list[object] = []
    for child in children:
        if type(child) is not Exact and type(child) is not In:
            return None
        child_state = object.__getattribute__(child, "__dict__")
        allowed_names = {
            "_constructor_args",
            "lhs",
            "rhs",
            "bilateral_transforms",
            "contains_aggregate",
            "contains_over_clause",
        }
        if type(child_state) is not dict or not set(child_state).issubset(
            allowed_names
        ):
            return None
        lhs_token = _database_expression_token(child_state.get("lhs"))
        rhs = child_state.get("rhs")
        if type(child) is In:
            if type(rhs) is not tuple and type(rhs) is not frozenset:
                return None
            immutable_rhs = cast(tuple[object, ...] | frozenset[object], rhs)
            if screen_candidate_collections and any(
                _trusted_candidate_token(item) is None for item in immutable_rhs
            ):
                return None
            rhs_token: object | None = _ImmutableCollectionIdentity(
                immutable_rhs,
                id(immutable_rhs),
                len(immutable_rhs),
                type(immutable_rhs),
            )
        else:
            rhs_token = _trusted_database_state_token(rhs)
        transforms = child_state.get("bilateral_transforms")
        if (
            lhs_token is None
            or rhs_token is None
            or type(transforms) is not list
            or transforms
        ):
            return None
        child_tokens.append(
            (
                type(child),
                lhs_token,
                rhs_token,
                child_state.get("contains_aggregate") is True,
                child_state.get("contains_over_clause") is True,
            )
        )
    return (
        connector,
        negated,
        state.get("contains_aggregate") is True,
        state.get("contains_over_clause") is True,
        tuple(child_tokens),
    )


def _database_query_semantic_token(
    data: object,
    *,
    screen_candidate_collections: bool = True,
) -> object | None:
    """Fingerprint one exact canonical QuerySet/Query graph without hooks."""
    if type(data) is not QuerySet:
        return None
    data_state = object.__getattribute__(data, "__dict__")
    if type(data_state) is not dict or set(data_state) != _QUERYSET_STATE_NAMES:
        return None
    query = data_state.get("_query")
    if type(query) is not Query:
        return None
    query_state = object.__getattribute__(query, "__dict__")
    if type(query_state) is not dict or not set(query_state).issubset(
        _QUERY_STATE_NAMES
    ):
        return None
    model = query_state.get("model")
    model_token = _trusted_database_state_token(model)
    database_alias = data_state.get("_db")
    hints_token = _trusted_database_state_token(data_state.get("_hints"))
    if (
        type(model_token) is not tuple
        or not model_token
        or model_token[0] != "class"
        or data_state["model"] is not model
        or (database_alias is not None and type(database_alias) is not str)
        or hints_token is None
        or data_state["_result_cache"] is not None
        or data_state["_sticky_filter"] is not False
        or data_state["_for_write"] is not False
        or type(data_state["_prefetch_related_lookups"]) is not tuple
        or data_state["_prefetch_related_lookups"]
        or data_state["_prefetch_done"] is not False
        or type(data_state["_known_related_objects"]) is not dict
        or data_state["_known_related_objects"]
        or data_state["_iterable_class"] is not ModelIterable
        or data_state["_fields"] is not None
        or data_state["_defer_next_filter"] is not False
        or data_state["_deferred_filter"] is not None
    ):
        return None
    where_token = _database_where_token(
        query_state.get("where"),
        screen_candidate_collections=screen_candidate_collections,
    )
    if where_token is None:
        return None

    annotations = query_state.get("annotations")
    if type(annotations) is not dict:
        return None
    annotation_tokens: list[tuple[str, object]] = []
    for name, expression in annotations.items():
        if type(name) is not str:
            return None
        expression_token = _database_expression_token(expression)
        if expression_token is None:
            return None
        annotation_tokens.append((name, expression_token))
    annotation_select_cache = query_state.get("_annotation_select_cache")
    if annotation_select_cache is None:
        annotation_select_cache_token: object = ("none",)
    elif type(annotation_select_cache) is dict:
        cached_annotations: list[tuple[str, object]] = []
        for name, expression in annotation_select_cache.items():
            if type(name) is not str:
                return None
            expression_token = _database_expression_token(expression)
            if expression_token is None:
                return None
            cached_annotations.append((name, expression_token))
        annotation_select_cache_token = tuple(cached_annotations)
    else:
        return None

    alias_map = query_state.get("alias_map")
    if type(alias_map) is not dict:
        return None
    alias_tokens: list[tuple[str, str, str]] = []
    for alias, table in alias_map.items():
        if type(alias) is not str or type(table) is not BaseTable:
            return None
        table_state = object.__getattribute__(table, "__dict__")
        if type(table_state) is not dict or set(table_state) != {
            "table_name",
            "table_alias",
        }:
            return None
        table_name = table_state["table_name"]
        table_alias = table_state["table_alias"]
        if type(table_name) is not str or type(table_alias) is not str:
            return None
        alias_tokens.append((alias, table_name, table_alias))

    alias_refcount_token = _trusted_database_state_token(
        query_state.get("alias_refcount")
    )
    external_aliases_token = _trusted_database_state_token(
        query_state.get("external_aliases")
    )
    table_map_token = _trusted_database_state_token(query_state.get("table_map"))
    lookup_joins_token = _trusted_database_state_token(query_state.get("_lookup_joins"))
    used_aliases = query_state.get("used_aliases")
    filter_is_sticky = query_state.get("filter_is_sticky")
    base_table = query_state.get("base_table")
    if (
        alias_refcount_token is None
        or external_aliases_token is None
        or table_map_token is None
        or lookup_joins_token is None
        or type(used_aliases) is not set
        or used_aliases
        or filter_is_sticky is not False
        or (base_table is not None and type(base_table) is not str)
    ):
        return None

    low_mark = object.__getattribute__(query, "low_mark")
    high_mark = object.__getattribute__(query, "high_mark")
    distinct = object.__getattribute__(query, "distinct")
    distinct_fields = object.__getattribute__(query, "distinct_fields")
    order_by = query_state.get("order_by", ())
    group_by = object.__getattribute__(query, "group_by")
    values_select = object.__getattribute__(query, "values_select")
    select = object.__getattribute__(query, "select")
    annotation_select_mask = object.__getattribute__(query, "annotation_select_mask")
    extra_order_by = object.__getattribute__(query, "extra_order_by")
    default_ordering = object.__getattribute__(query, "default_ordering")
    standard_ordering = object.__getattribute__(query, "standard_ordering")
    alias_cols = object.__getattribute__(query, "alias_cols")
    combinator = object.__getattribute__(query, "combinator")
    select_for_update = object.__getattribute__(query, "select_for_update")
    deferred_loading = object.__getattribute__(query, "deferred_loading")
    extra = query_state.get("extra")
    filtered_relations = query_state.get("_filtered_relations")
    if (
        type(low_mark) is not int
        or (high_mark is not None and type(high_mark) is not int)
        or type(distinct) is not bool
        or type(distinct_fields) is not tuple
        or not all(type(field) is str for field in distinct_fields)
        or type(order_by) is not tuple
        or not all(type(field) is str for field in order_by)
        or (group_by is not None and type(group_by) is not tuple)
        or type(values_select) is not tuple
        or not all(type(field) is str for field in values_select)
        or type(select) is not tuple
        or annotation_select_mask is not None
        or type(extra_order_by) is not tuple
        or not all(type(field) is str for field in extra_order_by)
        or type(default_ordering) is not bool
        or type(standard_ordering) is not bool
        or type(alias_cols) is not bool
        or combinator is not None
        or type(select_for_update) is not bool
        or select_for_update
        or type(deferred_loading) is not tuple
        or len(deferred_loading) != 2
        or type(deferred_loading[0]) is not frozenset
        or deferred_loading[0]
        or deferred_loading[1] is not True
        or type(extra) is not dict
        or extra
        or type(filtered_relations) is not dict
        or filtered_relations
    ):
        return None
    select_tokens = tuple(_database_expression_token(item) for item in select)
    group_tokens = (
        ()
        if group_by is None
        else tuple(_database_expression_token(item) for item in group_by)
    )
    if any(token is None for token in select_tokens) or any(
        token is None for token in group_tokens
    ):
        return None
    return (
        id(data),
        id(query),
        model_token,
        database_alias,
        hints_token,
        where_token,
        low_mark,
        high_mark,
        distinct,
        distinct_fields,
        tuple(annotation_tokens),
        annotation_select_cache_token,
        order_by,
        extra_order_by,
        default_ordering,
        standard_ordering,
        alias_cols,
        group_tokens,
        values_select,
        select_tokens,
        tuple(alias_tokens),
        alias_refcount_token,
        external_aliases_token,
        table_map_token,
        lookup_joins_token,
        base_table,
    )


def _database_source_live_state(
    source: DatabaseBucket[GeneralManager],
    *,
    screen_candidate_collections: bool = True,
) -> object | None:
    """Return hook-free state that is cheap to recheck for every candidate."""
    try:
        source_state = object.__getattribute__(source, "__dict__")
    except AttributeError:
        return None
    if (
        type(source) is not DatabaseBucket
        or type(source_state) is not dict
        or set(source_state) != _DATABASE_BUCKET_STATE_NAMES
        or source_state["_search_date"] is not None
        or any(
            inspect.getattr_static(DatabaseBucket, name) is not expected
            for name, expected in zip(
                _DATABASE_BUCKET_HOOK_NAMES,
                _CANONICAL_DATABASE_BUCKET_HOOKS,
                strict=True,
            )
        )
    ):
        return None
    data = source_state["_data"]
    query_shape = _database_query_semantic_token(
        data,
        screen_candidate_collections=screen_candidate_collections,
    )
    filters = source_state["filters"]
    excludes = source_state["excludes"]
    search_date = source_state["_search_date"]
    sort_keys = source_state["_sort_keys"]
    sort_reverse = source_state["_sort_reverse"]
    run_scoped_cacheable = source_state["_run_scoped_cacheable"]
    query_signature_cache = source_state["_query_signature_cache"]
    trusted_query_signature = source_state["_trusted_query_signature"]
    manager_class = source_state["_manager_class"]
    filters_token = _trusted_database_state_token(filters)
    excludes_token = _trusted_database_state_token(excludes)
    sort_keys_token = _trusted_database_state_token(sort_keys)
    trusted_signature_token = _immutable_state_identity(
        trusted_query_signature,
        screen=screen_candidate_collections,
    )
    query_signature_cache_token = _immutable_state_identity(
        query_signature_cache,
        screen=screen_candidate_collections,
    )
    if (
        query_shape is None
        or filters_token is None
        or excludes_token is None
        or sort_keys_token is None
        or trusted_signature_token is None
        or query_signature_cache_token is None
        or type(sort_reverse) is not bool
        or type(run_scoped_cacheable) is not bool
    ):
        return None
    return (
        query_shape,
        manager_class,
        search_date,
        filters_token,
        excludes_token,
        sort_keys_token,
        sort_reverse,
        run_scoped_cacheable,
        id(query_signature_cache),
        query_signature_cache_token,
        id(trusted_query_signature),
        trusted_signature_token,
    )


@dataclass(frozen=True, slots=True)
class _DatabaseSourceSignature:
    """One compiled source witness shared by every candidate in a pass."""

    live_state: object
    sql: str
    parameter_tokens: tuple[_TrustedToken, ...]


def _database_source_signature(
    source: DatabaseBucket[GeneralManager],
) -> _DatabaseSourceSignature | None:
    """Compile one conservative signature for an exact live database source."""
    try:
        source_state = object.__getattribute__(source, "__dict__")
    except AttributeError:
        return None
    if (
        type(source_state) is not dict
        or set(source_state) != _DATABASE_BUCKET_STATE_NAMES
    ):
        return None
    data = source_state["_data"]
    if _database_query_semantic_token(data) is None:
        return None
    query_signature = source._query_signature()
    if query_signature is None:
        return None
    data_state = object.__getattribute__(data, "__dict__")
    query = data_state["_query"]
    try:
        sql, params = Query.sql_with_params(query)
    except (AttributeError, EmptyResultSet, FieldError, TypeError, ValueError):
        return None
    live_state = _database_source_live_state(source)
    if live_state is None:
        return None
    parameter_tokens = tuple(_trusted_candidate_token(param) for param in params)
    if any(token is None for token in parameter_tokens):
        return None
    return _DatabaseSourceSignature(
        live_state=live_state,
        sql=sql,
        parameter_tokens=cast(tuple[_TrustedToken, ...], parameter_tokens),
    )


def _standard_database_manager_class(manager_class: type[object]) -> bool:
    """Reject manager classes whose dispatch or identification access is custom."""
    return (
        issubclass(manager_class, GeneralManager)
        and type(manager_class) is GeneralManagerMeta
        and manager_class.__init__ is GeneralManager.__init__
        and inspect.getattr_static(manager_class, "_track_identification_dependency")
        is inspect.getattr_static(GeneralManager, "_track_identification_dependency")
        and inspect.getattr_static(
            manager_class, "_track_identification_dependency_active"
        )
        is inspect.getattr_static(
            GeneralManager, "_track_identification_dependency_active"
        )
        and type.__getattribute__(
            manager_class,
            "_gm_uses_default_identification_dependency_active",
        )
        is True
        and _dispatch_matches(
            manager_class,
            _INSTANCE_DISPATCH_NAMES,
            _CANONICAL_MANAGER_DISPATCH,
        )
        and inspect.getattr_static(manager_class, "identification")
        is inspect.getattr_static(GeneralManager, "identification")
    )


@dataclass(slots=True)
class _DatabaseEnumerationEvidence:
    """Proof for one exact manager emitted from one exact database bucket."""

    input_field: Input[type[object]]
    provider: DatabaseBucket[GeneralManager]
    provider_signature: _DatabaseSourceSignature
    manager: GeneralManager
    identification: dict[str, object]
    primary_key_token: _TrustedToken
    authorized_tokens: frozenset[_TrustedToken] | None = None

    @property
    def primary_key(self) -> object:
        return self.identification["id"]

    def is_current(self, *, check_source_signature: bool = False) -> bool:
        if (
            type(self.input_field) is not Input
            or not _input_has_exact_standard_state(self.input_field)
            or _input_has_behavior_override(self.input_field)
            or self.input_field.possible_values is not self.provider
            or self.input_field.type is not self.provider._manager_class
            or self.input_field.validator is not None
            or self.input_field.normalizer is not None
            or type(self.input_field.depends_on) is not list
            or self.input_field.depends_on
            or not _standard_database_manager_class(self.provider._manager_class)
            or self.manager.__class__ is not self.provider._manager_class
        ):
            return False
        current_source_state = (
            _database_source_signature(self.provider)
            if check_source_signature
            else _database_source_live_state(
                self.provider,
                screen_candidate_collections=False,
            )
        )
        expected_source_state: object = (
            self.provider_signature
            if check_source_signature
            else self.provider_signature.live_state
        )
        if current_source_state != expected_source_state:
            return False
        try:
            current_identification = object.__getattribute__(
                self.manager,
                "_GeneralManager__id",
            )
        except AttributeError:
            return False
        return (
            current_identification is self.identification
            and type(current_identification) is dict
            and len(current_identification) == 1
            and "id" in current_identification
            and _trusted_candidate_token(current_identification["id"])
            == self.primary_key_token
        )

    def authorizes(
        self,
        input_field: Input[type[object]],
        value: object,
        _identification: Mapping[str, object],
    ) -> bool:
        return (
            input_field is self.input_field
            and value is self.manager
            and self.authorized_tokens is not None
            and self.primary_key_token in self.authorized_tokens
            and self.is_current()
        )

    def track_membership_dependency(self) -> None:
        self.provider._track_effective_dependencies()


type _TrustedEvidence = _EnumerationEvidence | _DatabaseEnumerationEvidence


_MANAGER_CONSTRUCTION_HOOK_NAMES = (
    "__init__",
    "_track_identification_dependency",
    "_track_identification_dependency_active",
)
_INTERFACE_CONSTRUCTION_HOOK_NAMES = (
    "__init__",
    "parse_input_fields_to_identification",
    "_process_input_field",
    "format_identification",
)
_INPUT_CONSTRUCTION_HOOK_NAMES = (
    "cast",
    "normalize",
    "validate_bounds",
    "validate_with_callable",
    "resolve_possible_values",
    "_build_dependency_values",
)
_INSTANCE_DISPATCH_NAMES = ("__getattribute__", "__getattr__", "__setattr__")
_METACLASS_DISPATCH_NAMES = (
    "__call__",
    "__getattribute__",
    "__getattr__",
    "__setattr__",
)
_STATIC_ATTRIBUTE_MISSING = object()


def _static_hook_snapshot(
    owner: type[object],
    hook_names: tuple[str, ...],
) -> tuple[object, ...] | None:
    """Read raw class descriptors without invoking descriptor hooks."""
    try:
        return tuple(inspect.getattr_static(owner, name) for name in hook_names)
    except AttributeError:
        return None


def _static_hooks_match(
    owner: type[object],
    hook_names: tuple[str, ...],
    expected_hooks: tuple[object, ...],
) -> bool:
    """Compare raw descriptors by identity without invoking equality hooks."""
    current_hooks = _static_hook_snapshot(owner, hook_names)
    if current_hooks is None or len(current_hooks) != len(expected_hooks):
        return False
    return all(
        current is expected
        for current, expected in zip(current_hooks, expected_hooks, strict=True)
    )


def _dispatch_snapshot(
    owner: type[object],
    dispatch_names: tuple[str, ...],
) -> tuple[object, ...]:
    """Read dispatch descriptors while preserving absent attributes."""
    return tuple(
        inspect.getattr_static(owner, name, _STATIC_ATTRIBUTE_MISSING)
        for name in dispatch_names
    )


def _dispatch_matches(
    owner: type[object],
    dispatch_names: tuple[str, ...],
    expected: tuple[object, ...],
) -> bool:
    """Compare dispatch descriptors by identity without invoking them."""
    current = _dispatch_snapshot(owner, dispatch_names)
    return all(
        actual is expected_descriptor
        for actual, expected_descriptor in zip(current, expected, strict=True)
    )


_CANONICAL_INPUT_DISPATCH = _dispatch_snapshot(object, _INSTANCE_DISPATCH_NAMES)
_CANONICAL_INTERFACE_DISPATCH = _dispatch_snapshot(
    InterfaceBase,
    _INSTANCE_DISPATCH_NAMES,
)
_CANONICAL_MANAGER_DISPATCH = _dispatch_snapshot(
    GeneralManager,
    _INSTANCE_DISPATCH_NAMES,
)
_CANONICAL_MANAGER_META_DISPATCH = _dispatch_snapshot(
    GeneralManagerMeta,
    _METACLASS_DISPATCH_NAMES,
)
_CANONICAL_INTERFACE_META_DISPATCH = _dispatch_snapshot(
    ABCMeta,
    _METACLASS_DISPATCH_NAMES,
)


@dataclass(frozen=True, slots=True)
class _TrustedConstructionPlan:
    """Snapshot audited construction hooks for one private manager pass."""

    manager_class: type[object]
    interface_class: type[InterfaceBase]
    input_fields: dict[str, Input[type[object]]]
    manager_hooks: tuple[object, ...]
    interface_hooks: tuple[object, ...]
    input_hooks: tuple[object, ...]
    manager_dispatch: tuple[object, ...]
    interface_dispatch: tuple[object, ...]
    input_dispatch: tuple[object, ...]
    metaclass_dispatch: tuple[object, ...]
    interface_metaclass_dispatch: tuple[object, ...]

    def is_current(self) -> bool:
        """Fail closed when any construction hook changed after preparation."""
        try:
            if (
                type.__getattribute__(self.manager_class, "Interface")
                is not self.interface_class
                or type.__getattribute__(self.interface_class, "_parent_class")
                is not self.manager_class
                or type.__getattribute__(self.interface_class, "input_fields")
                is not self.input_fields
                or type.__getattribute__(
                    self.manager_class,
                    "_gm_uses_default_identification_dependency_active",
                )
                is not True
            ):
                return False
        except AttributeError:
            return False
        return (
            _static_hooks_match(
                self.manager_class,
                _MANAGER_CONSTRUCTION_HOOK_NAMES,
                self.manager_hooks,
            )
            and _static_hooks_match(
                self.interface_class,
                _INTERFACE_CONSTRUCTION_HOOK_NAMES,
                self.interface_hooks,
            )
            and _static_hooks_match(
                Input,
                _INPUT_CONSTRUCTION_HOOK_NAMES,
                self.input_hooks,
            )
            and type(self.manager_class) is GeneralManagerMeta
            and type(self.interface_class) is ABCMeta
            and _dispatch_matches(
                self.manager_class,
                _INSTANCE_DISPATCH_NAMES,
                self.manager_dispatch,
            )
            and _dispatch_matches(
                self.interface_class,
                _INSTANCE_DISPATCH_NAMES,
                self.interface_dispatch,
            )
            and _dispatch_matches(
                Input,
                _INSTANCE_DISPATCH_NAMES,
                self.input_dispatch,
            )
            and _dispatch_matches(
                GeneralManagerMeta,
                _METACLASS_DISPATCH_NAMES,
                self.metaclass_dispatch,
            )
            and _dispatch_matches(
                ABCMeta,
                _METACLASS_DISPATCH_NAMES,
                self.interface_metaclass_dispatch,
            )
        )


@dataclass(frozen=True, slots=True)
class _PlanBoundEnumerationEvidence:
    """Require an unchanged construction plan at the membership gate."""

    evidence: _TrustedEvidence
    plan: _TrustedConstructionPlan

    def authorizes(
        self,
        input_field: Input[type[object]],
        value: object,
        identification: Mapping[str, object],
    ) -> bool:
        return self.plan.is_current() and self.evidence.authorizes(
            input_field,
            value,
            identification,
        )

    def track_membership_dependency(self) -> None:
        self.evidence.track_membership_dependency()


def _trusted_candidate_token(value: object) -> _TrustedToken | None:
    """Return a comparison-safe token for an eligible exact scalar."""
    value_type = type(value)
    if value_type is bool:
        return _TrustedToken("bool", value)
    if value_type is int:
        return _TrustedToken("int", value)
    if value_type is float:
        return _TrustedToken("float", struct.pack("!d", value))
    if value_type is str:
        return _TrustedToken("str", value)
    if value_type is bytes:
        return _TrustedToken("bytes", value)
    if value_type is date:
        date_value = cast(date, value)
        return _TrustedToken("date", date_value.toordinal())
    if value_type is datetime:
        datetime_value = cast(datetime, value)
        if datetime_value.tzinfo is not None:
            return None
        return _TrustedToken(
            "datetime",
            (
                datetime_value.year,
                datetime_value.month,
                datetime_value.day,
                datetime_value.hour,
                datetime_value.minute,
                datetime_value.second,
                datetime_value.microsecond,
                datetime_value.fold,
            ),
        )
    if value_type is UUID:
        return _TrustedToken("uuid", cast(UUID, value).int)
    return None


def _trusted_dependency_snapshot(
    dependency_names: tuple[str, ...],
    identification: Mapping[str, object],
) -> tuple[_TrustedToken, ...] | None:
    """Tokenize declared dependency values without comparing arbitrary objects."""
    if type(identification) is not dict:
        return None
    tokens: list[_TrustedToken] = []
    for dependency_name in dependency_names:
        if type(dependency_name) is not str:
            return None
        try:
            dependency_value = identification[dependency_name]
        except KeyError:
            return None
        token = _trusted_candidate_token(dependency_value)
        if token is None:
            return None
        tokens.append(token)
    return tuple(tokens)


def _input_has_behavior_override(input_field: Input[type[object]]) -> bool:
    """Return whether an exact Input instance shadows trusted behavior methods."""
    instance_state = input_field.__dict__
    if type(instance_state) is not dict:
        return True
    for state_name in instance_state:
        if type(state_name) is not str:
            return True
        if any(
            state_name == override_name
            for override_name in _INPUT_BEHAVIOR_OVERRIDE_NAMES
        ):
            return True
    return False


def _input_has_exact_standard_state(input_field: Input[type[object]]) -> bool:
    """Reject added, missing, or hostile Input instance state without hooks."""
    instance_state = input_field.__dict__
    if type(instance_state) is not dict or len(instance_state) != len(
        _INPUT_STATE_NAMES
    ):
        return False
    for state_name in instance_state:
        if type(state_name) is not str:
            return False
        if not any(state_name == expected for expected in _INPUT_STATE_NAMES):
            return False
    return True


def _trusted_dependency_names_match(
    current_names: list[str],
    expected_names: tuple[str, ...],
) -> bool:
    """Compare dependency names only after proving every current value is a str."""
    if len(current_names) != len(expected_names):
        return False
    for current_name, expected_name in zip(current_names, expected_names, strict=True):
        if type(current_name) is not str or current_name != expected_name:
            return False
    return True


def _numeric_range_configuration(
    source: NumericRangeDomain,
) -> tuple[_TrustedToken, _TrustedToken, _TrustedToken] | None:
    """Return safe exact built-in configuration for a numeric range."""
    if not _domain_has_exact_state(source, _NUMERIC_RANGE_STATE_NAMES):
        return None
    if type(source.kind) is not str or source.kind != "numeric_range":
        return None
    values = (source.min_value, source.max_value, source.step)
    if any(type(value) not in {int, float} for value in values):
        return None
    tokens = tuple(_trusted_candidate_token(value) for value in values)
    if any(token is None for token in tokens):
        return None
    return cast(
        tuple[_TrustedToken, _TrustedToken, _TrustedToken],
        tokens,
    )


def _date_range_configuration(
    source: DateRangeDomain,
) -> tuple[_TrustedToken, _TrustedToken, _TrustedToken, _TrustedToken] | None:
    """Return safe exact built-in configuration for a date range."""
    if not _domain_has_exact_state(source, _DATE_RANGE_STATE_NAMES):
        return None
    if (
        type(source.kind) is not str
        or source.kind != "date_range"
        or type(source.start) is not date
        or type(source.end) is not date
        or type(source.frequency) is not str
        or type(source.step) is not int
    ):
        return None
    tokens = tuple(
        _trusted_candidate_token(value)
        for value in (source.start, source.end, source.frequency, source.step)
    )
    if any(token is None for token in tokens):
        return None
    return cast(
        tuple[_TrustedToken, _TrustedToken, _TrustedToken, _TrustedToken],
        tokens,
    )


def _domain_has_exact_state(
    source: NumericRangeDomain | DateRangeDomain,
    expected_names: frozenset[str],
) -> bool:
    """Reject instance behavior shadows without reading or invoking them."""
    instance_state = source.__dict__
    if type(instance_state) is not dict:
        return False
    if len(instance_state) != len(expected_names):
        return False
    for state_name in instance_state:
        if type(state_name) is not str:
            return False
        if not any(state_name == expected_name for expected_name in expected_names):
            return False
    return True


def _sequence_enumeration_witness(
    source: list[object] | tuple[object, ...],
    candidate: object,
    candidate_token: _TrustedToken,
    source_index: int | None,
) -> _SequenceEnumerationWitness | None:
    """Build an identity-and-position witness for an exact sequence."""
    if type(source_index) is not int or source_index < 0:
        return None
    try:
        source_candidate = source[source_index]
    except IndexError:
        return None
    if source_candidate is not candidate:
        return None
    if _trusted_candidate_token(source_candidate) != candidate_token:
        return None
    return _SequenceEnumerationWitness(
        source,
        source_index,
        candidate,
        candidate_token,
    )


def _numeric_range_enumeration_witness(
    source: NumericRangeDomain,
    candidate: object,
) -> _NumericRangeEnumerationWitness | None:
    """Build a witness for a safe candidate emitted by an exact numeric range."""
    configuration = _numeric_range_configuration(source)
    if configuration is None:
        return None
    expected_type = (
        float
        if any(
            type(value) is float
            for value in (source.min_value, source.max_value, source.step)
        )
        else int
    )
    if type(candidate) is not expected_type:
        return None
    numeric_candidate = cast(int | float, candidate)
    return _NumericRangeEnumerationWitness(
        source,
        configuration,
        numeric_candidate,
    )


def _date_range_enumeration_witness(
    source: DateRangeDomain,
    candidate: object,
) -> _DateRangeEnumerationWitness | None:
    """Build a witness for a safe candidate emitted by an exact date range."""
    configuration = _date_range_configuration(source)
    if configuration is None or type(candidate) is not date:
        return None
    date_candidate = candidate
    return _DateRangeEnumerationWitness(source, configuration, date_candidate)


def _trusted_enumeration_evidence(
    input_field: Input[type[object]],
    resolved_source: object,
    candidate: object,
    identification: dict[str, object],
    *,
    source_index: int | None = None,
) -> _EnumerationEvidence | None:
    """Build static-source evidence without resolving callable providers."""
    if type(input_field) is not Input:
        return None
    provider = input_field.possible_values
    if callable(provider):
        return None
    if _input_has_behavior_override(input_field):
        return None
    if provider is not resolved_source:
        return None
    if type(input_field.depends_on) is not list:
        return None
    dependency_names = tuple(input_field.depends_on)
    dependency_tokens = _trusted_dependency_snapshot(
        dependency_names,
        identification,
    )
    if dependency_tokens is None:
        return None
    candidate_token = _trusted_candidate_token(candidate)
    if candidate_token is None:
        return None

    witness: _EnumerationWitness | None
    source_type = type(resolved_source)
    if source_type is list or source_type is tuple:
        witness = _sequence_enumeration_witness(
            cast(list[object] | tuple[object, ...], resolved_source),
            candidate,
            candidate_token,
            source_index,
        )
    elif source_type is NumericRangeDomain:
        witness = _numeric_range_enumeration_witness(
            cast(NumericRangeDomain, resolved_source),
            candidate,
        )
    elif source_type is DateRangeDomain:
        witness = _date_range_enumeration_witness(
            cast(DateRangeDomain, resolved_source),
            candidate,
        )
    else:
        witness = None
    if witness is None:
        return None
    return _EnumerationEvidence(
        input_field=input_field,
        provider=provider,
        dependency_names=dependency_names,
        dependency_tokens=dependency_tokens,
        candidate_token=candidate_token,
        witness=witness,
    )


def _database_enumeration_evidence(
    input_field: Input[type[object]],
    provider: object,
    candidate: object,
    *,
    provider_signature: _DatabaseSourceSignature
    | None
    | object = _STATIC_ATTRIBUTE_MISSING,
) -> _DatabaseEnumerationEvidence | None:
    """Build unprepared evidence for an exact static database bucket value."""
    if type(provider) is not DatabaseBucket or type(input_field) is not Input:
        return None
    database_provider = cast(DatabaseBucket[GeneralManager], provider)
    if (
        input_field.possible_values is not database_provider
        or input_field.type is not database_provider._manager_class
        or input_field.validator is not None
        or input_field.normalizer is not None
        or type(input_field.depends_on) is not list
        or input_field.depends_on
        or not _input_has_exact_standard_state(input_field)
        or _input_has_behavior_override(input_field)
        or not _standard_database_manager_class(database_provider._manager_class)
        or candidate.__class__ is not database_provider._manager_class
    ):
        return None
    try:
        identification = object.__getattribute__(candidate, "_GeneralManager__id")
    except AttributeError:
        return None
    if (
        type(identification) is not dict
        or len(identification) != 1
        or "id" not in identification
    ):
        return None
    primary_key_token = _trusted_candidate_token(identification["id"])
    if provider_signature is _STATIC_ATTRIBUTE_MISSING:
        provider_signature = _database_source_signature(database_provider)
    if primary_key_token is None or provider_signature is None:
        return None
    if not isinstance(provider_signature, _DatabaseSourceSignature):
        return None
    return _DatabaseEnumerationEvidence(
        input_field=input_field,
        provider=database_provider,
        provider_signature=provider_signature,
        manager=cast(GeneralManager, candidate),
        identification=identification,
        primary_key_token=primary_key_token,
    )


class SortedFilters(TypedDict):
    """Internal parsed-filter partition used while generating combinations."""

    prop_filters: ParsedFilters
    input_filters: ParsedFilters
    prop_excludes: ParsedFilters
    input_excludes: ParsedFilters


class InvalidCalculationInterfaceError(TypeError):
    """Raised when a CalculationBucket is initialized with a non-CalculationInterface manager."""

    def __init__(self) -> None:
        """
        Indicates a manager's interface does not inherit from CalculationInterface.

        Initializes the exception with the message "CalculationBucket requires a manager whose interface inherits from CalculationInterface."
        """
        super().__init__(
            "CalculationBucket requires a manager whose interface inherits from CalculationInterface."
        )


class IncompatibleBucketTypeError(TypeError):
    """Raised when attempting to combine buckets of different types."""

    def __init__(self, bucket_type: type, other_type: type) -> None:
        """
        Initialize the error indicating two bucket types cannot be combined.

        Parameters:
            bucket_type (type): The first bucket class involved in the attempted combination.
            other_type (type): The second bucket class involved in the attempted combination.

        Notes:
            The exception message is formatted as "Cannot combine {bucket_type.__name__} with {other_type.__name__}."
        """
        super().__init__(
            f"Cannot combine {bucket_type.__name__} with {other_type.__name__}."
        )


class IncompatibleBucketManagerError(TypeError):
    """Raised when attempting to combine buckets with different manager classes."""

    def __init__(self, first_manager: type, second_manager: type) -> None:
        """
        Indicate that two buckets for different manager classes cannot be combined.

        Parameters:
            first_manager (type): The first manager class involved in the attempted combination.
            second_manager (type): The second manager class involved in the attempted combination.

        Description:
            The exception message will include the class names of both managers.
        """
        super().__init__(
            f"Cannot combine buckets for {first_manager.__name__} and {second_manager.__name__}."
        )


class CyclicDependencyError(ValueError):
    """Raised when a cyclic dependency is detected in calculation sorting."""

    def __init__(self, node: str) -> None:
        """
        Initialize the CyclicDependencyError for a specific node involved in a dependency cycle.

        Parameters:
            node (str): The identifier of the node where a cycle was detected. The exception message will include this node, e.g. "Cyclic dependency detected: {node}."
        """
        super().__init__(f"Cyclic dependency detected: {node}.")


class InvalidPossibleValuesError(TypeError):
    """Raised when an input field provides invalid possible value definitions."""

    def __init__(self, key_name: str) -> None:
        """
        Indicate that an input field defines an invalid `possible_values` configuration.

        Parameters:
            key_name (str): Name of the input field whose `possible_values` configuration is invalid.
        """
        super().__init__(
            f"Invalid possible_values configuration for input '{key_name}'."
        )


class MissingCalculationMatchError(ValueError):
    """Raised when no calculation matches the provided filters."""

    def __init__(self) -> None:
        """
        Exception raised when no calculation matches the provided filters.

        Initializes the exception with the message "No matching calculation found."
        """
        super().__init__("No matching calculation found.")


class MultipleCalculationMatchError(ValueError):
    """Raised when more than one calculation matches the provided filters."""

    def __init__(self) -> None:
        """
        Error raised when more than one calculation matches the provided filters.

        Initializes the exception with the message "Multiple matching calculations found."
        """
        super().__init__("Multiple matching calculations found.")


class CalculationBucket(Bucket[GeneralManagerType]):
    """Bucket that builds cartesian products of calculation input fields."""

    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        filter_definitions: Optional[RawFilterDefinitions] = None,
        exclude_definitions: Optional[RawFilterDefinitions] = None,
        sort_key: Optional[Union[str, tuple[str]]] = None,
        reverse: bool = False,
    ) -> None:
        """
        Initialize a CalculationBucket configured to enumerate all valid input combinations for a manager.

        Parameters:
            manager_class (type[GeneralManagerType]): Manager subclass whose Interface must inherit from CalculationInterface.
            filter_definitions (dict[str, dict] | None): Mapping of input/property filter constraints to apply to generated combinations.
            exclude_definitions (dict[str, dict] | None): Mapping of input/property exclude constraints to remove generated combinations.
            sort_key (str | tuple[str] | None): Key name or tuple of key names used to order generated manager combinations.
            reverse (bool): If True, reverse the ordering defined by `sort_key`.

        Raises:
            InvalidCalculationInterfaceError: If the manager_class.Interface does not inherit from CalculationInterface.
        """
        from general_manager.interface.interfaces.calculation import (
            CalculationInterface,
        )

        super().__init__(manager_class)

        interface_class = manager_class.Interface
        if not issubclass(interface_class, CalculationInterface):
            raise InvalidCalculationInterfaceError()
        self.input_fields = interface_class.input_fields
        self.filter_definitions = (
            {} if filter_definitions is None else filter_definitions
        )
        self.exclude_definitions = (
            {} if exclude_definitions is None else exclude_definitions
        )

        properties = self._manager_class.Interface.get_graph_ql_properties()
        possible_values = self.transform_properties_to_input_fields(
            properties, self.input_fields
        )

        self._filters = parse_filters(self.filter_definitions, possible_values)
        self._excludes = parse_filters(self.exclude_definitions, possible_values)

        self._data: list[Combination] | None = None
        self._combination_evidence: dict[
            int, tuple[Combination, dict[str, _TrustedEvidence]]
        ] = {}
        self._evidence_exposed = False
        self.sort_key = sort_key
        self.reverse = reverse

    def _register_combination_evidence(
        self,
        combination: Combination,
        evidence_by_name: dict[str, _TrustedEvidence],
    ) -> None:
        """Retain private provenance for one exact combination dictionary."""
        if self._evidence_exposed or not evidence_by_name:
            return
        self._combination_evidence[id(combination)] = (
            combination,
            evidence_by_name.copy(),
        )

    def _lookup_combination_evidence(
        self, combination: Combination
    ) -> dict[str, _TrustedEvidence] | None:
        """Return provenance only when the identity-keyed entry still matches."""
        if self._evidence_exposed:
            return None
        entry = self._combination_evidence.get(id(combination))
        if entry is None or entry[0] is not combination:
            return None
        return entry[1]

    def _invalidate_combination_evidence(self, *, exposed: bool = False) -> None:
        """Revoke all retained provenance, optionally permanently for this bucket."""
        self._combination_evidence.clear()
        if exposed:
            self._evidence_exposed = True

    def _uses_standard_trusted_construction(self) -> bool:
        """Return whether manager construction follows the audited base path."""
        return self._trusted_construction_plan() is not None

    def _trusted_construction_plan(self) -> _TrustedConstructionPlan | None:
        """Capture exact hook identities for one eligible construction pass."""
        manager_class = self._manager_class
        interface_class = manager_class.Interface
        if not issubclass(manager_class, GeneralManager):
            return None
        if type(manager_class) is not GeneralManagerMeta:
            return None
        if type(interface_class) is not ABCMeta:
            return None
        if not _dispatch_matches(
            manager_class,
            _INSTANCE_DISPATCH_NAMES,
            _CANONICAL_MANAGER_DISPATCH,
        ):
            return None
        if not _dispatch_matches(
            interface_class,
            _INSTANCE_DISPATCH_NAMES,
            _CANONICAL_INTERFACE_DISPATCH,
        ):
            return None
        if not _dispatch_matches(
            Input,
            _INSTANCE_DISPATCH_NAMES,
            _CANONICAL_INPUT_DISPATCH,
        ):
            return None
        if not _dispatch_matches(
            GeneralManagerMeta,
            _METACLASS_DISPATCH_NAMES,
            _CANONICAL_MANAGER_META_DISPATCH,
        ):
            return None
        if not _dispatch_matches(
            ABCMeta,
            _METACLASS_DISPATCH_NAMES,
            _CANONICAL_INTERFACE_META_DISPATCH,
        ):
            return None
        if manager_class.__init__ is not GeneralManager.__init__:
            return None
        if inspect.getattr_static(
            manager_class,
            "_track_identification_dependency",
        ) is not inspect.getattr_static(
            GeneralManager,
            "_track_identification_dependency",
        ):
            return None
        if (
            type.__getattribute__(
                manager_class,
                "_gm_uses_default_identification_dependency_active",
            )
            is not True
        ):
            return None
        if interface_class.__init__ is not InterfaceBase.__init__:
            return None
        if (
            interface_class.parse_input_fields_to_identification
            is not InterfaceBase.parse_input_fields_to_identification
            or interface_class._process_input_field
            is not InterfaceBase._process_input_field
            or interface_class.format_identification
            is not InterfaceBase.format_identification
        ):
            return None
        if getattr(interface_class, "_parent_class", None) is not manager_class:
            return None
        if type(self.input_fields) is not dict:
            return None
        if interface_class.input_fields is not self.input_fields:
            return None
        if not all(
            type(input_field) is Input
            and _input_has_exact_standard_state(input_field)
            and not _input_has_behavior_override(input_field)
            for input_field in self.input_fields.values()
        ):
            return None
        manager_hooks = _static_hook_snapshot(
            manager_class,
            _MANAGER_CONSTRUCTION_HOOK_NAMES,
        )
        interface_hooks = _static_hook_snapshot(
            interface_class,
            _INTERFACE_CONSTRUCTION_HOOK_NAMES,
        )
        input_hooks = _static_hook_snapshot(Input, _INPUT_CONSTRUCTION_HOOK_NAMES)
        if manager_hooks is None or interface_hooks is None or input_hooks is None:
            return None
        return _TrustedConstructionPlan(
            manager_class=manager_class,
            interface_class=interface_class,
            input_fields=self.input_fields,
            manager_hooks=manager_hooks,
            interface_hooks=interface_hooks,
            input_hooks=input_hooks,
            manager_dispatch=_dispatch_snapshot(
                manager_class,
                _INSTANCE_DISPATCH_NAMES,
            ),
            interface_dispatch=_dispatch_snapshot(
                interface_class,
                _INSTANCE_DISPATCH_NAMES,
            ),
            input_dispatch=_dispatch_snapshot(Input, _INSTANCE_DISPATCH_NAMES),
            metaclass_dispatch=_dispatch_snapshot(
                GeneralManagerMeta,
                _METACLASS_DISPATCH_NAMES,
            ),
            interface_metaclass_dispatch=_dispatch_snapshot(
                ABCMeta,
                _METACLASS_DISPATCH_NAMES,
            ),
        )

    def _manager_from_combination(
        self,
        combination: Combination,
        *,
        construction_plan: _TrustedConstructionPlan | None,
    ) -> GeneralManagerType:
        """Construct one manager inside a synchronous, single-use trust scope."""
        evidence = (
            self._lookup_combination_evidence(combination)
            if construction_plan is not None
            else None
        )
        if evidence is None or construction_plan is None:
            return self._manager_class(**combination)
        scoped_evidence = {
            name: _PlanBoundEnumerationEvidence(field_evidence, construction_plan)
            for name, field_evidence in evidence.items()
        }
        with _trusted_enumeration_scope(
            self._manager_class.Interface,
            scoped_evidence,
        ):
            return self._manager_class(**combination)

    def _prepare_database_enumeration_evidence(
        self,
        combinations: Iterable[Combination],
    ) -> None:
        """Authorize exact database candidates with one membership query per source."""
        if type(combinations) is not list and type(combinations) is not tuple:
            return
        grouped: dict[
            int,
            tuple[
                DatabaseBucket[GeneralManager],
                list[_DatabaseEnumerationEvidence],
            ],
        ] = {}
        found_database_evidence = False
        for combination in combinations:
            evidence_by_name = self._lookup_combination_evidence(combination)
            if evidence_by_name is None or set(evidence_by_name) != set(
                self.input_fields
            ):
                return
            for evidence in evidence_by_name.values():
                if not isinstance(evidence, _DatabaseEnumerationEvidence):
                    continue
                found_database_evidence = True
                if not evidence.is_current(check_source_signature=False):
                    return
                source_id = id(evidence.provider)
                entry = grouped.get(source_id)
                if entry is None:
                    grouped[source_id] = (evidence.provider, [evidence])
                elif entry[0] is evidence.provider:
                    entry[1].append(evidence)
                else:
                    return
        if not found_database_evidence:
            return

        if any(
            _database_source_signature(source) != evidences[0].provider_signature
            for source, evidences in grouped.values()
        ):
            return

        authorized_by_source: list[
            tuple[list[_DatabaseEnumerationEvidence], frozenset[_TrustedToken]]
        ] = []
        for source, evidences in grouped.values():
            primary_keys = [evidence.primary_key for evidence in evidences]
            if not source._contains_all_primary_keys(primary_keys):
                continue
            authorized_by_source.append(
                (
                    evidences,
                    frozenset(evidence.primary_key_token for evidence in evidences),
                )
            )
        for evidences, authorized_tokens in authorized_by_source:
            for evidence in evidences:
                evidence.authorized_tokens = authorized_tokens

    @contextmanager
    def _trusted_construction_pass(
        self,
        combinations: Iterable[Combination],
    ) -> Iterator[_TrustedConstructionPlan | None]:
        """Own provenance for one construction pass and revoke it on every exit."""
        try:
            construction_plan = (
                None if self._evidence_exposed else self._trusted_construction_plan()
            )
            if construction_plan is not None:
                self._prepare_database_enumeration_evidence(combinations)
            yield construction_plan
        finally:
            self._invalidate_combination_evidence()

    def __eq__(self, other: object) -> bool:
        """
        Compare two calculation buckets for structural equality.

        Parameters:
            other (object): Candidate bucket.

        Returns:
            bool: True when both buckets share the same manager class and identical filter/exclude state.
        """
        if not isinstance(other, self.__class__):
            return False
        return (
            self.filter_definitions == other.filter_definitions
            and self.exclude_definitions == other.exclude_definitions
            and self._manager_class == other._manager_class
        )

    def __reduce__(self) -> generalManagerClassName | tuple[object, ...]:
        """
        Provide pickling support for calculation buckets.

        Returns:
            tuple[object, ...]: Reconstruction data representing the class, arguments, and state.
        """
        self._invalidate_combination_evidence(exposed=True)
        return (
            self.__class__,
            (
                self._manager_class,
                self.filter_definitions,
                self.exclude_definitions,
                self.sort_key,
                self.reverse,
            ),
            {"data": self._data},
        )

    def __setstate__(self, state: dict[str, object]) -> None:
        """
        Restore the bucket after unpickling.

        Parameters:
            state: Pickled state containing cached combination data.

        Returns:
            None
        """
        self._data = cast(list[Combination] | None, state.get("data"))
        self._combination_evidence = {}
        self._evidence_exposed = True

    def __copy__(self) -> CalculationBucket[GeneralManagerType]:
        """Return an untrusted shallow copy of this bucket."""
        copied = object.__new__(type(self))
        copied.__dict__.update(self.__dict__)
        for slot_name in self._copyable_slot_names():
            if hasattr(self, slot_name):
                setattr(copied, slot_name, getattr(self, slot_name))
        copied._data = (
            None if self._data is None else [combo.copy() for combo in self._data]
        )
        copied._combination_evidence = {}
        copied._evidence_exposed = True
        return copied

    def __deepcopy__(
        self, memo: dict[int, object]
    ) -> CalculationBucket[GeneralManagerType]:
        """Return an untrusted deep copy without copying private provenance."""
        copied = object.__new__(type(self))
        memo[id(self)] = copied
        instance_state = {
            name: value
            for name, value in self.__dict__.items()
            if name != "_combination_evidence"
        }
        copied.__dict__.update(deepcopy(instance_state, memo))
        for slot_name in self._copyable_slot_names():
            if hasattr(self, slot_name):
                setattr(copied, slot_name, deepcopy(getattr(self, slot_name), memo))
        copied._combination_evidence = {}
        copied._evidence_exposed = True
        return copied

    @classmethod
    def _copyable_slot_names(cls) -> Iterator[str]:
        """Yield concrete slot attribute names across the subclass hierarchy."""
        for base_class in cls.__mro__:
            slots = base_class.__dict__.get("__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot_name in slots:
                if slot_name in {"__dict__", "__weakref__"}:
                    continue
                if slot_name.startswith("__") and not slot_name.endswith("__"):
                    owner_name = base_class.__name__.lstrip("_")
                    slot_name = f"_{owner_name}{slot_name}"
                yield slot_name

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Build a bucket from constraints common to this bucket and another operand.

        Parameters:
            other: A CalculationBucket or a GeneralManager instance to combine.
                If a same-class manager instance is given, it is first converted
                into an ``id__in=[identification]`` filter bucket.

        Returns:
            A new CalculationBucket containing only filter and exclude
            definitions that are present with equal values on both bucket
            operands. This is a compatibility-preserving common-constraint
            merge, not a set union of materialized calculation results.

        Raises:
            IncompatibleBucketTypeError: If `other` is neither a CalculationBucket nor a compatible manager instance.
            IncompatibleBucketManagerError: If `other` is a CalculationBucket for a different manager class.
        """
        from general_manager.manager.general_manager import GeneralManager

        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(self.filter(id__in=[other.identification]))
        if not isinstance(other, self.__class__):
            raise IncompatibleBucketTypeError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise IncompatibleBucketManagerError(
                self._manager_class, other._manager_class
            )

        combined_filters = {
            key: value
            for key, value in self.filter_definitions.items()
            if key in other.filter_definitions
            and value == other.filter_definitions[key]
        }

        combined_excludes = {
            key: value
            for key, value in self.exclude_definitions.items()
            if key in other.exclude_definitions
            and value == other.exclude_definitions[key]
        }

        return CalculationBucket(
            self._manager_class,
            combined_filters,
            combined_excludes,
        )

    def __str__(self) -> str:
        """
        Return a compact preview of generated combinations.

        Cached buckets include the exact combination count. Uncached buckets avoid
        materializing all combinations for string formatting; when more than the
        preview limit exists, the count is reported as a lower-bound label.

        Returns:
            str: Human-readable summary of up to five combinations.
        """
        PRINT_MAX = 5
        combinations, count_label, has_more = self._str_combinations_preview(PRINT_MAX)
        prefix = f"CalculationBucket ({count_label})["
        main = ",".join(
            [f"{self._manager_class.__name__}(**{comb})" for comb in combinations]
        )
        suffix = "]"
        if has_more:
            suffix = ", ...]"

        return f"{prefix}{main}{suffix}"

    def _str_combinations_preview(
        self, limit: int
    ) -> tuple[list[Combination], str, bool]:
        """
        Return combinations, count label, and overflow flag for ``__str__``.

        Sorted or reversed buckets use normal materialization so the preview
        reflects the final global ordering. Unsorted uncached buckets read at
        most ``limit + 1`` matching combinations and leave ``_data`` untouched.
        """
        if self._data is not None:
            return self._data[:limit], str(len(self._data)), len(self._data) > limit

        if self._normalized_sort_key() is not None or self.reverse:
            combinations = self._materialize_combinations(expose=False)
            return (
                combinations[:limit],
                str(len(combinations)),
                len(combinations) > limit,
            )

        from general_manager.cache.run_context import ensure_calculation_run_context

        with ensure_calculation_run_context():
            sorted_inputs = self.topological_sort_inputs()
            sorted_filters = self._sort_filters(sorted_inputs)
            if self._uses_static_iterator_possible_values(sorted_inputs):
                combinations = self._materialize_combinations(expose=False)
                return (
                    combinations[:limit],
                    str(len(combinations)),
                    len(combinations) > limit,
                )
            snapshot_iterables = self._uses_dependent_possible_values(sorted_inputs)
            preview_iterator = self._iter_input_combinations(
                sorted_inputs,
                sorted_filters["input_filters"],
                sorted_filters["input_excludes"],
                snapshot_iterables=snapshot_iterables,
            )
            if sorted_filters["prop_filters"] or sorted_filters["prop_excludes"]:
                preview_iterator = self._iter_prop_filtered_identifications(
                    preview_iterator,
                    sorted_filters["prop_filters"],
                    sorted_filters["prop_excludes"],
                )
            preview = list(islice(preview_iterator, limit + 1))

        has_more = len(preview) > limit
        if has_more:
            preview = preview[:limit]
        count_label = f"{limit}+" if has_more else str(len(preview))
        return preview, count_label, has_more

    def _uses_static_iterator_possible_values(self, sorted_inputs: list[str]) -> bool:
        """Return whether previewing would consume a one-shot static iterator."""
        return any(
            isinstance(self.input_fields[input_name].possible_values, Iterator)
            for input_name in sorted_inputs
        )

    def _uses_dependent_possible_values(self, sorted_inputs: list[str]) -> bool:
        """Return whether previewing should snapshot values before dependencies."""
        return any(
            bool(self.input_fields[input_name].depends_on)
            and self.input_fields[input_name].possible_values is not None
            for input_name in sorted_inputs
        )

    def __repr__(self) -> str:
        """
        Return a detailed representation of the bucket configuration.

        Returns:
            str: Debug string listing filters, excludes, sort key, and ordering.
        """
        return f"{self.__class__.__name__}({self._manager_class.__name__}, {self.filter_definitions}, {self.exclude_definitions}, {self.sort_key}, {self.reverse})"

    @staticmethod
    def transform_properties_to_input_fields(
        properties: dict[str, GraphQLProperty],
        input_fields: dict[str, Input[type[object]]],
    ) -> dict[str, Input[type[object]]]:
        """
        Derive input-field definitions for GraphQL properties without explicit inputs.

        This helper is a framework hook used by calculation filtering and
        sorting. It treats list, tuple, set, union, and optional property type
        hints as their concrete element/member type when possible and falls back
        to ``object`` when the hint cannot be resolved to a class.

        Parameters:
            properties (dict[str, GraphQLProperty]): GraphQL properties declared on the manager.
            input_fields (dict[str, Input]): Existing input field definitions.

        Returns:
            dict[str, Input]: Combined mapping of input field names to `Input` definitions.
        """
        parsed_inputs = {**input_fields}
        for prop_name, prop in properties.items():
            current_hint = prop.graphql_type_hint
            origin = get_origin(current_hint)
            args = list(get_args(current_hint))

            if origin in (Union, UnionType):
                non_none_args = [arg for arg in args if arg is not type(None)]
                current_hint = non_none_args[0] if non_none_args else object
                origin = get_origin(current_hint)
                args = list(get_args(current_hint))

            if origin in (list, tuple, set):
                inner = args[0] if args else object
                resolved_type = inner if isinstance(inner, type) else object
            elif isinstance(current_hint, type):
                resolved_type = current_hint
            else:
                resolved_type = object

            prop_input = Input(
                type=resolved_type, possible_values=None, depends_on=None
            )
            parsed_inputs[prop_name] = prop_input

        return parsed_inputs

    def filter(self, **kwargs: object) -> CalculationBucket[GeneralManagerType]:
        """
        Add additional filters and return a new calculation bucket.

        Lookup keys use the shared calculation filter grammar: ``field`` or
        ``field__lookup`` for input and property values. Supported Python
        lookup operators are ``exact``, ``lt``, ``lte``, ``gt``, ``gte``,
        ``contains``, ``startswith``, ``endswith``, and ``in``. For
        manager-typed inputs, ``field=value`` filters by the manager id,
        ``field_id`` is an id alias, and suffixes such as
        ``field__name__startswith`` are forwarded to the nested manager bucket.
        Unknown fields raise ``UnknownInputFieldError`` from the filter parser.

        Parameters:
            **kwargs: Filter expressions applied to generated combinations.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket reflecting the updated filter definitions.

        Raises:
            UnknownInputFieldError: If a filter key references no input or
                derived GraphQL property.
            TypeError: Propagated from invalid input casts or downstream
                manager-bucket filtering.
            ValueError: Propagated from input parsing or normalization.
        """
        return CalculationBucket(
            manager_class=self._manager_class,
            filter_definitions={
                **self.filter_definitions.copy(),
                **kwargs,
            },
            exclude_definitions=self.exclude_definitions.copy(),
        )

    def exclude(self, **kwargs: object) -> CalculationBucket[GeneralManagerType]:
        """
        Add additional exclusion rules and return a new calculation bucket.

        Exclusion keys use the same lookup grammar and error behavior as
        :meth:`filter`; matching combinations are removed rather than kept.

        Parameters:
            **kwargs: Exclusion expressions removing combinations from the result.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket reflecting the updated exclusion definitions.

        Raises:
            UnknownInputFieldError: If an exclude key references no input or
                derived GraphQL property.
            TypeError: Propagated from invalid input casts or downstream
                manager-bucket filtering.
            ValueError: Propagated from input parsing or normalization.
        """
        return CalculationBucket(
            manager_class=self._manager_class,
            filter_definitions=self.filter_definitions.copy(),
            exclude_definitions={
                **self.exclude_definitions.copy(),
                **kwargs,
            },
        )

    def all(self) -> CalculationBucket[GeneralManagerType]:
        """
        Return a deep copy of this calculation bucket.

        Returns:
            CalculationBucket[GeneralManagerType]: Independent copy that can be mutated without affecting the original.
        """
        return deepcopy(self)

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Iterate over every generated combination as a manager instance.

        Yields:
            GeneralManagerType: Manager constructed from each valid set of inputs.
        """
        combinations = self._materialize_combinations(expose=False)
        with self._trusted_construction_pass(combinations) as construction_plan:
            for combo in combinations:
                yield self._manager_from_combination(
                    combo,
                    construction_plan=construction_plan,
                )

    def _sort_filters(self, sorted_inputs: List[str]) -> SortedFilters:
        """
        Partition filters into input- and property-based buckets.

        Parameters:
            sorted_inputs (list[str]): Input names ordered by dependency.

        Returns:
            SortedFilters: Mapping that separates filters/excludes for inputs and properties.
        """
        input_filters: ParsedFilters = {}
        prop_filters: ParsedFilters = {}
        input_excludes: ParsedFilters = {}
        prop_excludes: ParsedFilters = {}

        for filter_name, filter_def in self._filters.items():
            if filter_name in sorted_inputs:
                input_filters[filter_name] = filter_def
            else:
                prop_filters[filter_name] = filter_def
        for exclude_name, exclude_def in self._excludes.items():
            if exclude_name in sorted_inputs:
                input_excludes[exclude_name] = exclude_def
            else:
                prop_excludes[exclude_name] = exclude_def

        return {
            "prop_filters": prop_filters,
            "input_filters": input_filters,
            "prop_excludes": prop_excludes,
            "input_excludes": input_excludes,
        }

    def _normalized_sort_key(self) -> tuple[str, ...] | None:
        """Return the configured sort key as a tuple, or None when unsorted."""
        if self.sort_key is None:
            return None
        if isinstance(self.sort_key, str):
            return (self.sort_key,)
        return self.sort_key

    def _bucket_index_source_signature(self) -> Hashable:
        """Return a stable signature for equivalent calculation bucket plans."""
        return (
            "calculation",
            self._manager_class,
            freeze_bucket_index_value(self.filter_definitions),
            freeze_bucket_index_value(self.exclude_definitions),
            self._normalized_sort_key(),
            self.reverse,
        )

    def _sort_uses_only_inputs(self, sort_key: tuple[str, ...] | None) -> bool:
        """Return whether a sort can be applied to raw input dictionaries."""
        if sort_key is None:
            return True
        return all(key in self.input_fields for key in sort_key)

    def _sort_dict_combinations(
        self,
        combinations: list[Combination],
        sort_key: tuple[str, ...],
    ) -> list[Combination]:
        """
        Sort input dictionaries while tolerating missing optional inputs.

        Present values sort before missing values in ascending order. Missing
        keys use None as the explicit placeholder, guarded by a presence flag so
        they are not compared directly with concrete input values.
        """
        return sorted(
            combinations,
            key=lambda combo: tuple(
                (key not in combo, combo.get(key, None)) for key in sort_key
            ),
        )

    def _manager_combinations(
        self,
        combinations: list[Combination],
    ) -> list[GeneralManagerType]:
        """Instantiate managers for each raw input-combination dictionary."""
        with self._trusted_construction_pass(combinations) as construction_plan:
            return [
                self._manager_from_combination(
                    combo,
                    construction_plan=construction_plan,
                )
                for combo in combinations
            ]

    @staticmethod
    def _manager_identifications(
        managers: list[GeneralManagerType],
    ) -> list[Combination]:
        """Return the identification dictionaries from manager instances."""
        return [manager.identification for manager in managers]

    def _materialize_combinations(self, *, expose: bool) -> List[Combination]:
        """
        Compute (and cache) the list of valid input combinations.

        This framework helper materializes the bucket. It orders inputs by
        dependency, applies input-level filters/excludes while enumerating
        candidate values, then applies property-level filters/excludes and
        sorting when manager access is required. The returned list is the
        bucket's cached mutable list; callers should treat it as read-only.

        Returns:
            list[Combination]: Cached list of input dictionaries satisfying filters, excludes, and ordering.

        Raises:
            CyclicDependencyError: If input dependencies contain a cycle.
            InvalidPossibleValuesError: If a required input cannot provide
                iterable or bucket-backed possible values.
            UnknownInputFieldError: If stored filter definitions reference an
                unknown input or property.
            AttributeError: Propagated from missing computed properties during
                property filtering or sorting.
            TypeError: Propagated from invalid casts, downstream bucket
                filtering, or incomparable sort values.
            ValueError: Propagated from input parsing or normalization.
        """

        if self._data is None:
            from general_manager.cache.run_context import ensure_calculation_run_context

            self._invalidate_combination_evidence()
            try:
                with ensure_calculation_run_context():
                    sorted_inputs = self.topological_sort_inputs()
                    sorted_filters = self._sort_filters(sorted_inputs)
                    current_combinations = self._generate_input_combinations(
                        sorted_inputs,
                        sorted_filters["input_filters"],
                        sorted_filters["input_excludes"],
                        retain_evidence=True,
                    )
                    sort_key = self._normalized_sort_key()
                    needs_manager_access = (
                        bool(sorted_filters["prop_filters"])
                        or bool(sorted_filters["prop_excludes"])
                        or not self._sort_uses_only_inputs(sort_key)
                    )

                    if needs_manager_access:
                        manager_combinations = self._manager_combinations(
                            current_combinations
                        )
                        manager_combinations = self._filter_prop_combinations(
                            manager_combinations,
                            sorted_filters["prop_filters"],
                            sorted_filters["prop_excludes"],
                        )
                        if sort_key is not None:
                            getters = [attrgetter(key) for key in sort_key]
                            manager_combinations = sorted(
                                manager_combinations,
                                key=lambda manager_obj: tuple(
                                    getter(manager_obj) for getter in getters
                                ),
                            )
                        identifications = self._manager_identifications(
                            manager_combinations
                        )
                        self._invalidate_combination_evidence()
                    else:
                        identifications = current_combinations
                        if sort_key is not None:
                            identifications = self._sort_dict_combinations(
                                identifications,
                                sort_key,
                            )

                    if self.reverse:
                        identifications.reverse()
                    self._data = identifications
            except BaseException:
                self._invalidate_combination_evidence()
                raise

        if expose:
            self._invalidate_combination_evidence(exposed=True)
        return self._data

    def generate_combinations(self) -> List[Combination]:
        """Return cached combinations and permanently revoke private provenance."""
        return self._materialize_combinations(expose=True)

    def topological_sort_inputs(self) -> List[str]:
        """
        Produce a dependency-respecting order of input fields.

        This framework helper includes every configured input name and orders
        dependencies before the inputs that depend on them.

        Returns:
            list[str]: Input names ordered so each dependency appears before its dependents.

        Raises:
            CyclicDependencyError: If the dependency graph contains a cycle; the exception's `node` identifies a node involved in the cycle.
        """
        from collections import defaultdict

        dependencies = {
            name: field.depends_on for name, field in self.input_fields.items()
        }
        graph = defaultdict(set)
        for key, deps in dependencies.items():
            for dep in deps:
                graph[dep].add(key)

        visited = set()
        sorted_inputs = []

        def visit(node: str, temp_mark: set[str]) -> None:
            """
            Depth-first search helper that orders dependency nodes and detects cycles.

            Parameters:
                node (str): The input field being visited.
                temp_mark (set[str]): Nodes on the current DFS path used to detect cycles.

            Raises:
                CyclicDependencyError: If a cyclic dependency is detected involving `node`.
            """
            if node in visited:
                return
            if node in temp_mark:
                raise CyclicDependencyError(node)
            temp_mark.add(node)
            for m in graph.get(node, []):
                visit(m, temp_mark)
            temp_mark.remove(node)
            visited.add(node)
            sorted_inputs.append(node)

        for node in self.input_fields:
            if node not in visited:
                visit(node, set())

        sorted_inputs.reverse()
        return sorted_inputs

    def get_possible_values(
        self,
        key_name: str,
        input_field: Input[type[object]],
        current_combo: Combination,
    ) -> Union[Iterable[object], Bucket["GeneralManager"], None]:
        # Retrieve possible values
        """
        Resolve potential values for an input field based on the current partial input combination.

        This framework helper resolves static, callable, domain, iterable, or
        bucket-backed ``possible_values`` for one input. Optional inputs with no
        possible-values source return ``None``; required inputs without a valid
        iterable, domain, or bucket source raise ``InvalidPossibleValuesError``.

        Parameters:
            key_name (str): Name of the input field used for error context.
            input_field (Input): Input definition that may include `possible_values` and `depends_on`.
            current_combo (dict): Partial mapping of already-selected input values required to evaluate dependencies.

        Returns:
            Iterable[object] | Bucket[GeneralManager] | None: An iterable of allowed values for the input, a Bucket supplying candidate values, or ``None`` when an optional input has no explicit domain.

        Raises:
            InvalidPossibleValuesError: If the input field's `possible_values` is neither callable nor an iterable/Bucket.
        """
        possible_values = input_field.resolve_possible_values(
            current_combo,
            cache_context=(self._manager_class, key_name),
        )
        if possible_values is None:
            if input_field.required:
                raise InvalidPossibleValuesError(key_name)
            return None
        if isinstance(possible_values, InputDomain):
            possible_values = possible_values
        elif not isinstance(possible_values, (Iterable, Bucket)):
            raise InvalidPossibleValuesError(key_name)
        return possible_values

    def _iter_input_combinations(
        self,
        sorted_inputs: List[str],
        filters: ParsedFilters,
        excludes: ParsedFilters,
        *,
        snapshot_iterables: bool,
        retain_evidence: bool = False,
    ) -> Generator[Combination, None, None]:
        """
        Yield valid assignments of input fields satisfying filters and excludes.

        Parameters:
            sorted_inputs (list[str]): Input names in dependency-respecting order.
            filters (dict[str, dict]): Per-input filter definitions (may include `filter_funcs` or `filter_kwargs`).
            excludes (dict[str, dict]): Per-input exclusion definitions (may include `filter_funcs` or `filter_kwargs`).

        Yields:
            Combination: Completed input-to-value mappings that meet the
                filters and excludes.
        """

        registered_combinations: list[Combination] = []

        def input_passes_filters(
            input_name: str,
            current_combo: Combination,
        ) -> bool:
            """Return whether the current input state satisfies input-level filters."""

            field_filters = filters.get(input_name, {})
            field_excludes = excludes.get(input_name, {})
            current_value = current_combo.get(input_name)

            for filter_func in field_filters.get("filter_funcs", []):
                if not filter_func(current_value):
                    return False
            for exclude_func in field_excludes.get("filter_funcs", []):
                if exclude_func(current_value):
                    return False
            return True

        def helper(
            index: int,
            current_combo: Combination,
            current_evidence: dict[str, _TrustedEvidence],
        ) -> Generator[Combination, None, None]:
            """
            Recursively emit input combinations that satisfy filters and excludes.

            Parameters:
                index (int): Position within `sorted_inputs` currently being assigned.
                current_combo: Partial assignment of inputs built so far.

            Yields:
                Combination: Completed combination of input values.
            """
            if index == len(sorted_inputs):
                combination = current_combo.copy()
                if retain_evidence:
                    self._register_combination_evidence(
                        combination,
                        current_evidence,
                    )
                    if self._lookup_combination_evidence(combination) is not None:
                        registered_combinations.append(combination)
                yield combination
                return
            input_name: str = sorted_inputs[index]
            input_field = self.input_fields[input_name]

            possible_values = self.get_possible_values(
                input_name, input_field, current_combo
            )
            if possible_values is None:
                if input_passes_filters(input_name, current_combo):
                    yield from helper(index + 1, current_combo, current_evidence)
                return

            field_filters = filters.get(input_name, {})
            field_excludes = excludes.get(input_name, {})

            # use filter_funcs and exclude_funcs to filter possible values
            if isinstance(possible_values, Bucket):
                database_provider: object | None = (
                    possible_values if type(possible_values) is DatabaseBucket else None
                )
                database_provider_signature = (
                    _database_source_signature(
                        cast(DatabaseBucket[GeneralManager], database_provider)
                    )
                    if retain_evidence and database_provider is not None
                    else None
                )
                filter_kwargs = field_filters.get("filter_kwargs", {})
                exclude_kwargs = field_excludes.get("filter_kwargs", {})
                possible_values = possible_values.filter(**filter_kwargs).exclude(
                    **exclude_kwargs
                )
                indexed_values: Iterable[tuple[int, object]] = enumerate(
                    possible_values
                )
                resolved_source: object | None = None
            else:
                database_provider = None
                database_provider_signature = None
                resolved_source = possible_values
                filter_funcs = field_filters.get("filter_funcs", [])
                exclude_funcs = field_excludes.get("filter_funcs", [])

                def filtered_indexed_values() -> Generator[
                    tuple[int, object], None, None
                ]:
                    """Preserve callback order while retaining source positions."""
                    for source_index, value in enumerate(possible_values):
                        if any(not filter_func(value) for filter_func in filter_funcs):
                            continue
                        if any(exclude_func(value) for exclude_func in exclude_funcs):
                            continue
                        yield source_index, value

                indexed_values = filtered_indexed_values()
                if snapshot_iterables:
                    indexed_values = list(indexed_values)

            for source_index, value in indexed_values:
                if not isinstance(value, input_field.type):
                    continue
                evidence: _TrustedEvidence | None = None
                if retain_evidence and resolved_source is not None:
                    evidence = _trusted_enumeration_evidence(
                        input_field,
                        resolved_source,
                        value,
                        current_combo,
                        source_index=source_index,
                    )
                elif retain_evidence and database_provider is not None:
                    evidence = _database_enumeration_evidence(
                        input_field,
                        database_provider,
                        value,
                        provider_signature=database_provider_signature,
                    )
                current_combo[input_name] = value
                if evidence is not None:
                    current_evidence[input_name] = evidence
                try:
                    yield from helper(index + 1, current_combo, current_evidence)
                finally:
                    current_combo.pop(input_name, None)
                    if evidence is not None:
                        current_evidence.pop(input_name, None)

        completed = False
        try:
            yield from helper(0, {}, {})
            completed = True
        finally:
            if retain_evidence and not completed:
                for combination in registered_combinations:
                    entry = self._combination_evidence.get(id(combination))
                    if entry is not None and entry[0] is combination:
                        del self._combination_evidence[id(combination)]

    def _generate_input_combinations(
        self,
        sorted_inputs: List[str],
        filters: ParsedFilters,
        excludes: ParsedFilters,
        *,
        retain_evidence: bool = False,
    ) -> List[Combination]:
        """
        Generate all valid assignments of input fields that satisfy filters.

        Parameters:
            sorted_inputs (list[str]): Input names in dependency-respecting order.
            filters (dict[str, dict]): Per-input filter definitions.
            excludes (dict[str, dict]): Per-input exclusion definitions.

        Returns:
            list[Combination]: Completed input-to-value mappings that meet the
                filters and excludes.
        """
        return list(
            self._iter_input_combinations(
                sorted_inputs,
                filters,
                excludes,
                snapshot_iterables=True,
                retain_evidence=retain_evidence,
            )
        )

    def _iter_prop_filtered_identifications(
        self,
        combinations: Iterable[Combination],
        prop_filters: ParsedFilters,
        prop_excludes: ParsedFilters,
    ) -> Generator[Combination, None, None]:
        """
        Lazily apply property filters and yield manager identifications.

        This mirrors the property-filter materialization path used by
        :meth:`generate_combinations`, but lets ``__str__`` stop after enough
        matching combinations have been found.
        """
        with self._trusted_construction_pass(combinations) as construction_plan:
            for combo in combinations:
                manager = self._manager_from_combination(
                    combo,
                    construction_plan=construction_plan,
                )
                if self._filter_prop_combinations(
                    [manager], prop_filters, prop_excludes
                ):
                    yield manager.identification

    def _filter_prop_combinations(
        self,
        manager_combinations: list[GeneralManagerType],
        prop_filters: ParsedFilters,
        prop_excludes: ParsedFilters,
    ) -> list[GeneralManagerType]:
        """
        Apply property-level filters and excludes to manager combinations.

        Parameters:
            manager_combinations (list[GeneralManagerType]): Managers built from
                input combinations already passing input filters.
            prop_filters: Filter definitions keyed by property name.
            prop_excludes: Exclude definitions keyed by property name.

        Returns:
            list[GeneralManagerType]: Manager instances that satisfy property
            constraints.
        """

        prop_filter_needed = set(prop_filters.keys()) | set(prop_excludes.keys())
        if not prop_filter_needed:
            return manager_combinations

        # Apply property filters and exclusions
        filtered_combos: list[GeneralManagerType] = []
        for manager in manager_combinations:
            keep = True
            # include filters
            for prop_name, defs in prop_filters.items():
                for func in defs.get("filter_funcs", []):
                    if not func(getattr(manager, prop_name)):
                        keep = False
                        break
                if not keep:
                    break
            # excludes
            if keep:
                for prop_name, defs in prop_excludes.items():
                    for func in defs.get("filter_funcs", []):
                        if func(getattr(manager, prop_name)):
                            keep = False
                            break
                    if not keep:
                        break
            if keep:
                filtered_combos.append(manager)
        return filtered_combos

    def first(self) -> GeneralManagerType | None:
        """
        Return the first generated manager instance.

        Returns:
            GeneralManagerType | None: First instance or None when no combinations exist.
        """
        iterator = iter(self)
        try:
            return next(iterator)
        except StopIteration:
            return None
        finally:
            iterator.close()

    def last(self) -> GeneralManagerType | None:
        """
        Return the last generated manager instance.

        Returns:
            GeneralManagerType | None: Last instance or None when no combinations exist.
        """
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        """
        Return the number of calculation combinations.

        Returns:
            int: Number of generated combinations.
        """
        return self.__len__()

    def __len__(self) -> int:
        """
        Return the number of generated combinations.

        Returns:
            int: Cached number of combinations.
        """
        return len(self._materialize_combinations(expose=False))

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | CalculationBucket[GeneralManagerType]:
        """
        Retrieve a manager instance or subset of combinations.

        Parameters:
            item (int | slice): Index or slice specifying which combinations to return.

        Returns:
            GeneralManagerType | CalculationBucket[GeneralManagerType]:
                Manager instance for single indices or bucket wrapping the sliced combinations.
        """
        items = self._materialize_combinations(expose=False)
        result = items[item]
        if isinstance(result, list):
            self._invalidate_combination_evidence(exposed=True)
            new_bucket = CalculationBucket(
                self._manager_class,
                self.filter_definitions.copy(),
                self.exclude_definitions.copy(),
                self.sort_key,
                self.reverse,
            )
            new_bucket._data = result
            new_bucket._evidence_exposed = True
            return new_bucket
        with self._trusted_construction_pass((result,)) as construction_plan:
            return self._manager_from_combination(
                result,
                construction_plan=construction_plan,
            )

    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Determine whether the provided manager instance exists among generated combinations.

        Parameters:
            item (GeneralManagerType): Manager instance to test for membership.

        Returns:
            bool: True when the instance matches one of the generated combinations.
        """
        iterator = iter(self)
        try:
            return any(item == manager for manager in iterator)
        finally:
            iterator.close()

    def get(self, **kwargs: object) -> GeneralManagerType:
        """
        Return the single manager instance that matches the provided field filters.

        Parameters:
            **kwargs: Field filters to apply when selecting a calculation (e.g., property or input names mapped to expected values).

        Returns:
            The single manager instance that satisfies the provided filters.

        Raises:
            MissingCalculationMatchError: If no matching manager exists.
            MultipleCalculationMatchError: If more than one matching manager exists.
        """
        filtered_bucket = self.filter(**kwargs)
        items = list(filtered_bucket)
        if len(items) == 1:
            return items[0]
        elif len(items) == 0:
            raise MissingCalculationMatchError()
        else:
            raise MultipleCalculationMatchError()

    def sort(
        self, key: str | tuple[str], reverse: bool = False
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Create a new CalculationBucket configured to order generated combinations by the given attribute key.

        Sorting by raw input keys happens before managers are built. Sorting by
        computed properties builds manager instances and reads the named
        attributes. Missing attributes raise ``AttributeError`` when the bucket
        materializes; incomparable values raise ``TypeError`` from Python's
        sort. Exceptions raised by computed properties propagate unchanged.

        Parameters:
            key: Attribute name or tuple of attribute names to use for ordering generated manager combinations.
            reverse: If True, sort in descending order.

        Returns:
            A new CalculationBucket configured to sort combinations by the provided key and direction.
        """
        return CalculationBucket(
            self._manager_class,
            self.filter_definitions,
            self.exclude_definitions,
            key,
            reverse,
        )

    def none(self) -> CalculationBucket[GeneralManagerType]:
        """
        Return an empty calculation bucket for the same manager class.

        The returned bucket starts from an ``all()`` copy, then clears cached
        data and raw/parsed filter and exclude definitions. It preserves the
        manager class, sort key, and reverse flag.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket with no combinations
            and cleared filter/exclude state.
        """
        own = self.all()
        own._data = []
        own.filter_definitions = {}
        own.exclude_definitions = {}
        own._filters = {}
        own._excludes = {}
        return own
