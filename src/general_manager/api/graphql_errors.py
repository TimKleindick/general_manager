"""
Standalone error classes, error-category constants, GraphQL scalar types, and
pure utility functions extracted from ``api/graphql.py``.

Nothing in this module imports from ``general_manager.api.graphql``, which
makes it safe to import from both ``graphql.py`` and ``graphql_mutations.py``
without introducing circular dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypedDict, cast
from uuid import uuid4

from graphql import GraphQLError
from graphql.language import ast
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from general_manager.logging import get_logger
from general_manager.measurement.measurement import Measurement
from general_manager.as_of import (
    HistoricalContextConflictError,
    HistoricalMutationError,
    HistoricalReadNotSupportedError,
    InvalidSearchDateError,
)

if TYPE_CHECKING:

    class _GrapheneMountedType:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

    class ObjectType:
        """Typed stand-in for Graphene's untyped ``ObjectType`` base."""

    class Scalar(_GrapheneMountedType):
        """Typed stand-in for Graphene's untyped ``Scalar`` base."""

    class Boolean(_GrapheneMountedType): ...

    class Date(_GrapheneMountedType): ...

    class DateTime(_GrapheneMountedType): ...

    class Float(_GrapheneMountedType): ...

    class Int(_GrapheneMountedType): ...

    class String(_GrapheneMountedType): ...

    class GraphQLContext(Protocol):
        user: object

    class GraphQLResolveInfo(Protocol):
        context: GraphQLContext

    from general_manager.manager.general_manager import GeneralManager
    from general_manager.permission.base_permission import ReadPermissionPlan
else:
    from graphene import (  # type: ignore[import-untyped]
        Boolean,
        Date,
        DateTime,
        Float,
        Int,
        ObjectType,
        Scalar,
        String,
    )

logger = get_logger("api.graphql")

_INTERNAL_ERROR_MESSAGE = "An internal server error occurred."
_PERMISSION_DENIED_MESSAGE = "Permission denied."

GrapheneBaseType: TypeAlias = "_GrapheneMountedType"
GrapheneBaseTypeClass: TypeAlias = type[GrapheneBaseType]
PermissionFilter: TypeAlias = dict[str, object]


class PermissionConstraint(TypedDict, total=False):
    """Optional filter/exclude mappings for one read-permission alternative."""

    filter: PermissionFilter
    exclude: PermissionFilter


PermissionFilterPlan: TypeAlias = list[PermissionConstraint]
ReadPermissionPlanMethod: TypeAlias = Callable[[], object]
BigIntCoercible: TypeAlias = str | bytes | bytearray | int | float | Decimal
GraphQLSubscriptionAction: TypeAlias = str
ValidationFieldNameMapper: TypeAlias = Callable[[str], str]
ValidationFieldErrors: TypeAlias = dict[str, list[str]]


class _ReadPermissionProvider(Protocol):
    """Runtime permission object shape consumed by the GraphQL read helper."""

    def get_permission_filter(self) -> PermissionFilterPlan: ...

    def get_read_permission_plan(self) -> ReadPermissionPlan: ...


class _PermissionFactory(Protocol):
    """Callable permission class shape used by ``get_read_permission_filter``."""

    def __call__(
        self,
        instance: type[GeneralManager],
        request_user: object,
    ) -> _ReadPermissionProvider: ...


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SubscriptionEvent:
    """Payload delivered to GraphQL subscription resolvers.

    Args:
        item: Changed manager object or ``None`` when the snapshot or channel
            message cannot be instantiated. Delete-style events may still carry
            an instantiated item when the channel payload includes enough
            identification data.
        action: Open channel-layer action string. Generated detail subscriptions
            first yield ``"snapshot"`` and then forward subsequent data-change
            action strings such as ``"update"`` or ``"delete"`` unchanged.
    """

    item: object | None
    action: GraphQLSubscriptionAction


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class PublicGraphQLError(GraphQLError):
    """Deliberately public GraphQL failure with a stable client code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, extensions={"code": code})


_HISTORICAL_ERROR_CODES = {
    InvalidSearchDateError: "BAD_USER_INPUT",
    HistoricalContextConflictError: "HISTORICAL_CONTEXT_CONFLICT",
    HistoricalMutationError: "HISTORICAL_MUTATION_FORBIDDEN",
    HistoricalReadNotSupportedError: "HISTORICAL_READ_NOT_SUPPORTED",
}


def historical_graphql_error(error: Exception) -> PublicGraphQLError | None:
    """Map a historical-context exception to a stable public GraphQL error."""
    for error_type, code in _HISTORICAL_ERROR_CODES.items():
        if isinstance(error, error_type):
            return PublicGraphQLError(_safe_exception_message(error), code=code)
    return None


class InvalidMeasurementValueError(TypeError):
    """Internal scalar error for serializing a non-``Measurement`` value.

    This exception is documented so maintainers can preserve scalar behavior, but
    it is not exported from ``general_manager.api`` and has no stable public
    import path.
    """

    def __init__(self, value: object) -> None:
        super().__init__(f"Expected Measurement, got {type(value).__name__}.")


class MissingChannelLayerError(RuntimeError):
    """Raised when GraphQL subscriptions run without a configured channel layer."""

    def __init__(self) -> None:
        super().__init__(
            "No channel layer configured. "
            "Add a CHANNEL_LAYERS setting to use GraphQL subscriptions."
        )


class UnsupportedGraphQLFieldTypeError(TypeError):
    """Raised when attempting to map an unsupported Python type to GraphQL."""

    def __init__(self, field_type: type) -> None:
        super().__init__(
            f"GraphQL does not support dict fields (received {field_type.__name__})."
        )


class InvalidGeneralManagerClassError(TypeError):
    """Raised when a non-GeneralManager class is passed to GraphQL interface creation."""

    def __init__(self, received_class: type) -> None:
        super().__init__(
            f"{received_class.__name__} must be a subclass of GeneralManager "
            "to create a GraphQL interface."
        )


class MissingManagerIdentifierError(ValueError):
    """Raised when a GraphQL mutation is missing the required manager identifier."""

    def __init__(self) -> None:
        super().__init__("id is required.")


_BIG_INT_ERROR_VALUE_UNSET = object()


class InvalidBigIntScalarValueError(TypeError):
    """Internal scalar error for BigInt values rejected before ``int(...)``.

    This exception is documented so maintainers can preserve scalar behavior, but
    it is not exported from ``general_manager.api`` and has no stable public
    import path.
    """

    def __init__(self, value: object = _BIG_INT_ERROR_VALUE_UNSET) -> None:
        if value is _BIG_INT_ERROR_VALUE_UNSET:
            super().__init__("BigIntScalar cannot accept boolean values.")
        else:
            super().__init__(
                f"BigIntScalar cannot coerce {type(value).__name__} values."
            )


class InvalidReadPermissionConfigurationError(TypeError):
    """Raised when a manager declares an unusable GraphQL Permission class."""

    def __init__(self, manager_name: str) -> None:
        super().__init__(f"{manager_name}.Permission must be callable when set.")


# ---------------------------------------------------------------------------
# Error-category tuples
# ---------------------------------------------------------------------------

EXPECTED_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    PermissionError,
    ValidationError,
    GraphQLError,
)
"""Compatibility metadata for permission, validation, and explicit errors."""

SUSPICIOUS_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    RuntimeError,
)
"""Compatibility metadata for value, lookup, type, attribute, and runtime errors."""

HANDLED_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    *EXPECTED_MANAGER_ERRORS,
    *SUSPICIOUS_MANAGER_ERRORS,
)
"""Expected manager errors followed by suspicious manager errors for internal handling."""


# ---------------------------------------------------------------------------
# Graphene type classes
# ---------------------------------------------------------------------------


class MeasurementType(ObjectType):
    """GraphQL object wrapper exposing measurement magnitude and unit text.

    ``value`` is emitted through Graphene ``Float`` and may lose Decimal
    precision. ``unit`` is the measurement's GeneralManager public unit string.
    """

    value = Float()
    unit = String()


class MeasurementScalar(Scalar):
    """Serialize and parse ``Measurement`` values as ``"<value> <unit>"`` text.

    Stable user imports are available from ``general_manager.api`` and
    ``general_manager.api.graphql``. The published direct-call contract is
    exactly ``parse_value(value: str) -> Measurement`` even though Graphene may
    call scalar hooks dynamically at runtime. Non-string Graphene runtime calls
    are outside the documented user API.

    ``serialize()`` accepts only ``Measurement`` instances and returns their
    public string representation; it raises ``InvalidMeasurementValueError``
    for every other value. ``parse_value()`` is the public variable-input parser,
    accepts a string, and returns a ``Measurement``. Type checkers should reject
    non-string direct calls. Runtime non-string calls are unsupported; their
    exact exception class is unspecified and Graphene reports them to clients as
    scalar input coercion failures on the supplied variable. Valid strings are
    delegated to ``Measurement.from_string()``,
    so ``InvalidDimensionlessValueError`` and ``InvalidMeasurementStringError``
    from the measurement module propagate unchanged for malformed text.
    ``parse_literal()`` accepts ``graphql.language.ast.StringValueNode`` only and
    returns ``Measurement | None``; other literal nodes return ``None`` so
    Graphene can treat the literal as invalid.
    """

    @staticmethod
    def serialize(value: object) -> str:
        if not isinstance(value, Measurement):
            raise InvalidMeasurementValueError(value)
        return str(value)

    @staticmethod
    def parse_value(value: str) -> Measurement:
        return Measurement.from_string(value)

    @staticmethod
    def parse_literal(node: object) -> Measurement | None:
        if isinstance(node, ast.StringValueNode):
            return Measurement.from_string(node.value)
        return None


class BigIntScalar(Scalar):
    """GraphQL scalar for integers outside the built-in GraphQL ``Int`` range.

    Stable user imports are available from ``general_manager.api.graphql``.

    ``serialize()`` returns a string so clients do not lose precision beyond
    GraphQL's built-in ``Int`` range. ``parse_value()`` returns an ``int``.
    Runtime values accepted by both methods are strings, bytes, bytearrays,
    integers, floats, or ``Decimal`` values; they are coerced with Python
    ``int(value)`` after rejecting booleans. Accepted string/bytes/bytearray
    grammar, whitespace, signs, base-10 parsing, ``NaN``, and infinities therefore
    follow Python ``int(value)`` exactly. Prefixes such as ``"0x10"`` are not
    auto-detected because no explicit base is passed. Invalid numeric text and
    non-finite floats/decimals propagate Python ``ValueError`` or ``OverflowError``
    rather than being normalized to ``InvalidBigIntScalarValueError``. Fractional floats
    and ``Decimal`` values are accepted only for compatibility, not as
    integral-value validation; that truncation behavior is an intentional
    compatibility guarantee for current releases. For example,
    ``Decimal("1.9")`` and ``1.9`` both parse as ``1``. Booleans and objects
    outside the accepted coercible types raise
    ``InvalidBigIntScalarValueError``. ``parse_literal()`` accepts
    ``graphql.language.ast.IntValueNode`` and ``StringValueNode`` only and
    returns ``int | None``; invalid accepted literal values propagate Python
    coercion errors, while unsupported AST node types return ``None``.
    """

    @staticmethod
    def serialize(value: object) -> str:
        if isinstance(value, bool):
            raise InvalidBigIntScalarValueError()
        if not isinstance(value, (str, bytes, bytearray, int, float, Decimal)):
            raise InvalidBigIntScalarValueError(value)
        return str(int(value))

    @staticmethod
    def parse_value(value: BigIntCoercible) -> int:
        if isinstance(value, bool):
            raise InvalidBigIntScalarValueError()
        return int(value)

    @staticmethod
    def parse_literal(node: object) -> int | None:
        if isinstance(node, ast.IntValueNode):
            return int(node.value)
        if isinstance(node, ast.StringValueNode):
            return int(node.value)
        return None


class PageInfo(ObjectType):
    """Pagination metadata returned by generated list/page GraphQL fields.

    This is a generated/internal Graphene object type and is not a stable public
    import path. It may appear in generated schema/reference output because
    generated page fields expose it, but callers should not import it directly.

    ``total_count`` is counted after permission filters, client filters, excludes,
    sorting, and grouping, but before slicing the current page. Out-of-range pages
    therefore return empty ``items`` with the same filtered ``total_count``.
    ``current_page`` is 1-based and defaults to ``1`` when the client omits a
    page argument. ``page_size`` is nullable and remains ``None`` when no explicit
    page size was requested. ``total_pages`` is computed from ``page_size`` when
    present; without ``page_size`` it is ``1``, including empty result sets.
    Pagination argument validation belongs to generated field/resolver code, not
    this metadata type. Generated fields currently add no validation beyond
    Graphene's integer coercion. Non-positive values are not normalized here.
    ``page_size=0`` behaves as omitted pagination for ``total_pages`` because it
    is falsy. Negative ``page_size`` or ``current_page`` values are passed to
    resolver slicing unchanged and should be treated as internal/legacy behavior
    rather than a public pagination contract.
    """

    total_count = Int(required=True)
    page_size = Int(required=False)
    current_page = Int(required=True)
    total_pages = Int(required=True)


# ---------------------------------------------------------------------------
# Pure utility functions (no registry access)
# ---------------------------------------------------------------------------


def map_field_to_graphene_base_type(
    field_type: object,
    graphql_scalar: str | None = None,
) -> GrapheneBaseTypeClass:
    """Map a Python interface type to a Graphene scalar class.

    This is a private extracted helper used by the canonical GraphQL module; it
    is documented here so maintainers can preserve behavior, not as a stable
    import path.

    Args:
        field_type: Python annotation or concrete type from interface metadata.
            ``typing`` aliases are reduced with ``typing.get_origin()`` before
            subclass checks run. ``Optional[T]``, ``Union``, ``Annotated``, and
            ``Literal`` currently fall back to ``String`` unless their origin is
            one of the supported concrete classes below. For example,
            ``Optional[int]``/``int | None``, ``Annotated[int, ...]``, and
            ``list[int]`` all fall back to ``String`` here; optional handling,
            annotation unwrapping, and list wrapping are handled by higher-level
            GraphQL builders before they call this helper.
        graphql_scalar: Optional scalar override from interface metadata. The
            only recognized override is ``"bigint"``, which returns
            ``BigIntScalar`` regardless of ``field_type``. Unknown override
            strings are ignored.

    Returns:
        A Graphene scalar class type that callers instantiate to build fields.
        Supported mappings are ``str`` to ``String``,
        ``bool`` to ``Boolean``, ``int`` to ``Int``, ``float``/``Decimal`` to
        ``Float``, ``datetime`` to ``DateTime``, ``date`` to ``Date``, and
        ``Measurement`` to ``MeasurementScalar``. Subclasses follow the same
        mappings. Unknown and non-class inputs fall back to ``String``.

    Raises:
        UnsupportedGraphQLFieldTypeError: If ``field_type`` is ``dict`` or a
        ``dict`` subclass. Other unsupported types fall back to ``String`` for
        backwards compatibility.
    """
    from typing import get_origin

    base_type = get_origin(field_type) or field_type
    if graphql_scalar == "bigint":
        return BigIntScalar
    if not isinstance(base_type, type):
        return String
    if issubclass(base_type, dict):
        raise UnsupportedGraphQLFieldTypeError(base_type)
    if issubclass(base_type, str):
        return String
    elif issubclass(base_type, bool):
        return Boolean
    elif issubclass(base_type, int):
        return Int
    elif issubclass(base_type, (float, Decimal)):
        return Float
    elif issubclass(base_type, datetime):
        return DateTime
    elif issubclass(base_type, date):
        return Date
    elif issubclass(base_type, Measurement):
        return MeasurementScalar
    else:
        return String


def _validation_messages_to_strings(messages: object) -> list[str]:
    """Convert Django validation message values to plain strings."""
    if isinstance(messages, ValidationError):
        return [str(message) for message in messages.messages]
    if isinstance(messages, str):
        return [messages]
    if isinstance(messages, bytes):
        return [messages.decode()]
    if isinstance(messages, Iterable):
        return [str(message) for message in messages]
    return [str(messages)]


def _build_validation_error_extensions(
    message_dict: Mapping[str, object],
    *,
    field_name_mapper: ValidationFieldNameMapper | None,
) -> dict[str, object]:
    """Convert validation messages into GraphQL BAD_USER_INPUT extensions.

    Per-field keys are remapped with ``field_name_mapper`` when provided.
    ``NON_FIELD_ERRORS`` messages are separated into ``nonFieldErrors`` and are
    not remapped. The returned mapping always includes ``code``,
    ``fieldErrors``, and ``nonFieldErrors`` keys.
    """
    field_errors: ValidationFieldErrors = {}
    non_field_errors: list[str] = []

    for field_name, messages in message_dict.items():
        message_list = _validation_messages_to_strings(messages)
        if field_name == NON_FIELD_ERRORS:
            non_field_errors.extend(message_list)
            continue

        schema_field_name = (
            field_name_mapper(field_name)
            if field_name_mapper is not None
            else field_name
        )
        field_errors.setdefault(schema_field_name, []).extend(message_list)

    return {
        "code": "BAD_USER_INPUT",
        "fieldErrors": field_errors,
        "nonFieldErrors": non_field_errors,
    }


def _safe_exception_message(error: Exception) -> str:
    """Render an exception for server logs without risking a secondary failure."""
    try:
        return str(error)
    except Exception:  # noqa: BLE001 - rendering must not break error sanitization
        return "Exception message unavailable."


def handle_graph_ql_error(
    error: Exception,
    *,
    field_name_mapper: ValidationFieldNameMapper | None = None,
) -> GraphQLError:
    """Convert a handled exception into a GraphQL error.

    This private extracted helper is used by the canonical GraphQL module and is
    not a stable import path. The behavior documented here is a
    generated-resolver compatibility note, not a stable direct-import guarantee.

    Explicit ``GraphQLError`` instances are returned as the same object,
    preserving their message and existing ``extensions`` mapping. Converted
    exceptions always include an ``extensions["code"]`` value. Django
    ``ValidationError`` instances with ``message_dict`` use
    ``"Validation failed."`` with ``BAD_USER_INPUT`` plus ``fieldErrors`` and
    ``nonFieldErrors``. When ``field_name_mapper`` is provided, it maps
    ``message_dict`` field keys before they are emitted in ``fieldErrors``;
    non-field errors are not mapped. ``PermissionError`` instances use a fixed
    public message. All other exceptions use a generic internal-error message
    and an opaque correlation ID. Logging level/category is diagnostic behavior
    of the internal ``api.graphql`` logger and is not a public API contract.
    """
    message = _safe_exception_message(error)
    error_name = type(error).__name__
    if isinstance(error, GraphQLError):
        logger.warning(
            "graphql explicit error",
            context={"error": error_name, "message": message},
        )
        return error
    elif isinstance(error, ValidationError):
        logger.warning(
            "graphql user error",
            context={"error": error_name, "message": message},
        )
        if hasattr(error, "error_dict"):
            return GraphQLError(
                "Validation failed.",
                extensions=_build_validation_error_extensions(
                    error.message_dict,
                    field_name_mapper=field_name_mapper,
                ),
            )
        return GraphQLError(message, extensions={"code": "BAD_USER_INPUT"})
    elif isinstance(error, PermissionError):
        logger.info(
            "graphql permission error",
            context={"error": error_name, "message": message},
        )
        return GraphQLError(
            _PERMISSION_DENIED_MESSAGE,
            extensions={"code": "PERMISSION_DENIED"},
        )

    error_id = uuid4().hex
    logger.error(
        "graphql internal error",
        context={"error": error_name, "message": message, "error_id": error_id},
        exc_info=error,
    )
    return GraphQLError(
        _INTERNAL_ERROR_MESSAGE,
        extensions={"code": "INTERNAL_SERVER_ERROR", "errorId": error_id},
    )


def get_read_permission_filter(
    generalManagerClass: type[GeneralManager],
    info: GraphQLResolveInfo,
) -> ReadPermissionPlan:
    """Build the read-permission plan for a generated GraphQL resolver.

    This private extracted helper is used by generated resolvers and is not a
    stable import path. The behavior documented here is a generated-resolver
    compatibility note, not a stable direct-import guarantee.

    The function name is legacy: it now returns the full ``ReadPermissionPlan``
    instead of only a filter list. Internal compatibility callers that expected a
    list should read ``get_read_permission_filter(...).filters``.

    Args:
        generalManagerClass: Manager class whose optional ``Permission``
            attribute is read with ``getattr(generalManagerClass, "Permission",
            None)``. Inherited class attributes therefore apply. ``None`` means
            no permission factory is configured. Non-callable non-``None``
            values raise ``InvalidReadPermissionConfigurationError``. Callable
            permission factories are invoked positionally as
            ``Permission(generalManagerClass, info.context.user)``.
        info: Resolver info object with a context exposing ``user``.

    Returns:
        A ``ReadPermissionPlan`` with ``filters``, ``requires_instance_check``,
        and ``instance_check_reasons`` fields. ``ReadPermissionPlan`` is an
        internal adapter from ``general_manager.permission.base_permission``, not
        a stable public import path, but generated resolvers rely on these
        fields.

        The custom ``ReadPermissionPlan`` returned by the zero-argument
        permission instance method ``get_read_permission_plan()`` when it returns
        a ``ReadPermissionPlan`` instance. Other return values are ignored and
        the helper falls back to the zero-argument permission instance method
        ``get_permission_filter()``. If ``get_permission_filter`` is missing,
        the resulting ``AttributeError`` propagates. ``get_permission_filter()``
        must return the legacy ``list[PermissionConstraint]`` shape accepted by
        ``ReadPermissionPlan.filters``. Each legacy entry may include optional
        ``"filter"`` and ``"exclude"`` mappings of lookup names to values.
        Missing keys are later treated as empty mappings by generated resolvers.
        This helper does not validate malformed legacy constraints; non-mapping
        values or invalid lookup keys fail later when resolver code applies them
        to the bucket/search backend. The fallback plan requires a per-instance
        read check and sets ``instance_check_reasons`` to exactly
        ``("no_prefilter_backend",)``.

        Managers without a permission factory receive an empty filter plan and
        do not require instance checks. This is a default-allow read policy for
        managers that do not define ``Permission``; no additional GraphQL
        permission filtering or per-object read authorization is applied by this
        helper. More generally, ``filters=[]`` with
        ``requires_instance_check=True`` means generated resolvers start with the
        original queryset and then run per-object read checks for every
        candidate row. That later row gate instantiates the same Permission class
        with ``(instance, info.context.user)`` and calls ``can_read_instance()``;
        false returns deny that row, and exceptions propagate to the generated
        resolver's normal error handling.

    Raises:
        AttributeError: If ``info.context`` has no ``user`` attribute.
        TypeError: If the manager ``Permission`` attribute exists but is not
            callable in the expected two-argument form.
        Exception: Exceptions raised by the permission constructor,
            ``get_read_permission_plan()``, or ``get_permission_filter()``
            propagate unchanged.
    """
    from general_manager.permission.base_permission import ReadPermissionPlan

    permission_attribute: object = getattr(generalManagerClass, "Permission", None)
    if callable(permission_attribute):
        PermissionClass = cast(_PermissionFactory, permission_attribute)
        permission = PermissionClass(generalManagerClass, info.context.user)
        plan_method: object = getattr(permission, "get_read_permission_plan", None)
        if callable(plan_method):
            get_plan = cast(ReadPermissionPlanMethod, plan_method)
            plan = get_plan()
            if isinstance(plan, ReadPermissionPlan):
                return plan
        return ReadPermissionPlan(
            filters=permission.get_permission_filter(),
            requires_instance_check=True,
            instance_check_reasons=("no_prefilter_backend",),
        )
    if permission_attribute is not None:
        raise InvalidReadPermissionConfigurationError(generalManagerClass.__name__)
    return ReadPermissionPlan(filters=[], requires_instance_check=False)
