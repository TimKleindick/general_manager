import inspect
from typing import get_type_hints, Optional, Union, List, Tuple, get_origin, get_args
import graphene

from general_manager.api.graphql import GraphQL
from general_manager.manager.generalManager import GeneralManager

from general_manager.utils.formatString import snake_to_camel
from typing import TypeAliasType


def graphQlMutation(needs_role: Optional[str] = None, auth_required: bool = False):
    """
    Decorator that transforms a function into a GraphQL mutation class and registers it for use in a Graphene-based API.

    The decorated function must have type hints for all parameters (except `info`) and a return annotation. The decorator dynamically generates a mutation class with arguments and output fields based on the function's signature and return type. It also enforces authentication if `auth_required` is set to True, returning an error if the user is not authenticated.

    Parameters:
        needs_role (Optional[str]): Reserved for future use to specify a required user role.
        auth_required (bool): If True, the mutation requires an authenticated user.

    Returns:
        Callable: A decorator that registers the mutation and returns the original function.
    """

    def decorator(fn):
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        # Mutation name in PascalCase
        mutation_name = snake_to_camel(fn.__name__)

        # Build Arguments inner class dynamically
        arg_fields = {}
        for name, param in sig.parameters.items():
            if name == "info":
                continue
            ann = hints.get(name)
            if ann is None:
                raise TypeError(
                    f"Missing type hint for parameter {name} in {fn.__name__}"
                )
            required = True
            default = param.default
            has_default = default is not inspect._empty

            # Prepare kwargs
            kwargs = {}
            if required:
                kwargs["required"] = True
            if has_default:
                kwargs["default_value"] = default

            # Handle Optional[...] → not required
            origin = get_origin(ann)
            if origin is Union and type(None) in get_args(ann):
                required = False
                # extract inner type
                ann = [a for a in get_args(ann) if a is not type(None)][0]
                kwargs["required"] = False

            # Resolve list types to List scalar
            if get_origin(ann) is list or get_origin(ann) is List:
                inner = get_args(ann)[0]
                field = graphene.List(
                    GraphQL._mapFieldToGrapheneBaseType(inner),
                    **kwargs,
                )
            else:
                if inspect.isclass(ann) and issubclass(ann, GeneralManager):
                    field = graphene.ID(**kwargs)
                else:
                    field = GraphQL._mapFieldToGrapheneBaseType(ann)(**kwargs)

            arg_fields[name] = field

        Arguments = type("Arguments", (), arg_fields)

        # Build output fields: success, errors, + fn return types
        outputs = {
            "success": graphene.Boolean(required=True),
            "errors": graphene.List(graphene.String),
        }
        return_ann: type | tuple[type] | None = hints.get("return")
        if return_ann is None:
            raise TypeError(f"Mutation {fn.__name__} missing return annotation")

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
                raise TypeError(
                    f"Mutation {fn.__name__} return type {out} is not a type"
                )
            name = out.__name__
            field_name = name[0].lower() + name[1:]

            basis_type = out.__value__ if is_named_type else out

            outputs[field_name] = GraphQL._mapFieldToGrapheneRead(
                basis_type, field_name
            )

        # Define mutate method
        def _mutate(root, info, **kwargs):
            # Auth check
            if auth_required and not getattr(info.context, "user", None):
                return mutation_class(
                    **{"success": False, "errors": ["Authentication required"]}
                )
            try:
                result = fn(info, **kwargs)
                data = {}
                if isinstance(result, tuple):
                    # unpack according to outputs ordering after success/errors
                    for (field, _), val in zip(
                        outputs.items(), [None, None] + list(result)
                    ):
                        # skip success/errors
                        if field in ("success", "errors"):
                            continue
                        data[field] = val
                else:
                    only = next(k for k in outputs if k not in ("success", "errors"))
                    data[only] = result
                data["success"] = True
                data["errors"] = []
                return mutation_class(**data)
            except Exception as e:
                return mutation_class(**{"success": False, "errors": [str(e)]})

        # Assemble class dict
        class_dict = {
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

    return decorator
