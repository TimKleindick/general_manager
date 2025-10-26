"""Decorator utilities for building GraphQL mutations from manager functions."""

import inspect
from typing import (
    Any,
    Callable,
    Optional,
    TypeVar,
    Union,
    List,
    Tuple,
    get_origin,
    get_args,
    Type,
    get_type_hints,
    cast,
    TypeAliasType,
)
import graphene  # type: ignore[import]
from graphql import GraphQLResolveInfo

from general_manager.api.graphql import GraphQL, HANDLED_MANAGER_ERRORS
from general_manager.manager.general_manager import GeneralManager

from general_manager.utils.format_string import snake_to_camel
from general_manager.permission.mutation_permission import MutationPermission
from types import UnionType


FuncT = TypeVar("FuncT", bound=Callable[..., object])


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


def graph_ql_mutation(
    _func: FuncT | type[MutationPermission] | None = None,
    permission: Optional[Type[MutationPermission]] = None,
) -> FuncT | Callable[[FuncT], FuncT]:
    """
    Decorator that converts a function into a GraphQL mutation class for use with Graphene, automatically generating argument and output fields from the function's signature and type annotations.

    The decorated function must provide type hints for all parameters (except `info`) and a return annotation. The decorator dynamically constructs a mutation class with appropriate Graphene fields, enforces permission checks if a `permission` class is provided, and registers the mutation for use in the GraphQL API.

    Parameters:
        permission (Optional[Type[MutationPermission]]): An optional permission class to enforce access control on the mutation.

    Returns:
        Callable: A decorator that registers the mutation and returns the original function.
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
            fn (Callable[..., Any]): Resolver implementing the mutation behaviour.

        Returns:
            Callable[..., Any]: Original function after registration.
        """
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        # Mutation name in PascalCase
        mutation_name = snake_to_camel(fn.__name__)

        # Build Arguments inner class dynamically
        arg_fields: dict[str, Any] = {}
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
            kwargs: dict[str, Any] = {}
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

            # Resolve list types to List scalar
            field: Any
            if get_origin(ann) is list or get_origin(ann) is List:
                inner = get_args(ann)[0]
                field = graphene.List(
                    GraphQL._map_field_to_graphene_base_type(inner),
                    **kwargs,
                )
            else:
                if inspect.isclass(ann) and issubclass(ann, GeneralManager):
                    field = graphene.ID(**kwargs)
                else:
                    field = GraphQL._map_field_to_graphene_base_type(ann)(**kwargs)

            arg_fields[name] = field

        Arguments = type("Arguments", (), arg_fields)

        # Build output fields: success + fn return types
        outputs: dict[str, Any] = {
            "success": graphene.Boolean(required=True),
        }
        return_ann: type | tuple[type] | None = hints.get("return")
        if return_ann is None:
            raise MissingMutationReturnAnnotationError(fn.__name__)

        # Unpack tuple return or single
        out_types = (
            list(get_args(return_ann))
            if get_origin(return_ann) in (tuple, Tuple)
            else [return_ann]
        )
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
                mutation_class: Instance of the mutation with output fields populated; `success` is `True` on successful execution and `False` if a handled manager error occurred (after being forwarded to GraphQL._handle_graph_ql_error).
            """
            if permission:
                permission.check(kwargs, info.context.user)
            try:
                result = fn(info, **kwargs)
                data: dict[str, Any] = {}
                if isinstance(result, tuple):
                    # unpack according to outputs ordering after success
                    for (field, _), val in zip(
                        outputs.items(),
                        [None, *list(result)],
                        strict=False,  # None for success field to be set later
                    ):
                        # skip success
                        if field == "success":
                            continue
                        data[field] = val
                else:
                    only = next(k for k in outputs if k != "success")
                    data[only] = result
                data["success"] = True
                return mutation_class(**data)
            except HANDLED_MANAGER_ERRORS as error:
                raise GraphQL._handle_graph_ql_error(error) from error

        # Assemble class dict
        class_dict: dict[str, Any] = {
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
        return decorator(cast(FuncT, _func))
    return decorator
