from typing import Any, Callable, get_type_hints


class GraphQLProperty(property):
    def __init__(self, fget: Callable[..., Any], doc: str | None = None):
        super().__init__(fget, doc=doc)
        self.is_graphql_resolver = True
        self.graphql_type_hint = get_type_hints(fget).get("return", None)


def graphQlProperty(func: Callable[..., Any]):
    from general_manager.cache.cacheDecorator import cached

    """
    Dekorator für GraphQL-Feld-Resolver, der automatisch:
    - die Methode als benutzerdefiniertes Property registriert,
    - die Resolver-Informationen speichert,
    - den Field-Typ aus dem Type-Hint ableitet.
    """
    return GraphQLProperty(cached()(func))
