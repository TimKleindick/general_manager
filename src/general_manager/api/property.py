from typing import Any, Callable, get_type_hints, overload, TypeVar

T = TypeVar("T", bound=Callable[..., Any])


class GraphQLProperty(property):
    def __init__(
        self,
        fget: Callable[..., Any],
        doc: str | None = None,
        *,
        is_operatable: bool = False,
        query_annotation: Any | None = None,
    ) -> None:
        super().__init__(fget, doc=doc)
        self.is_graphql_resolver = True
        self.graphql_type_hint = get_type_hints(fget).get("return", None)
        self.is_operatable = is_operatable
        self.query_annotation = query_annotation


@overload
def graphQlProperty(func: T) -> GraphQLProperty: ...
@overload
def graphQlProperty(
    *,
    is_operatable: bool = False,
    query_annotation: Any | None = None,
) -> Callable[[T], GraphQLProperty]: ...


def graphQlProperty(
    func: Callable[..., Any] | None = None,
    *,
    is_operatable: bool = False,
    query_annotation: Any | None = None,
) -> GraphQLProperty | Callable[[T], GraphQLProperty]:
    from general_manager.cache.cacheDecorator import cached

    """Decorator to create a :class:`GraphQLProperty`.

    It can be used without arguments or with optional configuration for
    filtering, sorting and queryset annotation.
    """

    def wrapper(f: Callable[..., Any]) -> GraphQLProperty:
        return GraphQLProperty(
            cached()(f),
            is_operatable=is_operatable,
            query_annotation=query_annotation,
        )

    if func is None:
        return wrapper
    return wrapper(func)
