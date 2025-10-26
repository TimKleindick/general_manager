"""GraphQL-aware property descriptor used by GeneralManager classes."""

import sys
from typing import Any, Callable, TypeVar, get_type_hints, overload

T = TypeVar("T", bound=Callable[..., Any])


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
        super().__init__(fget, doc=doc)
        self.is_graphql_resolver = True
        self._owner: type | None = None
        self._name: str | None = None
        self._graphql_type_hint: Any | None = None

        self.sortable = sortable
        self.filterable = filterable
        self.query_annotation = query_annotation

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
) -> Callable[[T], GraphQLProperty]: ...


def graph_ql_property(
    func: Callable[..., Any] | None = None,
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: Any | None = None,
) -> GraphQLProperty | Callable[[T], GraphQLProperty]:
    from general_manager.cache.cache_decorator import cached

    """
    Decorate a resolver to return a cached ``GraphQLProperty`` descriptor.

    Parameters:
        func (Callable[..., Any] | None): Resolver function when used without arguments.
        sortable (bool): Whether the property can participate in sorting.
        filterable (bool): Whether the property can be used in filtering.
        query_annotation (Any | None): Optional queryset annotation callable or expression.

    Returns:
        GraphQLProperty | Callable[[Callable[..., Any]], GraphQLProperty]: Decorated property or decorator factory.
    """

    def wrapper(f: Callable[..., Any]) -> GraphQLProperty:
        return GraphQLProperty(
            cached()(f),
            sortable=sortable,
            query_annotation=query_annotation,
            filterable=filterable,
        )

    if func is None:
        return wrapper
    return wrapper(func)
