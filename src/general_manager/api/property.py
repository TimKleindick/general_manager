from typing import Any, Callable, get_type_hints


class GraphQLProperty(property):
    def __init__(
        self,
        fget: Callable[..., Any],
        doc: str | None = None,
        *,
        filterable: bool = False,
        sortable: bool = False,
        query_annotation: Any | None = None,
    ) -> None:
        super().__init__(fget, doc=doc)
        self.is_graphql_resolver = True
        self.graphql_type_hint = get_type_hints(fget).get("return", None)
        self.filterable = filterable
        self.sortable = sortable
        self.query_annotation = query_annotation


def graphQlProperty(
    func: Callable[..., Any] | None = None,
    *,
    filterable: bool = False,
    sortable: bool = False,
    query_annotation: Any | None = None,
) -> GraphQLProperty | Callable[[Callable[..., Any]], GraphQLProperty]:
    from general_manager.cache.cacheDecorator import cached

    """Decorator to create a :class:`GraphQLProperty`.

    It can be used without arguments or with optional configuration for
    filtering, sorting and queryset annotation.
    """

    def wrapper(f: Callable[..., Any]) -> GraphQLProperty:
        return GraphQLProperty(
            cached()(f),
            filterable=filterable,
            sortable=sortable,
            query_annotation=query_annotation,
        )

    if func is None:
        return wrapper
    return wrapper(func)
