"""
Standalone mutation-generation functions extracted from ``api/graphql.py``.

Each public function in this module corresponds to a classmethod that was
previously on the ``GraphQL`` class.  The ``GraphQL`` class still exposes them
as classmethods (thin wrappers) for backward compatibility.

No import from ``general_manager.api.graphql`` is present here, so this
module can be imported by ``graphql.py`` without creating a circular dependency.
"""

from __future__ import annotations

from typing import Any, cast, TYPE_CHECKING

import graphene  # type: ignore[import]

from django.db.models import NOT_PROVIDED

from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.type_checks import safe_issubclass
from general_manager.api.graphql_errors import (
    HANDLED_MANAGER_ERRORS,
    MissingManagerIdentifierError,
    handle_graph_ql_error,
    map_field_to_graphene_base_type,
)

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo


def _normalize_mutation_kwargs_for_manager(
    general_manager_class: type[GeneralManager],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Normalize GraphQL relation aliases to the ORM mutation contract."""
    interface_cls = getattr(general_manager_class, "Interface", None)
    if interface_cls is None:
        return dict(kwargs)

    attribute_types = interface_cls.get_attribute_types()
    normalized = dict(kwargs)

    for key in list(kwargs.keys()):
        if key.endswith("_list") and not key.endswith("_id_list"):
            base_key = key.removesuffix("_list")
            if base_key in attribute_types:
                normalized.setdefault(f"{base_key}_id_list", normalized[key])
                normalized.pop(key, None)
                continue

        if key.startswith("_") and not key.endswith("_id"):
            type_info = attribute_types.get(key)
            relation_type = type_info["type"] if type_info is not None else None
            if safe_issubclass(relation_type, GeneralManager):
                normalized.setdefault(f"{key}_id", normalized[key])
                normalized.pop(key, None)

    return normalized


# ---------------------------------------------------------------------------
# Write-field helpers
# ---------------------------------------------------------------------------


def create_write_fields(interface_cls: InterfaceBase) -> dict[str, Any]:
    """
    Create Graphene input fields for writable attributes defined by an Interface.

    Skips system fields (``changed_by``, ``created_at``, ``updated_at``) and
    attributes marked as derived.  For attributes whose type is a
    ``GeneralManager``, produces an ID field or a list of ID fields for names
    ending with ``"_list"``.  Always includes an optional ``history_comment``
    string field.

    Parameters:
        interface_cls: Interface providing attribute metadata used to build
            the input fields.

    Returns:
        Mapping from attribute name to a Graphene input field instance.
    """
    fields: dict[str, Any] = {}
    for name, info in interface_cls.get_attribute_types().items():
        if name in ["changed_by", "created_at", "updated_at"]:
            continue
        if info["is_derived"]:
            continue

        typ = info["type"]
        req = info["is_required"]
        default = info["default"]

        fld: Any
        if safe_issubclass(typ, GeneralManager):
            if name.endswith("_list"):
                fld = graphene.List(graphene.ID, required=req, default_value=default)
            else:
                fld = graphene.ID(required=req, default_value=default)
        else:
            base_cls = map_field_to_graphene_base_type(
                typ,
                info.get("graphql_scalar"),
            )
            fld = base_cls(required=req, default_value=default)

        cast(Any, fld).editable = info["is_editable"]
        fields[name] = fld

    history_field = graphene.String()
    cast(Any, history_field).editable = True
    fields["history_comment"] = history_field

    return fields


# ---------------------------------------------------------------------------
# Mutation class generators
# ---------------------------------------------------------------------------


def generate_create_mutation_class(
    generalManagerClass: type[GeneralManager],
    default_return_values: dict[str, Any],
) -> type[graphene.Mutation] | None:
    """
    Generate a Graphene Mutation class that creates instances of the given manager.

    Parameters:
        generalManagerClass: The GeneralManager subclass to expose a create
            mutation for.
        default_return_values: Base mutation return fields to include on the
            generated class.

    Returns:
        A Mutation class named ``Create<ManagerName>``, or ``None`` if the
        manager class does not define an ``Interface``.
    """
    interface_cls: InterfaceBase | None = getattr(
        generalManagerClass, "Interface", None
    )
    if not interface_cls:
        return None

    def create_mutation(
        self: Any,
        info: GraphQLResolveInfo,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            kwargs = {
                field_name: value
                for field_name, value in kwargs.items()
                if value is not NOT_PROVIDED
            }
            kwargs = _normalize_mutation_kwargs_for_manager(generalManagerClass, kwargs)
            instance = generalManagerClass.create(
                **kwargs, creator_id=info.context.user.id
            )
        except HANDLED_MANAGER_ERRORS as error:
            raise handle_graph_ql_error(error) from error
        return {"success": True, generalManagerClass.__name__: instance}

    return type(
        f"Create{generalManagerClass.__name__}",
        (graphene.Mutation,),
        {
            **default_return_values,
            "__doc__": f"Mutation to create {generalManagerClass.__name__}",
            "Arguments": type(
                "Arguments",
                (),
                {
                    field_name: field
                    for field_name, field in create_write_fields(interface_cls).items()
                    if field_name not in generalManagerClass.Interface.input_fields
                },
            ),
            "mutate": create_mutation,
        },
    )


def generate_update_mutation_class(
    generalManagerClass: type[GeneralManager],
    default_return_values: dict[str, Any],
) -> type[graphene.Mutation] | None:
    """
    Generate a Graphene Mutation class that updates instances of the given manager.

    Parameters:
        generalManagerClass: The GeneralManager subclass to expose an update
            mutation for.
        default_return_values: Base mutation return fields to include on the
            generated class.

    Returns:
        A Mutation class named ``Update<ManagerName>``, or ``None`` if the
        manager class does not define an ``Interface``.
    """
    interface_cls: InterfaceBase | None = getattr(
        generalManagerClass, "Interface", None
    )
    if not interface_cls:
        return None

    def update_mutation(
        self: Any,
        info: GraphQLResolveInfo,
        **kwargs: Any,
    ) -> dict[str, Any]:
        manager_id = kwargs.pop("id", None)
        if manager_id is None:
            raise handle_graph_ql_error(MissingManagerIdentifierError())
        try:
            kwargs = {
                field_name: value
                for field_name, value in kwargs.items()
                if value is not NOT_PROVIDED
            }
            kwargs = _normalize_mutation_kwargs_for_manager(generalManagerClass, kwargs)
            instance = generalManagerClass(id=manager_id).update(
                creator_id=info.context.user.id, **kwargs
            )
        except HANDLED_MANAGER_ERRORS as error:
            raise handle_graph_ql_error(error) from error
        return {"success": True, generalManagerClass.__name__: instance}

    return type(
        f"Update{generalManagerClass.__name__}",
        (graphene.Mutation,),
        {
            **default_return_values,
            "__doc__": f"Mutation to update {generalManagerClass.__name__}",
            "Arguments": type(
                "Arguments",
                (),
                {
                    "id": graphene.ID(required=True),
                    **{
                        field_name: field
                        for field_name, field in create_write_fields(
                            interface_cls
                        ).items()
                        if field.editable
                    },
                },
            ),
            "mutate": update_mutation,
        },
    )


def generate_delete_mutation_class(
    generalManagerClass: type[GeneralManager],
    default_return_values: dict[str, Any],
) -> type[graphene.Mutation] | None:
    """
    Generate a Graphene Mutation class that deletes instances of the given manager.

    Parameters:
        generalManagerClass: The GeneralManager subclass to expose a delete
            mutation for.
        default_return_values: Base mutation return fields to include on the
            generated class.

    Returns:
        A Mutation class named ``Delete<ManagerName>``, or ``None`` if the
        manager class does not define an ``Interface``.
    """
    interface_cls: InterfaceBase | None = getattr(
        generalManagerClass, "Interface", None
    )
    if not interface_cls:
        return None

    def delete_mutation(
        self: Any,
        info: GraphQLResolveInfo,
        **kwargs: Any,
    ) -> dict[str, Any]:
        manager_id = kwargs.pop("id", None)
        if manager_id is None:
            raise handle_graph_ql_error(MissingManagerIdentifierError())
        try:
            instance = generalManagerClass(id=manager_id).delete(
                creator_id=info.context.user.id
            )
        except HANDLED_MANAGER_ERRORS as error:
            raise handle_graph_ql_error(error) from error
        return {"success": True, generalManagerClass.__name__: instance}

    return type(
        f"Delete{generalManagerClass.__name__}",
        (graphene.Mutation,),
        {
            **default_return_values,
            "__doc__": f"Mutation to delete {generalManagerClass.__name__}",
            "Arguments": type(
                "Arguments",
                (),
                {
                    # Always include id so the resolver can locate the instance.
                    "id": graphene.ID(required=True),
                    **{
                        field_name: field
                        for field_name, field in create_write_fields(
                            interface_cls
                        ).items()
                        if field_name in generalManagerClass.Interface.input_fields
                    },
                },
            ),
            "mutate": delete_mutation,
        },
    )
