"""GraphQL-aware property descriptor used by GeneralManager classes."""

from functools import wraps
import sys
from typing import Any, Callable, Literal, TypeVar, cast, get_type_hints, overload

T = TypeVar("T", bound=Callable[..., Any])
GraphQLPropertyCache = Literal["dependency", "run", "none"]


class GraphQLPropertyReturnAnnotationError(TypeError):
    """Raised when a GraphQLProperty is defined without a return type annotation."""

    def __init__(self) -> None:
        """
        Indicates a GraphQLProperty-decorated function is missing a return type annotation.

        This exception is raised to signal that a property resolver intended for use with GraphQLProperty must have an explicit return type hint. The exception message is: "GraphQLProperty requires a return type hint for the property function."
        """
        super().__init__(
            "GraphQLProperty requires a return type hint for the property function."
        )


class GraphQLProperty(property):
    """Descriptor that exposes a property with GraphQL metadata and type hints."""

    sortable: bool
    filterable: bool
    query_annotation: Any | None

    def __init__(
        self,
        fget: Callable[..., Any],
        doc: str | None = None,
        *,
        sortable: bool = False,
        filterable: bool = False,
        query_annotation: Any | None = None,
        cache: GraphQLPropertyCache = "none",
    ) -> None:
        """
        Initialize the GraphQLProperty descriptor with GraphQL-specific metadata.

        Parameters:
            fget (Callable[..., Any]): The resolver function to wrap; its unwrapped form must include a return type annotation.
            doc (str | None): Optional documentation string exposed on the descriptor.
            sortable (bool): Whether the property should be considered for sorting.
            filterable (bool): Whether the property should be considered for filtering.
            query_annotation (Any | None): Optional annotation to apply when querying/queryset construction.

        Raises:
            GraphQLPropertyReturnAnnotationError: If the underlying resolver function does not declare a return type annotation.
        """
        self._raw_fget = fget
        self._cached_fget: Callable[..., Any] | None = None

        @wraps(fget)
        def resolver(instance: Any) -> Any:
            return self._get_cached_fget()(instance)

        super().__init__(resolver, doc=doc)
        self.is_graphql_resolver = True
        self._owner: type | None = None
        self._name: str | None = None
        self._graphql_type_hint: Any | None = None

        self.sortable = sortable
        self.filterable = filterable
        self.query_annotation = query_annotation
        self.cache = cache

        orig = getattr(
            fget, "__wrapped__", fget
        )  # falls decorator Annotations durchreicht
        ann = getattr(orig, "__annotations__", {}) or {}
        if "return" not in ann:
            raise GraphQLPropertyReturnAnnotationError()

    def __set_name__(self, owner: type, name: str) -> None:
        """
        Record the owner class and attribute name for the descriptor to support later introspection.

        Parameters:
            owner (type): The class that owns this descriptor.
            name (str): The attribute name under which this descriptor is assigned.
        """
        self._owner = owner
        self._name = name
        self._cached_fget = self._build_cached_fget(owner)

    def _build_cached_fget(self, _owner: type) -> Callable[..., Any]:
        from general_manager.cache.cache_decorator import cached

        selected_cache = self.cache
        if selected_cache == "none":
            return self._raw_fget
        return cached(scope=cast(Literal["dependency", "run"], selected_cache))(
            self._raw_fget
        )

    def _get_cached_fget(self) -> Callable[..., Any]:
        if self._cached_fget is None:
            if self._owner is None:
                self._cached_fget = self._raw_fget
            else:
                self._cached_fget = self._build_cached_fget(self._owner)
        return self._cached_fget

    def _try_resolve_type_hint(self) -> None:
        """
        Resolve and cache the wrapped resolver's return type hint.

        When successful, stores the resolved return annotation on self._graphql_type_hint; if resolution fails or cannot be determined, sets self._graphql_type_hint to None.
        """
        if self._graphql_type_hint is not None:
            return

        try:
            mod = sys.modules.get(self.fget.__module__)
            globalns = vars(mod) if mod else {}

            localns: dict[str, Any] = {}
            if self._owner is not None:
                localns = dict(self._owner.__dict__)
                localns[self._owner.__name__] = self._owner

            hints = get_type_hints(self.fget, globalns=globalns, localns=localns)
            self._graphql_type_hint = hints.get("return", None)
        except (AttributeError, KeyError, NameError, TypeError, ValueError):
            self._graphql_type_hint = None

    @property
    def graphql_type_hint(self) -> Any | None:
        """Return the cached GraphQL type hint resolved from annotations."""
        if self._graphql_type_hint is None:
            self._try_resolve_type_hint()
        return self._graphql_type_hint


@overload
def graph_ql_property(func: T) -> GraphQLProperty: ...
@overload
def graph_ql_property(
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: Any | None = None,
    cache: GraphQLPropertyCache = "run",
) -> Callable[[T], GraphQLProperty]: ...


def graph_ql_property(
    func: Callable[..., Any] | None = None,
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: Any | None = None,
    cache: GraphQLPropertyCache = "run",
) -> GraphQLProperty | Callable[[T], GraphQLProperty]:
    """
    Decorate a resolver to return a cached ``GraphQLProperty`` descriptor.

    Parameters:
        func (Callable[..., Any] | None): Resolver function when used without arguments.
        sortable (bool): Whether the property can participate in sorting.
        filterable (bool): Whether the property can be used in filtering.
        query_annotation (Any | None): Optional queryset annotation callable or expression.
        cache (GraphQLPropertyCache): Cache scope for the resolver. ``"run"`` is the
            default and memoizes the value only within the active GraphQL request,
            calculation graph, bulk operation, or background run. ``"dependency"``
            persists the value across runs and records accessed managers so later
            mutations can invalidate the cache entry. ``"none"`` disables caching
            and evaluates the resolver on every access.

    Returns:
        GraphQLProperty | Callable[[Callable[..., Any]], GraphQLProperty]: Decorated property or decorator factory.
    """

    def wrapper(f: Callable[..., Any]) -> GraphQLProperty:
        return GraphQLProperty(
            f,
            sortable=sortable,
            query_annotation=query_annotation,
            filterable=filterable,
            cache=cache,
        )

    if func is None:
        return wrapper
    return wrapper(func)
