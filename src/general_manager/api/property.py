"""GraphQL-aware property descriptor used by GeneralManager classes.

The stable public entry point is `graph_ql_property`. The decorator-specific
exception classes are public when imported from `general_manager.api`. Other
names in this module are importable implementation details used by the public
decorator and internal schema introspection code unless they are listed in the
public API registry.
"""

from functools import wraps
import sys
from typing import Callable, Literal, TypeVar, cast, get_type_hints, overload

T = TypeVar("T", bound=Callable[..., object])
GraphQLPropertyCache = Literal["dependency", "run", "timeout", "none"]
_TYPE_HINT_UNRESOLVED = object()


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


class GraphQLPropertyWarmUpConfigurationError(ValueError):
    """Raised when warm-up is configured for an unsupported cache scope."""

    def __init__(self) -> None:
        """Build the warm-up cache-scope validation error."""
        super().__init__('warm_up=True requires cache="dependency" or cache="timeout"')


class GraphQLPropertyTimeoutConfigurationError(ValueError):
    """Raised when timeout cache configuration is invalid."""

    @classmethod
    def missing_timeout(cls) -> "GraphQLPropertyTimeoutConfigurationError":
        """Build the error raised when timeout cache omits a timeout."""
        return cls('cache="timeout" requires timeout')

    @classmethod
    def unexpected_timeout(cls) -> "GraphQLPropertyTimeoutConfigurationError":
        """Build the error raised when non-timeout caches specify a timeout."""
        return cls('timeout is only supported with cache="timeout"')


class GraphQLProperty(property):
    """Descriptor that exposes a resolver with GraphQL metadata and caching.

    Stable inspection attributes are `sortable`, `filterable`,
    `query_annotation`, `cache`, `timeout`, `warm_up`, and `graphql_type_hint`.
    The descriptor preserves normal `property` behavior such as `fget` and
    `__doc__`, but those values follow Python's property/wraps mechanics rather
    than a GeneralManager-specific metadata contract. Metadata attributes are
    mutable implementation state; application code should treat them as
    read-only after class definition.
    """

    sortable: bool
    filterable: bool
    warm_up: bool
    query_annotation: object | None
    timeout: int | None

    def __init__(
        self,
        fget: Callable[..., object],
        doc: str | None = None,
        *,
        sortable: bool = False,
        filterable: bool = False,
        query_annotation: object | None = None,
        cache: GraphQLPropertyCache = "none",
        timeout: int | None = None,
        warm_up: bool = False,
    ) -> None:
        """Initialize the descriptor with GraphQL-specific metadata.

        Direct construction is an implementation path. Unlike
        `graph_ql_property`, it defaults to `cache="none"` because the public
        decorator is responsible for applying the normal run-cache default.

        Args:
            fget: Resolver function. Its unwrapped callable must declare a
                return annotation; the annotation is used for GraphQL schema
                generation when it can be resolved. Passing a non-callable is
                ordinary caller misuse and raises the Python error produced by
                `property`/`functools.wraps`.
            doc: Optional documentation string exposed on the descriptor.
            sortable: Whether generated GraphQL fields may expose sorting for
                this property. Stored as supplied; runtime truthiness is not
                coerced or validated.
            filterable: Whether generated GraphQL fields may expose filtering
                for this property. Stored as supplied; runtime truthiness is not
                coerced or validated.
            query_annotation: Opaque ORM annotation object or callable consumed
                by database bucket query construction.
            cache: Resolver cache scope. `"none"` evaluates every access,
                `"run"` memoizes inside the active calculation run, `"dependency"`
                persists with dependency tracking, and `"timeout"` persists for
                `timeout` seconds.
            timeout: Timeout in seconds. Required with `cache="timeout"` and
                invalid with every other cache scope. Non-`None` values are
                delegated to the shared cache decorator and cache backend; this
                descriptor does not reject zero, negative, boolean, or non-int
                values itself.
            warm_up: Whether proactive GraphQL warm-up may precompute this
                property. Only dependency and timeout caches support warm-up.
                Runtime truthiness is used; values are not coerced to `bool`.

        Raises:
            GraphQLPropertyReturnAnnotationError: If `fget` has no return
                annotation.
            GraphQLPropertyWarmUpConfigurationError: If `warm_up=True` is used
                with `"run"` or `"none"` caching. This validation runs before
                timeout/cache decorator validation.
            GraphQLPropertyTimeoutConfigurationError: If timeout configuration
                is missing for `"timeout"` caching or supplied for another cache
                scope. Unexpected-timeout validation runs before unsupported
                cache-scope validation from the shared cache decorator.
            ValueError: Propagated from the shared cache decorator for an
                unsupported runtime cache scope.
        """
        if warm_up and cache not in {"dependency", "timeout"}:
            raise GraphQLPropertyWarmUpConfigurationError()
        if cache == "timeout" and timeout is None:
            raise GraphQLPropertyTimeoutConfigurationError.missing_timeout()
        if timeout is not None and cache != "timeout":
            raise GraphQLPropertyTimeoutConfigurationError.unexpected_timeout()

        self._raw_fget = fget
        self._cached_fget: Callable[..., object] | None = None

        @wraps(fget)
        def resolver(instance: object) -> object:
            """Resolve the property through the selected cached wrapper."""
            cached_fget = self._cached_fget
            if cached_fget is None:
                cached_fget = self._get_cached_fget()
            return cached_fget(instance)

        super().__init__(resolver, doc=doc)
        self.is_graphql_resolver = True
        self._owner: type | None = None
        self._name: str | None = None
        self._graphql_type_hint: object = _TYPE_HINT_UNRESOLVED

        self.sortable = sortable
        self.filterable = filterable
        self.query_annotation = query_annotation
        self.cache = cache
        self.timeout = timeout
        self.warm_up = warm_up

        orig = getattr(fget, "__wrapped__", fget)
        ann = getattr(orig, "__annotations__", {}) or {}
        if "return" not in ann:
            raise GraphQLPropertyReturnAnnotationError()

    def __set_name__(self, owner: type, name: str) -> None:
        """Record the owner class and attribute name for introspection.

        Args:
            owner: Class that owns this descriptor.
            name: Attribute name under which this descriptor is assigned.
        """
        self._owner = owner
        self._name = name
        self._cached_fget = self._build_cached_fget(owner)

    def _build_cached_fget(self, _owner: type) -> Callable[..., object]:
        """Build the resolver wrapper for the configured cache scope.

        Returns:
            Callable that accepts the manager instance and returns the resolver
            result, applying the selected cache behavior.

        Raises:
            ValueError: Propagated from the shared cache decorator for invalid
                runtime cache configuration.
        """
        from general_manager.cache.cache_decorator import cached

        selected_cache = self.cache
        if selected_cache == "none":
            return self._raw_fget
        if selected_cache == "timeout":
            timeout_decorator = cast(
                Callable[[Callable[..., object]], Callable[..., object]],
                cached(cache="timeout", timeout=self.timeout),
            )
            return timeout_decorator(self._raw_fget)
        run_decorator = cast(
            Callable[[Callable[..., object]], Callable[..., object]],
            cached(cache=selected_cache),
        )
        return run_decorator(self._raw_fget)

    def _get_cached_fget(self) -> Callable[..., object]:
        """Return the cached resolver wrapper, building it lazily if needed."""
        if self._cached_fget is None:
            if self._owner is None:
                self._cached_fget = self._raw_fget
            else:
                self._cached_fget = self._build_cached_fget(self._owner)
        return self._cached_fget

    def _try_resolve_type_hint(self) -> None:
        """Resolve and cache the wrapped resolver's return type hint.

        Resolution uses the resolver module globals and owner class namespace.
        It runs on first `graphql_type_hint` access, not during descriptor
        construction. `AttributeError`, `KeyError`, `NameError`, `TypeError`,
        and `ValueError` from `typing.get_type_hints` are swallowed and cached
        as `None`, so repeated property metadata access does not retry unresolved
        forward references or local aliases. Other exceptions propagate.
        """
        if self._graphql_type_hint is not _TYPE_HINT_UNRESOLVED:
            return

        try:
            mod = sys.modules.get(self.fget.__module__)
            globalns = vars(mod) if mod else {}

            localns: dict[str, object] = {}
            if self._owner is not None:
                localns = dict(self._owner.__dict__)
                localns[self._owner.__name__] = self._owner

            hints = get_type_hints(self.fget, globalns=globalns, localns=localns)
            self._graphql_type_hint = hints.get("return", None)
        except (AttributeError, KeyError, NameError, TypeError, ValueError):
            self._graphql_type_hint = None

    @property
    def graphql_type_hint(self) -> object | None:
        """Return the cached GraphQL type hint resolved from annotations.

        Returns:
            Resolved return annotation for the wrapped resolver, or `None` when
            annotation resolution fails. `None` never means "not resolved yet"
            to callers; first access resolves once and caches either a concrete
            annotation or `None`. A resolver annotated as `-> None` resolves to
            `type(None)`.
        """
        if self._graphql_type_hint is _TYPE_HINT_UNRESOLVED:
            self._try_resolve_type_hint()
        if self._graphql_type_hint is _TYPE_HINT_UNRESOLVED:
            return None
        return self._graphql_type_hint


@overload
def graph_ql_property(func: T) -> GraphQLProperty:
    """Type overload for direct ``@graph_ql_property`` usage."""
    ...


@overload
def graph_ql_property(
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: object | None = None,
    cache: GraphQLPropertyCache = "run",
    timeout: int | None = None,
    warm_up: bool = False,
) -> Callable[[T], GraphQLProperty]:
    """Type overload for configured ``@graph_ql_property(...)`` usage."""
    ...


def graph_ql_property(
    func: Callable[..., object] | None = None,
    *,
    sortable: bool = False,
    filterable: bool = False,
    query_annotation: object | None = None,
    cache: GraphQLPropertyCache = "run",
    timeout: int | None = None,
    warm_up: bool = False,
) -> GraphQLProperty | Callable[[T], GraphQLProperty]:
    """Decorate a resolver as a GraphQL-exposed cached property.

    The decorator supports both `@graph_ql_property` and
    `@graph_ql_property(...)` forms. The wrapped resolver is called with the
    manager instance and must declare a return annotation.

    Args:
        func: Resolver function when the decorator is used without parentheses.
        sortable: Whether generated GraphQL fields may expose sorting for this
            property.
        filterable: Whether generated GraphQL fields may expose filtering for
            this property.
        query_annotation: Opaque ORM annotation object or callable consumed by
            database bucket query construction.
        cache: Cache scope. `"run"` is the default and memoizes only within the
            active GraphQL request, calculation graph, bulk operation, or
            background run. `"dependency"` persists across runs with dependency
            tracking. `"timeout"` persists for `timeout` seconds. `"none"`
            disables caching and evaluates on every access.
        timeout: Timeout in seconds. Required with `cache="timeout"` and invalid
            with every other cache scope. Non-`None` values are delegated to the
            shared cache decorator and cache backend; this decorator does not
            reject zero, negative, boolean, or non-int values itself.
        warm_up: Whether proactive GraphQL warm-up may precompute this property.
            Only dependency and timeout caches support warm-up. Runtime
            truthiness is used; values are not coerced to `bool`.

    Returns:
        A `GraphQLProperty` when decorating a function directly, or a decorator
        that converts one resolver function into a `GraphQLProperty`.

    Raises:
        GraphQLPropertyReturnAnnotationError: If the resolver has no return
            annotation.
        GraphQLPropertyWarmUpConfigurationError: If `warm_up=True` is used with
            `"run"` or `"none"` caching. This validation runs before timeout or
            cache decorator validation when the resolver is wrapped.
        GraphQLPropertyTimeoutConfigurationError: If timeout configuration is
            missing for `"timeout"` caching or supplied for another cache scope.
            Unexpected-timeout validation runs before unsupported cache-scope
            validation from the shared cache decorator.
        ValueError: Propagated from the shared cache decorator for an
            unsupported runtime cache scope.
        TypeError: Raised by ordinary Python call mechanics for unsupported
            decorator forms, for example non-callable `func`.

    Validation of configured decorator options occurs when the returned
    decorator wraps a resolver function, not when `graph_ql_property(...)` is
    called without `func`. Passing `func=None` explicitly is the same as omitting
    `func` and returns a configured decorator.
    """

    def wrapper(f: Callable[..., object]) -> GraphQLProperty:
        """Wrap one resolver function in a GraphQLProperty descriptor."""
        return GraphQLProperty(
            f,
            sortable=sortable,
            query_annotation=query_annotation,
            filterable=filterable,
            cache=cache,
            timeout=timeout,
            warm_up=warm_up,
        )

    if func is None:
        return wrapper
    return wrapper(func)
