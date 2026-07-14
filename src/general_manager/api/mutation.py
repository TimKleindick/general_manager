"""Decorator utilities for building GraphQL mutations from manager functions."""

import inspect
from collections.abc import Sequence
from typing import (
    Callable,
    TypeVar,
    Union,
    List,
    Tuple,
    get_origin,
    get_args,
    get_type_hints,
    TypeGuard,
    TypeAliasType,
    Protocol,
    cast,
)
import graphene
from graphql import GraphQLError, GraphQLResolveInfo

from general_manager.api.graphql import GraphQL
from general_manager.manager.general_manager import GeneralManager

from general_manager.utils.format_string import snake_to_camel
from general_manager.permission.mutation_permission import MutationPermission
from types import UnionType


FuncT = TypeVar("FuncT", bound=Callable[..., object])
type GrapheneFieldMap = dict[str, object]
type MutationAnnotations = dict[str, object]
type MutationKwargs = dict[str, object]
type MutationData = dict[str, object]
_manager_input_type_registry: dict[str, type[graphene.InputObjectType]] = {}


class ManagerMutationInputField(Protocol):
    """Input descriptor attributes needed to build custom mutation arguments."""

    type: type[object]
    required: bool


class MissingParameterTypeHintError(TypeError):
    """Raised when a mutation resolver parameter lacks a type hint."""

    def __init__(self, parameter_name: str, function_name: str) -> None:
        """
        Initialize the exception indicating a missing type hint for a function parameter.

        Parameters:
            parameter_name (str): Name of the parameter that lacks a type hint.
            function_name (str): Name of the function containing the parameter.
        """
        super().__init__(
            f"Missing type hint for parameter {parameter_name} in {function_name}."
        )


class MissingMutationReturnAnnotationError(TypeError):
    """Raised when a mutation resolver does not specify a return annotation."""

    def __init__(self, function_name: str) -> None:
        """
        Initialize the exception indicating a mutation is missing a return annotation.

        Parameters:
            function_name (str): Name of the mutation function that lacks a return annotation.
        """
        super().__init__(f"Mutation {function_name} missing return annotation.")


class InvalidMutationReturnTypeError(TypeError):
    """Raised when a mutation resolver declares a non-type return value."""

    def __init__(self, function_name: str, return_type: object) -> None:
        """
        Initialize an InvalidMutationReturnTypeError for a mutation whose return annotation is not a valid type.

        Parameters:
            function_name (str): Name of the mutation function that provided the invalid return annotation.
            return_type (object): The invalid return annotation value that triggered the error.
        """
        super().__init__(
            f"Mutation {function_name} return type {return_type} is not a type."
        )


class DuplicateMutationOutputNameError(ValueError):
    """Raised when a mutation resolver would expose duplicate output field names."""

    def __init__(self, function_name: str, field_name: str) -> None:
        """
        Initialize the exception indicating duplicate output field names.

        Parameters:
            function_name (str): Name of the mutation function that produced duplicates.
            field_name (str): The conflicting output field name.
        """
        super().__init__(
            f"Mutation {function_name} produces duplicate output field name '{field_name}'."
        )


class MutationTupleReturnLengthMismatchError(ValueError):
    """Raised when a tuple-annotated mutation returns the wrong number of values."""

    def __init__(
        self,
        function_name: str,
        expected_count: int,
        received_count: int,
    ) -> None:
        """
        Initialize the exception indicating a tuple return length mismatch.

        Parameters:
            function_name (str): Name of the mutation function that returned values.
            expected_count (int): Number of tuple values declared by the annotation.
            received_count (int): Number of tuple values returned by the resolver.
        """
        super().__init__(
            f"Mutation {function_name} expected {expected_count} "
            f"tuple return values but received {received_count}."
        )


def _is_general_manager_type(annotation: object) -> TypeGuard[type[GeneralManager]]:
    """Return True for class annotations that subclass GeneralManager."""
    return inspect.isclass(annotation) and issubclass(annotation, GeneralManager)


def _manager_input_fields(
    manager_class: type[GeneralManager],
) -> dict[str, ManagerMutationInputField]:
    """Return the manager interface input-field mapping when it is dict-shaped."""
    interface = getattr(manager_class, "Interface", None)
    input_fields = getattr(interface, "input_fields", {})
    if not isinstance(input_fields, dict):
        return {}
    return cast(dict[str, ManagerMutationInputField], dict(input_fields))


def _uses_single_id_input(manager_class: type[GeneralManager]) -> bool:
    """Return True when the manager expects one ID instead of nested input."""
    input_fields = _manager_input_fields(manager_class)
    return not input_fields or tuple(input_fields) == ("id",)


def _manager_input_type_identifier(manager_class: type[GeneralManager]) -> str:
    """Build a stable cache key from the manager's module and qualname."""
    return f"{manager_class.__module__}.{manager_class.__qualname__}"


def _manager_input_type_name(manager_class: type[GeneralManager]) -> str:
    """Build a GraphQL-safe input type name from a unique manager identifier."""
    identifier = _manager_input_type_identifier(manager_class)
    safe_identifier = "".join(
        char if char.isalnum() else "_" for char in identifier
    ).strip("_")
    if safe_identifier[:1].isdigit():
        safe_identifier = f"_{safe_identifier}"
    return f"{safe_identifier}MutationInput"


def _get_or_create_manager_input_type(
    manager_class: type[GeneralManager],
) -> type[graphene.InputObjectType]:
    """Build and cache a Graphene input object for multi-input manager arguments."""
    cache_key = _manager_input_type_identifier(manager_class)
    cached = _manager_input_type_registry.get(cache_key)
    if cached is not None:
        return cached

    type_name = _manager_input_type_name(manager_class)
    fields: GrapheneFieldMap = {}
    for input_name, input_field in _manager_input_fields(manager_class).items():
        field_type = input_field.type
        required = input_field.required
        if _is_general_manager_type(field_type):
            if _uses_single_id_input(field_type):
                fields[input_name] = graphene.ID(required=required)
            else:
                fields[input_name] = graphene.InputField(
                    _get_or_create_manager_input_type(field_type),
                    required=required,
                )
            continue

        fields[input_name] = GraphQL._map_field_to_graphene_base_type(field_type)(
            required=required,
        )

    input_type = type(type_name, (graphene.InputObjectType,), fields)
    _manager_input_type_registry[cache_key] = input_type
    return input_type


def _build_manager_argument_field(
    manager_class: type[GeneralManager],
    **kwargs: object,
) -> object:
    """Return ``ID`` for single-id managers or a nested input argument otherwise."""
    if _uses_single_id_input(manager_class):
        return graphene.ID(**kwargs)
    return graphene.Argument(_get_or_create_manager_input_type(manager_class), **kwargs)


def _normalize_manager_argument(
    manager_class: type[GeneralManager],
    value: object,
) -> GeneralManager | None:
    """Normalize ``None``, manager instances, mapping input, or ID input."""
    if value is None or isinstance(value, manager_class):
        return value
    if isinstance(value, dict):
        return manager_class(**value)
    return manager_class(value)


def _normalize_mutation_arguments(
    annotations: MutationAnnotations,
    kwargs: MutationKwargs,
) -> MutationKwargs:
    """Normalize manager-typed arguments and lists using resolver annotations."""
    normalized = dict(kwargs)
    for name, annotation in annotations.items():
        if name not in normalized:
            continue

        origin = get_origin(annotation)
        if origin is list or origin is List:
            inner = get_args(annotation)[0]
            if _is_general_manager_type(inner) and normalized[name] is not None:
                normalized[name] = [
                    _normalize_manager_argument(inner, item)
                    for item in _sequence_argument(normalized[name])
                ]
            continue

        if _is_general_manager_type(annotation):
            normalized[name] = _normalize_manager_argument(
                annotation,
                normalized[name],
            )
    return normalized


def _sequence_argument(value: object) -> Sequence[object]:
    """Return a GraphQL list argument as a sequence for manager normalization."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return value
    return (value,)


def graph_ql_mutation(
    _func: FuncT | type[MutationPermission] | None = None,
    permission: type[MutationPermission] | None = None,
) -> FuncT | Callable[[FuncT], FuncT]:
    """
    Register a synchronous function as a Graphene GraphQL mutation.

    Supported forms are ``@graph_ql_mutation``, ``@graph_ql_mutation()``,
    ``@graph_ql_mutation(SomePermission)``, and
    ``@graph_ql_mutation(permission=SomePermission)``. Passing both a
    positional permission class and ``permission=`` is unsupported; the
    positional class is treated as the permission and replaces the keyword
    value.

    Registration happens immediately when the decorator runs. A generated
    ``graphene.Mutation`` subclass is stored in ``GraphQL._mutations`` under
    the decorated function name converted by ``snake_to_camel``: the first
    underscore-delimited segment is kept unchanged and later segments are
    title-cased. The original function object is returned so it remains
    directly callable.

    The decorated function must provide type hints for all parameters except
    the parameter named ``info``; ``info`` is skipped by name and may appear in
    any position. Parameters become GraphQL arguments. ``Optional[T]`` marks an
    argument as not required, default values are passed through as Graphene
    defaults, ``list[T]`` becomes a GraphQL list, GeneralManager parameters with
    no declared inputs or only one ``id`` input become ``ID`` arguments, and
    multi-input GeneralManager parameters become generated nested input objects.
    Runtime manager normalization preserves existing manager instances, returns
    ``None`` unchanged, constructs mapping inputs with ``manager_class(**value)``,
    and constructs non-mapping inputs with ``manager_class(value)``. For
    ``list[Manager]`` and ``List[Manager]`` arguments, each list item follows
    that same manager normalization before permission checks and resolver calls.
    Other supported Python annotations are mapped through
    ``GraphQL._map_field_to_graphene_base_type``.

    The return annotation creates output fields. A single type creates one
    output field named after that type with a lower-case first letter; a tuple
    return annotation creates one output field per tuple member. Duplicate
    output names are rejected. The generated mutation also includes a required
    ``success`` field. Resolver execution normalizes GeneralManager arguments,
    calls ``permission.check(normalized_kwargs, info.context.user)`` when a
    permission class is configured, calls the original function, and returns the
    generated mutation instance. Explicit ``GraphQLError`` instances are
    preserved. Validation and deliberately public errors retain their intended
    client behavior, while unexpected ordinary exceptions are sanitized and
    assigned correlation IDs by ``GraphQL._handle_graph_ql_error``.

    Parameters:
        _func: Decorated function for bare usage, or a positional
            ``MutationPermission`` subclass.
        permission: Optional permission class used to enforce access control on
            the normalized mutation arguments.

    Returns:
        The original function for bare usage, or a decorator that registers the
        mutation and returns the original function.

    Raises:
        MissingParameterTypeHintError: A non-``info`` parameter has no type
            annotation.
        MissingMutationReturnAnnotationError: The decorated function has no
            return annotation.
        InvalidMutationReturnTypeError: The return annotation is not a concrete
            type or supported type alias.
        DuplicateMutationOutputNameError: Two return values would expose the
            same output field name.
    """
    if (
        _func is not None
        and inspect.isclass(_func)
        and issubclass(_func, MutationPermission)
    ):
        permission = _func
        _func = None

    def decorator(fn: FuncT) -> FuncT:
        """
        Transform ``fn`` into a Graphene-compatible mutation class.

        Parameters:
            fn: Resolver implementing the mutation behavior.

        Returns:
            Original function after registration.
        """
        vars(fn)["_general_manager_mutation_permission"] = permission
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        # Mutation name in PascalCase
        mutation_name = snake_to_camel(fn.__name__)

        # Build Arguments inner class dynamically
        arg_fields: GrapheneFieldMap = {}
        argument_annotations: MutationAnnotations = {}
        for name, param in sig.parameters.items():
            if name == "info":
                continue
            ann = hints.get(name)
            if ann is None:
                raise MissingParameterTypeHintError(name, fn.__name__)
            required = True
            default = param.default
            has_default = default is not inspect._empty

            # Prepare kwargs
            kwargs: MutationKwargs = {}
            if required:
                kwargs["required"] = True
            if has_default:
                kwargs["default_value"] = default

            # Handle Optional[...] → not required
            origin = get_origin(ann)
            if (origin is Union or origin is UnionType) and type(None) in get_args(ann):
                required = False
                # extract inner type
                ann = next(a for a in get_args(ann) if a is not type(None))
                kwargs["required"] = False

            argument_annotations[name] = ann

            # Resolve list types to List scalar
            field: object
            if get_origin(ann) is list or get_origin(ann) is List:
                inner = get_args(ann)[0]
                if _is_general_manager_type(inner):
                    if _uses_single_id_input(inner):
                        field = graphene.List(graphene.ID, **kwargs)
                    else:
                        field = graphene.List(
                            _get_or_create_manager_input_type(inner),
                            **kwargs,
                        )
                    arg_fields[name] = field
                    continue
                field = graphene.List(
                    GraphQL._map_field_to_graphene_base_type(inner),
                    **kwargs,
                )
            else:
                if _is_general_manager_type(ann):
                    field = _build_manager_argument_field(ann, **kwargs)
                else:
                    field = GraphQL._map_field_to_graphene_base_type(ann)(**kwargs)

            arg_fields[name] = field

        Arguments = type("Arguments", (), arg_fields)

        # Build output fields: success + fn return types
        outputs: GrapheneFieldMap = {
            "success": graphene.Boolean(required=True),
        }
        return_ann = hints.get("return")
        if return_ann is None:
            raise MissingMutationReturnAnnotationError(fn.__name__)

        # Unpack tuple return or single
        return_origin = get_origin(return_ann)
        is_tuple_return = return_origin in (tuple, Tuple)
        out_types = list(get_args(return_ann)) if is_tuple_return else [return_ann]
        for out in out_types:
            is_named_type = isinstance(out, TypeAliasType)
            is_type = isinstance(out, type)
            if not is_type and not is_named_type:
                raise InvalidMutationReturnTypeError(fn.__name__, out)
            name = out.__name__
            field_name = name[0].lower() + name[1:]
            if field_name in outputs:
                raise DuplicateMutationOutputNameError(fn.__name__, field_name)

            basis_type = out.__value__ if is_named_type else out

            outputs[field_name] = GraphQL._map_field_to_graphene_read(
                basis_type, field_name
            )

        # Define mutate method
        def _mutate(
            root: object,
            info: GraphQLResolveInfo,
            **kwargs: object,
        ) -> graphene.Mutation:
            """
            Execute the mutation resolver, enforce an optional permission check, and convert the resolver result into the mutation's output fields.

            Parameters:
                root: Graphene root object (unused).
                info: GraphQL execution info provided by Graphene.
                **kwargs: Mutation arguments provided by the client.

            Returns:
                An instance of the generated mutation with output fields populated
                and ``success=True``. An instance is returned only on success.

            Raises:
                GraphQLError: The resolver raises an explicit GraphQL error, or a
                    resolver failure is mapped to a GraphQL error.
            """
            try:
                normalized_kwargs = _normalize_mutation_arguments(
                    argument_annotations,
                    kwargs,
                )
                if permission:
                    permission.check(normalized_kwargs, info.context.user)
                result = fn(info, **normalized_kwargs)
                data: MutationData = {}
                if is_tuple_return:
                    result_values = result if isinstance(result, tuple) else (result,)
                    expected_count = len(out_types)
                    received_count = len(result_values)
                    if received_count != expected_count:
                        raise MutationTupleReturnLengthMismatchError(  # noqa: TRY301
                            fn.__name__,
                            expected_count,
                            received_count,
                        )
                    # unpack according to outputs ordering after success
                    for field, val in zip(
                        (field for field in outputs if field != "success"),
                        result_values,
                        strict=True,
                    ):
                        data[field] = val
                else:
                    only = next(k for k in outputs if k != "success")
                    data[only] = result
                data["success"] = True
                return mutation_class(**data)
            except GraphQLError:
                raise
            except Exception as error:
                raise GraphQL._handle_graph_ql_error(
                    error,
                    field_name_mapper=snake_to_camel,
                ) from error

        # Assemble class dict
        class_dict: GrapheneFieldMap = {
            "Arguments": Arguments,
            "__doc__": fn.__doc__,
            "mutate": staticmethod(_mutate),
        }
        class_dict.update(outputs)

        # Create Mutation class
        mutation_class = type(mutation_name, (graphene.Mutation,), class_dict)

        if mutation_class.__name__ not in GraphQL._mutations:
            GraphQL._mutations[mutation_class.__name__] = mutation_class

        return fn

    if _func is not None and inspect.isfunction(_func):
        return decorator(_func)
    return decorator
