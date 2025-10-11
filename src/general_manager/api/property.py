"""GraphQL-aware property descriptor used by GeneralManager classes."""

from typing import Any, Callable, get_type_hints, overload, TypeVar
import sys

T = TypeVar("T", bound=Callable[..., Any])


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
        Initialise the descriptor with GraphQL-specific configuration.

        Parameters:
            fget (Callable): Underlying resolver function.
            doc (str | None): Optional documentation string.
            sortable (bool): Whether the property participates in sorting.
            filterable (bool): Whether the property participates in filtering.
            query_annotation (Any | None): Optional annotation applied to querysets.
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
            raise TypeError(
                "GraphQLProperty requires a return type hint for the property function."
            )

    def __set_name__(self, owner: type, name: str) -> None:
        """Store the owning class and attribute name for later introspection."""
        self._owner = owner
        self._name = name

    def _try_resolve_type_hint(self) -> None:
        """Resolve the return type hint of the wrapped resolver, if available."""
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
        except Exception:
            self._graphql_type_hint = None

    @property
    def graphql_type_hint(self) -> Any | None:
        """Return the cached GraphQL type hint resolved from annotations."""
        if self._graphql_type_hint is None:
            self._try_resolve_type_hint()
        return self._graphql_type_hint


@overload
def graphQlProperty(func: T) -> GraphQLProperty: ...
@overload
def graphQlProperty(
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: Any | None = None,
) -> Callable[[T], GraphQLProperty]: ...


def graphQlProperty(
    func: Callable[..., Any] | None = None,
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: Any | None = None,
) -> GraphQLProperty | Callable[[T], GraphQLProperty]:
    from general_manager.cache.cacheDecorator import cached

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
