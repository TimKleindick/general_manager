"""
Standalone error classes, error-category constants, GraphQL scalar types, and
pure utility functions extracted from ``api/graphql.py``.

Nothing in this module imports from ``general_manager.api.graphql``, which
makes it safe to import from both ``graphql.py`` and ``graphql_mutations.py``
without introducing circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TYPE_CHECKING, Type

import graphene  # type: ignore[import]
from graphql.language import ast
from graphql import GraphQLError
from django.core.exceptions import ValidationError

from general_manager.logging import get_logger
from general_manager.measurement.measurement import Measurement

if TYPE_CHECKING:
    from general_manager.permission.base_permission import (
        BasePermission,
        ReadPermissionPlan,
    )
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.graphql")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SubscriptionEvent:
    """Payload delivered to GraphQL subscription resolvers."""

    item: Any | None
    action: str


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class InvalidMeasurementValueError(TypeError):
    """Raised when a scalar receives a value that is not a Measurement instance."""

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


# ---------------------------------------------------------------------------
# Error-category tuples
# ---------------------------------------------------------------------------

EXPECTED_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    PermissionError,
    ValidationError,
    ValueError,
    LookupError,
    GraphQLError,
)
"""Errors arising from normal business logic (bad input, missing objects, denied access)."""

SUSPICIOUS_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    TypeError,
    AttributeError,
    RuntimeError,
)
"""Errors that *may* indicate real bugs rather than user mistakes."""

HANDLED_MANAGER_ERRORS: tuple[type[Exception], ...] = (
    *EXPECTED_MANAGER_ERRORS,
    *SUSPICIOUS_MANAGER_ERRORS,
)


# ---------------------------------------------------------------------------
# Graphene type classes
# ---------------------------------------------------------------------------


class MeasurementType(graphene.ObjectType):
    value = graphene.Float()
    unit = graphene.String()


class MeasurementScalar(graphene.Scalar):
    """A measurement in format "value unit", e.g. "12.5 m/s"."""

    @staticmethod
    def serialize(value: Measurement) -> str:
        if not isinstance(value, Measurement):
            raise InvalidMeasurementValueError(value)
        return str(value)

    @staticmethod
    def parse_value(value: str) -> Measurement:
        return Measurement.from_string(value)

    @staticmethod
    def parse_literal(node: Any) -> Measurement | None:
        if isinstance(node, ast.StringValueNode):
            return Measurement.from_string(node.value)
        return None


class BigIntScalar(graphene.Scalar):
    """GraphQL scalar for integers outside the built-in GraphQL Int range."""

    @staticmethod
    def serialize(value: int) -> str:
        return str(int(value))

    @staticmethod
    def parse_value(value: str | int) -> int:
        return int(value)

    @staticmethod
    def parse_literal(node: Any) -> int | None:
        if isinstance(node, ast.IntValueNode):
            return int(node.value)
        if isinstance(node, ast.StringValueNode):
            return int(node.value)
        return None


class PageInfo(graphene.ObjectType):
    total_count = graphene.Int(required=True)
    page_size = graphene.Int(required=False)
    current_page = graphene.Int(required=True)
    total_pages = graphene.Int(required=True)


# ---------------------------------------------------------------------------
# Pure utility functions (no registry access)
# ---------------------------------------------------------------------------


def map_field_to_graphene_base_type(
    field_type: type,
    graphql_scalar: str | None = None,
) -> Type[Any]:
    """
    Map a Python interface type to the corresponding Graphene scalar or custom scalar.

    Parameters:
        field_type (type): Python type from the interface to map.

    Returns:
        Type[Any]: The Graphene scalar type used to represent the input type.

    Raises:
        UnsupportedGraphQLFieldTypeError: If ``field_type`` is ``dict``.
    """
    from typing import get_origin

    base_type = get_origin(field_type) or field_type
    if graphql_scalar == "bigint":
        return BigIntScalar
    if not isinstance(base_type, type):
        return graphene.String
    if issubclass(base_type, dict):
        raise UnsupportedGraphQLFieldTypeError(field_type)
    if issubclass(base_type, str):
        return graphene.String
    elif issubclass(base_type, bool):
        return graphene.Boolean
    elif issubclass(base_type, int):
        return graphene.Int
    elif issubclass(base_type, (float, Decimal)):
        return graphene.Float
    elif issubclass(base_type, datetime):
        return graphene.DateTime
    elif issubclass(base_type, date):
        return graphene.Date
    elif issubclass(base_type, Measurement):
        return MeasurementScalar
    else:
        return graphene.String


def handle_graph_ql_error(error: Exception) -> GraphQLError:
    """
    Convert an exception into a GraphQL error with an appropriate ``extensions['code']``.

    Maps:
        ``PermissionError``  → ``"PERMISSION_DENIED"``
        ``ValueError``, ``ValidationError`` → ``"BAD_USER_INPUT"``
        ``TypeError``, ``AttributeError``, ``RuntimeError`` → ``"INTERNAL_SERVER_ERROR"`` (warning)
        other → ``"INTERNAL_SERVER_ERROR"`` (error)
    """
    message = str(error)
    error_name = type(error).__name__
    if isinstance(error, PermissionError):
        logger.info(
            "graphql permission error",
            context={"error": error_name, "message": message},
        )
        return GraphQLError(message, extensions={"code": "PERMISSION_DENIED"})
    elif isinstance(error, (ValueError, ValidationError)):
        logger.warning(
            "graphql user error",
            context={"error": error_name, "message": message},
        )
        return GraphQLError(message, extensions={"code": "BAD_USER_INPUT"})
    elif isinstance(error, SUSPICIOUS_MANAGER_ERRORS):
        logger.warning(
            "graphql caught suspicious error (may indicate a bug)",
            context={"error": error_name, "message": message},
            exc_info=error,
        )
        return GraphQLError(message, extensions={"code": "INTERNAL_SERVER_ERROR"})
    else:
        logger.error(
            "graphql internal error",
            context={"error": error_name, "message": message},
            exc_info=error,
        )
        return GraphQLError(message, extensions={"code": "INTERNAL_SERVER_ERROR"})


def get_read_permission_filter(
    generalManagerClass: Type[GeneralManager],
    info: GraphQLResolveInfo,
) -> ReadPermissionPlan:
    """
    Produce a list of permission-derived filter and exclude mappings.

    Parameters:
        generalManagerClass: Manager class to derive permission filters for.
        info: GraphQL resolver info whose context provides the current user.

    Returns:
        A read-permission plan consisting of queryset/search prefilters plus a
        flag indicating whether per-instance authorization must still run.
    """
    from general_manager.permission.base_permission import ReadPermissionPlan

    PermissionClass: type[BasePermission] | None = getattr(
        generalManagerClass, "Permission", None
    )
    if PermissionClass:
        permission = PermissionClass(generalManagerClass, info.context.user)
        plan_method = getattr(permission, "get_read_permission_plan", None)
        if callable(plan_method):
            plan = plan_method()
            if isinstance(getattr(plan, "filters", None), list) and isinstance(
                getattr(plan, "requires_instance_check", None),
                bool,
            ):
                return plan
        return ReadPermissionPlan(
            filters=permission.get_permission_filter(),
            requires_instance_check=True,
            instance_check_reasons=("no_prefilter_backend",),
        )
    return ReadPermissionPlan(filters=[], requires_instance_check=False)
