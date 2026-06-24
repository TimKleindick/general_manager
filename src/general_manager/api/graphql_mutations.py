"""
Standalone mutation-generation functions extracted from ``api/graphql.py``.

Each public function in this module corresponds to a classmethod that was
previously on the ``GraphQL`` class.  The ``GraphQL`` class still exposes them
as classmethods (thin wrappers) for backward compatibility.

No import from ``general_manager.api.graphql`` is present here, so this
module can be imported by ``graphql.py`` without creating a circular dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import graphene

from django.db.models import NOT_PROVIDED

from general_manager.interface.base_interface import AttributeTypedDict, InterfaceBase
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

type MutationPayload = dict[str, object]
type MutationReturnDefaults = dict[str, object]


class _UnsetHistoryComment:
    """Sentinel for absent GraphQL ``history_comment`` input."""


_HISTORY_COMMENT_UNSET = _UnsetHistoryComment()


class _EditableGrapheneField(Protocol):
    """Graphene field instance carrying GeneralManager's dynamic editability flag."""

    editable: bool


type GrapheneFieldMap = dict[str, _EditableGrapheneField]


class _ManagerCreateMethod(Protocol):
    """Callable shape used for generated GraphQL create mutations."""

    def __call__(self, **kwargs: object) -> GeneralManager: ...


class _ManagerUpdateMethod(Protocol):
    """Callable shape used for generated GraphQL update mutations."""

    def __call__(self, **kwargs: object) -> GeneralManager: ...


class _ManagerDeleteMethod(Protocol):
    """Callable shape used for generated GraphQL delete mutations."""

    def __call__(self, **kwargs: object) -> None: ...


def _pop_history_comment(
    kwargs: MutationPayload,
) -> str | None | _UnsetHistoryComment:
    """Remove and return the GraphQL ``history_comment`` value from ``kwargs``.

    Omitted values return the internal unset sentinel. Explicit GraphQL ``null``
    returns ``None`` so callers can forward ``history_comment=None`` deliberately.
    """
    value = kwargs.pop("history_comment", _HISTORY_COMMENT_UNSET)
    if isinstance(value, _UnsetHistoryComment):
        return value
    if value is None or isinstance(value, str):
        return value
    return cast(str, value)


def _normalize_mutation_kwargs_for_manager(
    general_manager_class: type[GeneralManager],
    kwargs: MutationPayload,
) -> MutationPayload:
    """Normalize GraphQL relation aliases to the ORM mutation contract.

    GraphQL-facing relation inputs may arrive as ``field``/``field_list`` while
    ORM mutation capabilities expect ``field_id``/``field_id_list`` for manager
    relations. Unknown keys and already-normalized keys are preserved.
    """
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


def _is_direct_relation_raw_id_alias(
    name: str,
    attribute_types: dict[str, AttributeTypedDict],
) -> bool:
    """Return true when ``name`` is a raw relation id alias with a canonical field."""
    if name.endswith("_id_list"):
        relation_name = f"{name.removesuffix('_id_list')}_list"
        relation_info = attribute_types.get(relation_name)
        if relation_info is None:
            return False
        return safe_issubclass(relation_info["type"], GeneralManager)

    if not name.endswith("_id"):
        return False

    relation_name = name.removesuffix("_id")
    relation_info = attribute_types.get(relation_name)
    if relation_info is None:
        return False

    return relation_info.get("relation_kind") == "direct" and not relation_info.get(
        "is_derived", False
    )


def create_write_fields(
    interface_cls: type[InterfaceBase],
    *,
    require_fields: bool = True,
) -> GrapheneFieldMap:
    """
    Create Graphene input fields from interface attribute metadata.

    ``interface_cls`` must expose ``get_attribute_types()`` with
    :class:`AttributeTypedDict` metadata. This helper skips system fields
    (``changed_by``, ``created_at``, ``updated_at``), attributes marked as
    derived, raw ``*_id`` aliases for direct relations already exposed by their
    canonical relation field, and raw ``*_id_list`` aliases when the canonical
    ``*_list`` relation is present. For a direct relation, the canonical field is
    the non-``_id`` metadata entry whose ``relation_kind`` is ``"direct"`` and
    whose ``is_derived`` flag is false. For a list relation, the canonical field
    is the ``*_list`` metadata entry whose type is a ``GeneralManager`` subclass.

    Non-editable attributes are still returned with ``editable=False``. Create
    and update builders filter on that flag; delete keeps only constructor input
    fields plus its explicit metadata arguments. ``_EditableGrapheneField`` is an
    internal structural contract used by this module to annotate the dynamic flag
    attached to otherwise untyped Graphene field instances.

    The returned mapping keys are Python/interface metadata names such as
    ``"owner"`` and ``"member_list"``; Graphene applies any schema-level
    camel-casing later. For attributes whose type is a ``GeneralManager``, this
    helper produces an ID field or a list of ID fields for names ending with
    ``"_list"``. Later mutation resolvers normalize those canonical inputs to
    ``"owner_id"`` and ``"member_id_list"`` before calling the ORM mutation
    layer. Always includes an optional ``history_comment`` string field.

    Parameters:
        interface_cls: Interface providing attribute metadata used to build
            the input fields.
        require_fields: Whether generated fields should mirror interface
            requiredness. Create/delete helper calls use the default ``True``;
            update mutations set this to ``False`` to support partial updates.

    Returns:
        Mapping from attribute name to a Graphene input field instance. Returned
        fields carry a dynamic maintainer-facing ``editable`` flag used by the
        generated mutation class builders.
    """
    fields: GrapheneFieldMap = {}
    attribute_types = interface_cls.get_attribute_types()
    for name, info in attribute_types.items():
        if name in ["changed_by", "created_at", "updated_at"]:
            continue
        if info["is_derived"]:
            continue
        if _is_direct_relation_raw_id_alias(name, attribute_types):
            continue

        typ = info["type"]
        req = info["is_required"] if require_fields else False
        default = info["default"]

        fld: object
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

        editable_field = cast(_EditableGrapheneField, fld)
        editable_field.editable = info["is_editable"]
        fields[name] = editable_field

    history_field = graphene.String()
    editable_history_field = cast(_EditableGrapheneField, history_field)
    editable_history_field.editable = True
    fields["history_comment"] = editable_history_field

    return fields


# ---------------------------------------------------------------------------
# Mutation class generators
# ---------------------------------------------------------------------------


def generate_create_mutation_class(
    generalManagerClass: type[GeneralManager],
    default_return_values: MutationReturnDefaults,
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
    interface_cls: type[InterfaceBase] | None = getattr(
        generalManagerClass, "Interface", None
    )
    if not interface_cls:
        return None

    def create_mutation(
        self: object,
        info: GraphQLResolveInfo,
        **kwargs: object,
    ) -> MutationPayload:
        try:
            kwargs = {
                field_name: value
                for field_name, value in kwargs.items()
                if value is not NOT_PROVIDED
            }
            kwargs = _normalize_mutation_kwargs_for_manager(generalManagerClass, kwargs)
            history_comment = _pop_history_comment(kwargs)
            create = cast(_ManagerCreateMethod, generalManagerClass.create)
            create_kwargs = {"creator_id": info.context.user.id, **kwargs}
            if isinstance(history_comment, _UnsetHistoryComment):
                instance = create(**create_kwargs)
            else:
                create_kwargs["history_comment"] = history_comment
                instance = create(**create_kwargs)
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
                    and field.editable
                },
            ),
            "mutate": create_mutation,
        },
    )


def generate_update_mutation_class(
    generalManagerClass: type[GeneralManager],
    default_return_values: MutationReturnDefaults,
) -> type[graphene.Mutation] | None:
    """
    Generate a Graphene Mutation class that updates instances of the given manager.

    The generated mutation is named ``Update<ManagerName>``. It always requires
    an ``id`` argument, marks generated write fields optional for partial
    updates, filters Graphene ``NOT_PROVIDED`` sentinels, and forwards an
    explicit ``history_comment`` value separately from the field payload.

    Parameters:
        generalManagerClass: The GeneralManager subclass to expose an update
            mutation for.
        default_return_values: Base mutation return fields to include on the
            generated class.

    Returns:
        A Mutation class named ``Update<ManagerName>``, or ``None`` if the
        manager class does not define an ``Interface``.
    """
    interface_cls: type[InterfaceBase] | None = getattr(
        generalManagerClass, "Interface", None
    )
    if not interface_cls:
        return None

    def update_mutation(
        self: object,
        info: GraphQLResolveInfo,
        **kwargs: object,
    ) -> MutationPayload:
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
            history_comment = _pop_history_comment(kwargs)
            update = cast(
                _ManagerUpdateMethod, generalManagerClass(id=manager_id).update
            )
            update_kwargs = {"creator_id": info.context.user.id, **kwargs}
            if isinstance(history_comment, _UnsetHistoryComment):
                instance = update(**update_kwargs)
            else:
                update_kwargs["history_comment"] = history_comment
                instance = update(**update_kwargs)
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
                            interface_cls,
                            require_fields=False,
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
    default_return_values: MutationReturnDefaults,
) -> type[graphene.Mutation] | None:
    """
    Generate a Graphene Mutation class that deletes instances of the given manager.

    The generated mutation is named ``Delete<ManagerName>``. It always requires
    an ``id`` argument and exposes optional ``history_comment`` metadata for the
    manager delete call. Additional constructor input fields may appear for
    backward compatibility, but only ``id`` and ``history_comment`` are consumed
    by the generated resolver.

    Parameters:
        generalManagerClass: The GeneralManager subclass to expose a delete
            mutation for.
        default_return_values: Base mutation return fields to include on the
            generated class.

    Returns:
        A Mutation class named ``Delete<ManagerName>``, or ``None`` if the
        manager class does not define an ``Interface``.
    """
    interface_cls: type[InterfaceBase] | None = getattr(
        generalManagerClass, "Interface", None
    )
    if not interface_cls:
        return None

    def delete_mutation(
        self: object,
        info: GraphQLResolveInfo,
        **kwargs: object,
    ) -> MutationPayload:
        manager_id = kwargs.pop("id", None)
        if manager_id is None:
            raise handle_graph_ql_error(MissingManagerIdentifierError())
        history_comment = _pop_history_comment(kwargs)
        try:
            delete = cast(
                _ManagerDeleteMethod, generalManagerClass(id=manager_id).delete
            )
            if isinstance(history_comment, _UnsetHistoryComment):
                delete(creator_id=info.context.user.id)
            else:
                delete(
                    creator_id=info.context.user.id,
                    history_comment=history_comment,
                )
        except HANDLED_MANAGER_ERRORS as error:
            raise handle_graph_ql_error(error) from error
        return {"success": True, generalManagerClass.__name__: None}

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
                    "history_comment": graphene.String(),
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
