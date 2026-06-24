"""Internal GraphQL helpers for dependency-cache prefetch planning."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Protocol, cast

from django.core.cache import cache as django_cache
from graphql import GraphQLResolveInfo
from graphql.language.ast import (
    FieldNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    SelectionSetNode,
)

from general_manager.api.property import GraphQLProperty
from general_manager.cache.dependency_cache import (
    DependencyCacheBackend,
    DependencyCacheHit,
    read_many_dependency_cache_hits,
)
from general_manager.cache.run_context import current_calculation_run_context
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.make_cache_key import make_cache_key


class _GraphQLPropertyInterface(Protocol):
    """Interface shape required for dependency-cache prefetch planning."""

    @classmethod
    def get_graph_ql_properties(cls) -> Mapping[str, GraphQLProperty]: ...


def _get_graphql_properties(
    manager_class: type[object],
) -> Mapping[str, GraphQLProperty]:
    """Return GraphQL properties for manager classes that expose them."""
    interface_cls = getattr(manager_class, "Interface", None)
    if interface_cls is None:
        return {}
    raw_getter = getattr(interface_cls, "get_graph_ql_properties", None)
    if not callable(raw_getter):
        return {}
    get_properties = cast(Callable[[], Mapping[str, GraphQLProperty]], raw_getter)
    return get_properties()


def normalize_graphql_name(name: str) -> str:
    """Convert a GraphQL field name to the Python attribute name.

    Names already containing underscores are returned unchanged. CamelCase and
    lowerCamelCase names are converted to snake_case. Acronyms follow the
    regular-expression splitting used here rather than Graphene's full name
    conversion rules.
    """
    if "_" in name:
        return name
    snake = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", snake)
    return snake.lower()


def collect_selected_graphql_property_names(
    info: GraphQLResolveInfo,
    manager_class: type[object],
    *,
    root_field: str,
    normalize_name: Callable[[str], str] = normalize_graphql_name,
) -> set[str]:
    """Return GraphQLProperty names selected directly under ``root_field``.

    The helper traverses resolver ``field_nodes`` plus inline and named
    fragments, guarding named fragments with a visited set so cycles terminate.
    It compares AST field ``name.value`` values, not aliases, for both
    ``root_field`` and property fields. It treats syntactically present fields as
    selected and does not evaluate ``@skip``/``@include`` directives or fragment
    type conditions. It inspects only fields selected directly under each
    matching ``root_field``; nested relation selections are ignored. GraphQL
    field names are normalized before comparison so generated camelCase property
    fields map back to Python attribute names. Managers without an ``Interface``
    or without GraphQL properties return an empty set. Exceptions from
    ``Interface.get_graph_ql_properties()`` and from the injected
    ``normalize_name`` callback propagate unchanged. Malformed resolver-info
    objects may raise ``AttributeError`` for missing ``field_nodes``,
    ``fragments``, ``selection_set``, ``selections``, or ``name`` attributes.
    """
    available_properties = set(_get_graphql_properties(manager_class).keys())
    if not available_properties:
        return set()

    property_names: set[str] = set()

    def collect_direct_fields(
        selection_set: SelectionSetNode | None,
        visited: frozenset[str],
    ) -> None:
        if selection_set is None:
            return
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                normalized = normalize_name(selection.name.value)
                if normalized in available_properties:
                    property_names.add(normalized)
            elif isinstance(selection, InlineFragmentNode):
                collect_direct_fields(selection.selection_set, visited)
            elif isinstance(selection, FragmentSpreadNode):
                fragment_name = selection.name.value
                if fragment_name in visited:
                    continue
                fragment = info.fragments.get(fragment_name)
                if fragment is not None:
                    collect_direct_fields(
                        fragment.selection_set,
                        visited | frozenset((fragment_name,)),
                    )

    def inspect_for_root(
        selection_set: SelectionSetNode | None,
        visited: frozenset[str],
    ) -> None:
        if selection_set is None:
            return
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                if selection.name.value == root_field:
                    collect_direct_fields(selection.selection_set, visited)
                else:
                    inspect_for_root(selection.selection_set, visited)
            elif isinstance(selection, InlineFragmentNode):
                inspect_for_root(selection.selection_set, visited)
            elif isinstance(selection, FragmentSpreadNode):
                fragment_name = selection.name.value
                if fragment_name in visited:
                    continue
                fragment = info.fragments.get(fragment_name)
                if fragment is not None:
                    inspect_for_root(
                        fragment.selection_set,
                        visited | frozenset((fragment_name,)),
                    )

    for field_node in getattr(info, "field_nodes", ()):
        inspect_for_root(field_node.selection_set, frozenset())
    return property_names


@dataclass(frozen=True, slots=True)
class DependencyCachePrefetchPlan:
    """A dependency-cache key planned for one GraphQL list item field."""

    cache_key: str
    instance: GeneralManager
    property_name: str


def plan_dependency_cache_prefetches(
    instances: Iterable[GeneralManager],
    manager_class: type[object],
    property_names: Iterable[str],
    *,
    can_read_field: Callable[[GeneralManager, str], bool],
) -> dict[str, DependencyCachePrefetchPlan]:
    """Build dependency-cache key plans for selected readable properties.

    ``instances`` is materialized once so one-shot iterables can be reused for
    multiple selected properties. ``property_names`` is materialized in caller
    order so duplicate dependency-cache keys keep the first selected property.
    ``instances`` are expected to be materialized ``GeneralManager`` instances
    from the returned GraphQL page; non-manager objects are outside the generated
    resolver contract even if a custom call path can build cache keys for them.
    Only selected names that resolve to ``GraphQLProperty`` objects with
    ``cache="dependency"`` are planned. The ``can_read_field`` callback is
    called for every candidate instance/property pair before a cache key is
    generated; denied fields are skipped. Plans are keyed by the exact
    dependency-cache key, so duplicate keys collapse to the first planned
    instance/property pair. Managers without an ``Interface`` or without matching
    dependency-cached properties return an empty mapping.
    Exceptions from ``Interface.get_graph_ql_properties()``,
    ``can_read_field()``, ``GraphQLProperty._get_cached_fget()``, or
    ``make_cache_key()`` propagate unchanged.
    """
    available_properties = _get_graphql_properties(manager_class)
    instance_list = list(instances)
    selected = tuple(dict.fromkeys(property_names))
    plans: dict[str, DependencyCachePrefetchPlan] = {}

    for property_name in selected:
        prop = available_properties.get(property_name)
        if not isinstance(prop, GraphQLProperty):
            continue
        if prop.cache != "dependency":
            continue

        cached_getter = prop._get_cached_fget()
        for instance in instance_list:
            if not can_read_field(instance, property_name):
                continue
            cache_key = make_cache_key(cached_getter, (instance,), {})
            plans.setdefault(
                cache_key,
                DependencyCachePrefetchPlan(
                    cache_key=cache_key,
                    instance=instance,
                    property_name=property_name,
                ),
            )
    return plans


DependencyCacheBulkReader = Callable[
    [DependencyCacheBackend, Iterable[str]],
    dict[str, DependencyCacheHit],
]


def prefetch_dependency_cache_hits(
    plans: Mapping[str, DependencyCachePrefetchPlan],
    *,
    cache_backend: DependencyCacheBackend = django_cache,
    reader: DependencyCacheBulkReader | None = None,
) -> dict[str, DependencyCacheHit]:
    """Bulk-read planned dependency-cache hits into the active run context.

    Returns an empty mapping without calling ``reader`` when there is no active
    ``CalculationRunContext`` or when no plans were supplied. Otherwise it reads
    all planned keys in insertion order, stores every hit returned by the reader
    in the active run context (including extra keys a custom reader may return),
    and returns the same hits mapping. The default reader is
    ``read_many_dependency_cache_hits`` against Django's configured cache
    backend. Exceptions from ``current_calculation_run_context()``, the reader,
    or ``context.set_dependency_cache_hits()`` propagate unchanged.
    """
    context = current_calculation_run_context()
    if context is None or not plans:
        return {}

    if reader is None:
        reader = read_many_dependency_cache_hits
    cache_keys = tuple(plans.keys())
    hits = reader(cache_backend, cache_keys)
    if hits:
        context.set_dependency_cache_hits(hits)
    return hits
