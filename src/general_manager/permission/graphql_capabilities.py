"""GraphQL-facing permission capability declarations and evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Literal, cast

from general_manager.logging import get_logger

logger = get_logger("permission.graphql_capabilities")

CapabilityAction = Literal["create", "update", "delete"]
CapabilityPayload = Mapping[str, Any] | Callable[[Any, Any], Mapping[str, Any]]
ObjectCapabilityEvaluator = Callable[[Any, Any], bool]
BatchCapabilityEvaluator = Callable[
    [Sequence[Any], Any], Mapping[Any, bool] | Sequence[bool]
]


@dataclass(frozen=True, slots=True)
class GraphQLPermissionCapability:
    """
    Boolean authorization hint exposed under a GraphQL ``capabilities`` object.

    Capability fields are advisory frontend hints, not authorization gates. The
    evaluator receives the resolved object and request user, and GraphQL returns
    ``false`` if evaluation fails. Use ``description`` to explain the business
    action the field previews so schema introspection remains useful to client
    developers.
    """

    name: str
    evaluator: ObjectCapabilityEvaluator
    batch_evaluator: BatchCapabilityEvaluator | None = None
    description: str | None = None


def object_capability(
    name: str,
    evaluator: ObjectCapabilityEvaluator,
    *,
    batch_evaluator: BatchCapabilityEvaluator | None = None,
    description: str | None = None,
) -> GraphQLPermissionCapability:
    """
    Declare a domain-specific GraphQL capability for one resolved object.

    Use this helper when the capability is a business rule that does not map
    directly to a generated CRUD mutation or custom mutation permission. The
    evaluator is called with ``(instance, user)`` and should return a boolean.
    Provide ``batch_evaluator`` for list pages to avoid repeated per-row policy
    work, and ``description`` to document the generated GraphQL field.
    """
    return GraphQLPermissionCapability(
        name=name,
        evaluator=evaluator,
        batch_evaluator=batch_evaluator,
        description=description or _object_capability_description(name),
    )


def permission_capability(
    target: type[Any],
    action: CapabilityAction,
    *,
    name: str | None = None,
    payload: CapabilityPayload | None = None,
    description: str | None = None,
) -> GraphQLPermissionCapability:
    """
    Declare a GraphQL capability backed by a manager ``Permission`` CRUD check.

    The generated capability previews the same permission method used by the
    corresponding generated mutation: create delegates to
    ``check_create_permission``, update to ``check_update_permission``, and
    delete to ``check_delete_permission``. Pass ``payload`` when create or
    update checks need proposed field values from the current object context.
    ``payload`` may be a mapping or a callable receiving ``(instance, user)``:
    ``instance`` is the object whose GraphQL ``capabilities`` field is being
    resolved, and ``user`` is the authenticated request user after the standard
    permission user lookup. The resolved mapping is passed unchanged to create
    and update permission checks; delete checks ignore it.
    """
    capability_name = name or _default_capability_name(action, target)

    def evaluator(instance: Any, user: Any) -> bool:
        permission_class = getattr(target, "Permission", None)
        if not isinstance(permission_class, type):
            return True
        permission = cast(Any, permission_class)
        resolved_payload = _resolve_payload(payload, instance, user)
        try:
            if action == "create":
                permission.check_create_permission(resolved_payload, target, user)
            elif action == "update":
                permission.check_update_permission(resolved_payload, instance, user)
            else:
                permission.check_delete_permission(instance, user)
        except PermissionError:
            return False
        return True

    return object_capability(
        capability_name,
        evaluator,
        description=description or _permission_capability_description(action, target),
    )


def mutation_capability(
    mutation: Any,
    *,
    name: str | None = None,
    payload: CapabilityPayload | None = None,
    description: str | None = None,
) -> GraphQLPermissionCapability:
    """
    Declare a GraphQL capability backed by a custom mutation permission.

    The capability calls the mutation's configured ``MutationPermission`` with a
    resolved payload and returns whether the current user would pass that check.
    Use it when a boolean field should preview a custom GraphQL action rather
    than generated manager create, update, or delete behavior. ``payload`` may
    be a mapping or a callable receiving ``(instance, user)``: ``instance`` is
    the object whose GraphQL ``capabilities`` field is being resolved, and the
    returned mapping is passed to the mutation permission's ``check`` method.
    """
    capability_name = name or _lower_camel(getattr(mutation, "__name__", "mutation"))

    def evaluator(instance: Any, user: Any) -> bool:
        permission = getattr(mutation, "_general_manager_mutation_permission", None)
        if permission is None and hasattr(mutation, "check"):
            permission = mutation
        if permission is None:
            return True
        resolved_payload = _resolve_payload(payload, instance, user)
        try:
            permission.check(dict(resolved_payload), user)
        except PermissionError:
            return False
        return True

    return object_capability(
        capability_name,
        evaluator,
        description=description or _mutation_capability_description(mutation),
    )


class CapabilityEvaluationContext:
    """Operation-scoped cache for GraphQL permission capability evaluation."""

    def __init__(self, *, user: Any | None = None) -> None:
        from django.contrib.auth.models import AnonymousUser

        from general_manager.permission.base_permission import BasePermission

        self.user = (
            BasePermission.get_user_with_id(user)
            if user is not None
            else AnonymousUser()
        )
        self._cache: dict[tuple[str, str, str, str], bool] = {}

    def evaluate(self, declaration: GraphQLPermissionCapability, instance: Any) -> bool:
        """Evaluate a capability and cache deny-on-error results for the operation."""
        cache_key = self._cache_key(declaration, instance)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            result = bool(declaration.evaluator(instance, self.user))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "graphql capability evaluation failed",
                context={
                    "capability": declaration.name,
                    "manager": instance.__class__.__name__,
                    "error": type(exc).__name__,
                },
            )
            result = False
        self._cache[cache_key] = result
        return result

    def warm(
        self,
        declarations: Sequence[GraphQLPermissionCapability],
        instances: Sequence[Any],
    ) -> None:
        """Warm cached capability values for a page of instances when possible."""
        if not instances:
            return
        for declaration in declarations:
            if declaration.batch_evaluator is None:
                continue
            missing = [
                instance
                for instance in instances
                if self._cache_key(declaration, instance) not in self._cache
            ]
            if not missing:
                continue
            try:
                batch_result = declaration.batch_evaluator(missing, self.user)
                self._store_batch_result(declaration, missing, batch_result)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "graphql capability batch evaluation failed",
                    context={
                        "capability": declaration.name,
                        "manager": missing[0].__class__.__name__,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                self._store_batch_result(
                    declaration,
                    missing,
                    [False for _instance in missing],
                )

    def _store_batch_result(
        self,
        declaration: GraphQLPermissionCapability,
        instances: Sequence[Any],
        batch_result: Mapping[Any, bool] | Sequence[bool],
    ) -> None:
        if isinstance(batch_result, Mapping):
            normalized = {
                _instance_identity(instance): bool(value)
                for instance, value in batch_result.items()
            }
            for instance in instances:
                identity = _instance_identity(instance)
                self._cache[self._cache_key(declaration, instance)] = bool(
                    normalized.get(identity, False)
                )
            return

        for instance, value in zip(instances, batch_result, strict=True):
            self._cache[self._cache_key(declaration, instance)] = bool(value)

    def _cache_key(
        self,
        declaration: GraphQLPermissionCapability,
        instance: Any,
    ) -> tuple[str, str, str, str]:
        return (
            instance.__class__.__module__ + "." + instance.__class__.__qualname__,
            _instance_identity(instance),
            _user_identity(self.user),
            declaration.name,
        )


def get_graphql_capabilities(
    manager_class: type[Any],
) -> tuple[GraphQLPermissionCapability, ...]:
    """Return validated GraphQL capability declarations for a manager class."""
    permission_class = getattr(manager_class, "Permission", None)
    declarations = getattr(permission_class, "graphql_capabilities", ()) or ()
    return tuple(
        declaration
        for declaration in declarations
        if isinstance(declaration, GraphQLPermissionCapability)
    )


def get_capability_context(info: Any) -> CapabilityEvaluationContext:
    """Return the operation-scoped capability context for a GraphQL resolver."""
    request_context = getattr(info, "context", None)
    user = getattr(request_context, "user", None)
    operation_key = id(getattr(info, "operation", None))
    storage_name = "_general_manager_graphql_capability_contexts"
    contexts = getattr(request_context, storage_name, None)
    if contexts is None:
        contexts = {}
        try:
            setattr(request_context, storage_name, contexts)
        except Exception:  # noqa: BLE001
            return CapabilityEvaluationContext(user=user)
    if operation_key not in contexts:
        contexts[operation_key] = CapabilityEvaluationContext(user=user)
    return contexts[operation_key]


def clear_capability_context(info: Any) -> None:
    """Discard cached capability values for the current GraphQL operation."""
    request_context = getattr(info, "context", None)
    operation_key = id(getattr(info, "operation", None))
    storage_name = "_general_manager_graphql_capability_contexts"
    contexts = getattr(request_context, storage_name, None)
    if isinstance(contexts, dict):
        contexts.pop(operation_key, None)


def _resolve_payload(
    payload: CapabilityPayload | None,
    instance: Any,
    user: Any,
) -> dict[str, Any]:
    if payload is None:
        return {}
    if callable(payload):
        return dict(payload(instance, user))
    return dict(payload)


def _default_capability_name(action: str, target: type[Any]) -> str:
    return _lower_camel(f"{action}_{target.__name__}")


def _object_capability_description(name: str) -> str:
    return (
        f"Whether the current user has the {name} capability for this object. "
        "This is an advisory UI hint; backend permissions still enforce the action."
    )


def _permission_capability_description(
    action: CapabilityAction,
    target: type[Any],
) -> str:
    manager_name = getattr(target, "__name__", "manager")
    return (
        f"Whether the current user would pass the {manager_name} {action} "
        "permission check for this object context. This is an advisory UI hint; "
        "the mutation still enforces backend permissions."
    )


def _mutation_capability_description(mutation: Any) -> str:
    mutation_name = getattr(mutation, "__name__", "mutation")
    return (
        f"Whether the current user would pass the {mutation_name} mutation "
        "permission check for this object context. This is an advisory UI hint; "
        "the mutation still enforces backend permissions."
    )


def _lower_camel(value: str) -> str:
    parts = value.replace("-", "_").split("_")
    if not parts:
        return value
    first, *rest = parts
    return (
        first[:1].lower()
        + first[1:]
        + "".join(part[:1].upper() + part[1:] for part in rest)
    )


def _instance_identity(instance: Any) -> str:
    identification = getattr(instance, "identification", None)
    if isinstance(identification, Mapping):
        return repr(tuple(sorted(identification.items())))
    return repr(getattr(instance, "pk", getattr(instance, "id", id(instance))))


def _user_identity(user: Any) -> str:
    return repr(getattr(user, "pk", getattr(user, "id", None)))
