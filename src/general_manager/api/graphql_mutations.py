"""
Standalone mutation-generation functions extracted from ``api/graphql.py``.

Each public function in this module corresponds to a classmethod that was
previously on the ``GraphQL`` class.  The ``GraphQL`` class still exposes them
as classmethods (thin wrappers) for backward compatibility.

No import from ``general_manager.api.graphql`` is present here, so this
module can be imported by ``graphql.py`` without creating a circular dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Protocol, cast

import graphene
from graphql import GraphQLError, Undefined

from django.db.models import NOT_PROVIDED
from django.db import models
from django.core.exceptions import FieldDoesNotExist

from general_manager.interface.base_interface import AttributeTypedDict, InterfaceBase
from general_manager.api.graphql_relations import (
    get_graphql_manager_registry,
    resolve_general_manager_type,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.uploads.errors import UploadError, stable_upload_error
from general_manager.uploads.graphql_types import UploadToken
from general_manager.uploads.types import UploadOperation
from general_manager.utils.format_string import snake_to_camel
from general_manager.api.graphql_errors import (
    MissingManagerIdentifierError,
    handle_graph_ql_error,
    map_field_to_graphene_base_type,
)
from general_manager.api.graphql_identifiers import (
    build_identification_arguments,
    is_orm_identifier_input,
    pop_identification,
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


def _preflight_mutation_uploads(
    *,
    info: GraphQLResolveInfo,
    general_manager_class: type[GeneralManager],
    operation: UploadOperation,
    target_id: object | None,
    file_field_names: tuple[str, ...],
    kwargs: MutationPayload,
) -> None:
    """Convert file tokens before manager permission and observability hooks."""

    graphql_error: GraphQLError | None = None
    try:
        # Import lazily because GraphQL mutation classes are imported while the
        # Django app registry is still being populated.
        from general_manager.uploads.services import preflight_upload_tokens

        preflight_upload_tokens(
            user=getattr(info.context, "user", None),
            manager_class=general_manager_class,
            operation=operation,
            target_id=target_id,
            file_field_names=file_field_names,
            values=kwargs,
        )
    except UploadError as error:
        public_error = stable_upload_error(error)
        graphql_error = GraphQLError(
            public_error.default_message,
            extensions={"code": public_error.code},
        )

    if graphql_error is not None:
        # Raising outside the handler keeps service errors and their frames out
        # of the GraphQL exception chain; ``kwargs`` is already token-free.
        raise graphql_error


def _file_token_field_names(
    interface_cls: type[InterfaceBase],
    write_fields: GrapheneFieldMap,
) -> tuple[str, ...]:
    """Return generated UploadToken inputs backed by actual ORM file fields."""

    model = getattr(interface_cls, "_model", None)
    if not isinstance(model, type) or not issubclass(model, models.Model):
        return ()
    result: list[str] = []
    for name, field_value in write_fields.items():
        if not isinstance(field_value, UploadToken) or field_value.editable is not True:
            continue
        try:
            model_field = model._meta.get_field(name)
        except (FieldDoesNotExist, LookupError):
            continue
        if isinstance(model_field, models.FileField) and model_field.editable is True:
            result.append(name)
    return tuple(sorted(result))


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
    relations. Structured references pass through their manager constructor so
    all declared inputs are validated. Scalar reference values stay unchanged.
    Unknown keys and already-normalized keys are preserved.
    """
    interface_cls = getattr(general_manager_class, "Interface", None)
    if interface_cls is None:
        return dict(kwargs)

    attribute_types = interface_cls.get_attribute_types()
    normalized = dict(kwargs)
    manager_registry = get_graphql_manager_registry()

    def normalize_reference(
        manager_type: type[GeneralManager], value: object, relation_name: str
    ) -> object:
        if not isinstance(value, Mapping):
            return value
        reference = manager_type(**dict(value))
        model = getattr(interface_cls, "_model", None)
        if isinstance(model, type) and issubclass(model, models.Model):
            try:
                model_field = model._meta.get_field(relation_name)
            except FieldDoesNotExist:
                return reference
            target_field = getattr(model_field, "target_field", None)
            if isinstance(model_field, models.ManyToManyField):
                related_model = model_field.remote_field.model
                target_field = related_model._meta.get_field(
                    model_field.m2m_reverse_target_field_name()
                )
            if target_field is not None and not target_field.primary_key:
                return cast(object, getattr(reference, target_field.attname))
        return reference

    for key in list(kwargs.keys()):
        if key.endswith("_list") and not key.endswith("_id_list"):
            base_key = key.removesuffix("_list")
            type_info = attribute_types.get(key)
            relation_type = type_info["type"] if type_info is not None else None
            manager_type = resolve_general_manager_type(relation_type, manager_registry)
            if manager_type is not None:
                target_key = f"{base_key}_id_list"
                if target_key not in normalized:
                    value = normalized[key]
                    normalized[target_key] = (
                        [
                            normalize_reference(manager_type, item, base_key)
                            for item in value
                        ]
                        if isinstance(value, (list, tuple))
                        else normalize_reference(manager_type, value, base_key)
                    )
                normalized.pop(key, None)
                continue

        if not key.endswith("_id"):
            type_info = attribute_types.get(key)
            relation_type = type_info["type"] if type_info is not None else None
            manager_type = resolve_general_manager_type(relation_type, manager_registry)
            if manager_type is not None:
                target_key = f"{key}_id"
                if target_key not in normalized:
                    normalized[target_key] = normalize_reference(
                        manager_type, normalized[key], key
                    )
                normalized.pop(key, None)

    return normalized


def _graphql_mutation_field_name(
    general_manager_class: type[GeneralManager],
    field_name: str,
) -> str:
    interface_cls = getattr(general_manager_class, "Interface", None)
    if interface_cls is None:
        return snake_to_camel(field_name)

    attribute_types = interface_cls.get_attribute_types()
    manager_registry = get_graphql_manager_registry()

    if field_name.endswith("_id_list"):
        relation_name = f"{field_name.removesuffix('_id_list')}_list"
        relation_info = attribute_types.get(relation_name)
        if relation_info is not None and resolve_general_manager_type(
            relation_info["type"],
            manager_registry,
        ):
            return snake_to_camel(relation_name)

    if field_name.endswith("_id"):
        relation_name = field_name.removesuffix("_id")
        relation_info = attribute_types.get(relation_name)
        if relation_info is not None and resolve_general_manager_type(
            relation_info["type"],
            manager_registry,
        ):
            return snake_to_camel(relation_name)

    return snake_to_camel(field_name)


# ---------------------------------------------------------------------------
# Write-field helpers
# ---------------------------------------------------------------------------


def _is_direct_relation_raw_id_alias(
    name: str,
    attribute_types: dict[str, AttributeTypedDict],
    manager_registry: dict[str, type[GeneralManager]],
) -> bool:
    """Return true when ``name`` is a raw relation id alias with a canonical field."""
    if name.endswith("_id_list"):
        relation_name = f"{name.removesuffix('_id_list')}_list"
        relation_info = attribute_types.get(relation_name)
        if relation_info is None:
            return False
        return (
            resolve_general_manager_type(
                relation_info["type"],
                manager_registry,
            )
            is not None
        )

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
    and update builders filter on that flag; delete uses the shared constructor
    argument builder plus explicit metadata arguments. ``_EditableGrapheneField`` is an
    internal structural contract used by this module to annotate the dynamic flag
    attached to otherwise untyped Graphene field instances.

    The returned mapping keys are Python/interface metadata names such as
    ``"owner"`` and ``"member_list"``; Graphene applies any schema-level
    camel-casing later. For attributes whose type is a ``GeneralManager``, this
    helper produces an ID field or a list of ID fields for names ending with
    ``"_list"``. Later mutation resolvers normalize those canonical inputs to
    ``"owner_id"`` and ``"member_id_list"`` before calling the ORM mutation
    layer. Scalar identity metadata maps to ``ID`` as well. Sentinel and callable
    defaults are omitted from the schema; runtime defaults and validation stay
    with the interface/ORM. Always includes an optional ``history_comment``.

    Parameters:
        interface_cls: Interface providing attribute metadata used to build
            the input fields.
        require_fields: Whether generated fields should mirror interface
            requiredness. Create/delete helper calls use the default ``True``;
            update mutations set this to ``False`` to support partial updates
            and omit model defaults from their GraphQL arguments.

    Returns:
        Mapping from attribute name to a Graphene input field instance. Returned
        fields carry a dynamic maintainer-facing ``editable`` flag used by the
        generated mutation class builders.
    """
    fields: GrapheneFieldMap = {}
    attribute_types = interface_cls.get_attribute_types()
    manager_registry = get_graphql_manager_registry()
    for name, info in attribute_types.items():
        if name in ["changed_by", "created_at", "updated_at"]:
            continue
        if info["is_derived"]:
            continue
        if _is_direct_relation_raw_id_alias(
            name,
            attribute_types,
            manager_registry,
        ):
            continue

        typ = info["type"]
        req = info["is_required"] if require_fields else False
        # GraphQL injects argument defaults before the resolver sees the payload.
        # Partial updates must preserve omission, including defaults of None.
        default = info["default"] if require_fields else Undefined
        if default is NOT_PROVIDED or callable(default):
            default = Undefined
        manager_type = resolve_general_manager_type(typ, manager_registry)

        fld: object
        if info.get("orm_field_kind") in {"file", "image"}:
            fld = UploadToken(required=req, default_value=default)
        elif info.get("is_identifier"):
            fld = graphene.ID(required=req, default_value=default)
        elif manager_type is not None:
            from general_manager.api.mutation import (
                _build_manager_argument_field,
                _get_or_create_manager_input_type,
                _uses_single_id_input,
            )

            if name.endswith("_list"):
                reference_type = (
                    graphene.ID
                    if _uses_single_id_input(manager_type)
                    else _get_or_create_manager_input_type(manager_type)
                )
                fld = graphene.List(reference_type, required=req, default_value=default)
            else:
                fld = _build_manager_argument_field(
                    manager_type, required=req, default_value=default
                )
        elif isinstance(typ, type) and issubclass(typ, models.Model):
            if info.get("relation_kind") == "collection":
                fld = graphene.List(graphene.ID, required=req, default_value=default)
            elif info.get("relation_kind") == "direct":
                fld = graphene.ID(required=req, default_value=default)
            else:
                fld = map_field_to_graphene_base_type(typ)(
                    required=req, default_value=default
                )
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
    write_fields = create_write_fields(interface_cls)
    attribute_types: Mapping[str, Mapping[str, object]] = (
        interface_cls.get_attribute_types()
    )
    model = getattr(interface_cls, "_model", None)
    has_model = isinstance(model, type) and issubclass(model, models.Model)
    file_field_names = tuple(
        name
        for name in _file_token_field_names(interface_cls, write_fields)
        if name not in generalManagerClass.Interface.input_fields
    )

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
            _preflight_mutation_uploads(
                info=info,
                general_manager_class=generalManagerClass,
                operation=UploadOperation.CREATE,
                target_id=None,
                file_field_names=file_field_names,
                kwargs=kwargs,
            )
            kwargs = _normalize_mutation_kwargs_for_manager(generalManagerClass, kwargs)
            history_comment = _pop_history_comment(kwargs)
            create = cast(_ManagerCreateMethod, generalManagerClass.create)
            create_kwargs = {"creator_id": info.context.user.id, **kwargs}
            if isinstance(history_comment, _UnsetHistoryComment):
                instance = create(**create_kwargs)
            else:
                create_kwargs["history_comment"] = history_comment
                instance = create(**create_kwargs)
        except GraphQLError:
            raise
        except Exception as error:
            raise handle_graph_ql_error(
                error,
                field_name_mapper=lambda field_name: _graphql_mutation_field_name(
                    generalManagerClass, field_name
                ),
            ) from error
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
                    for field_name, field in write_fields.items()
                    if field.editable
                    and not attribute_types.get(field_name, {}).get(
                        "is_auto_primary_key"
                    )
                    and (
                        has_model
                        or field_name not in generalManagerClass.Interface.input_fields
                    )
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
    write_fields = create_write_fields(interface_cls, require_fields=False)
    attribute_types: Mapping[str, Mapping[str, object]] = (
        interface_cls.get_attribute_types()
    )
    identification_arguments = build_identification_arguments(
        generalManagerClass, for_mutation=True
    )
    requires_id = is_orm_identifier_input(generalManagerClass, "id")
    if not identification_arguments:
        identification_arguments = {"id": graphene.Argument(graphene.ID, required=True)}
        requires_id = True
    file_field_names = _file_token_field_names(interface_cls, write_fields)

    def update_mutation(
        self: object,
        info: GraphQLResolveInfo,
        **kwargs: object,
    ) -> MutationPayload:
        identification = pop_identification(identification_arguments, kwargs)
        manager_id = identification.get("id")
        if requires_id and manager_id is None:
            raise handle_graph_ql_error(MissingManagerIdentifierError())
        try:
            kwargs = {
                field_name: value
                for field_name, value in kwargs.items()
                if value is not NOT_PROVIDED
            }
            _preflight_mutation_uploads(
                info=info,
                general_manager_class=generalManagerClass,
                operation=UploadOperation.UPDATE,
                target_id=manager_id,
                file_field_names=file_field_names,
                kwargs=kwargs,
            )
            kwargs = _normalize_mutation_kwargs_for_manager(generalManagerClass, kwargs)
            history_comment = _pop_history_comment(kwargs)
            update = cast(
                _ManagerUpdateMethod, generalManagerClass(**identification).update
            )
            update_kwargs = {"creator_id": info.context.user.id, **kwargs}
            if isinstance(history_comment, _UnsetHistoryComment):
                instance = update(**update_kwargs)
            else:
                update_kwargs["history_comment"] = history_comment
                instance = update(**update_kwargs)
        except GraphQLError:
            raise
        except Exception as error:
            raise handle_graph_ql_error(
                error,
                field_name_mapper=lambda field_name: _graphql_mutation_field_name(
                    generalManagerClass, field_name
                ),
            ) from error
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
                    **{
                        field_name: field
                        for field_name, field in write_fields.items()
                        if field.editable
                        and not attribute_types.get(field_name, {}).get(
                            "is_primary_key"
                        )
                    },
                    **identification_arguments,
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
    an ``id: ID!`` argument for ORM managers and exposes optional
    ``history_comment`` metadata. Non-ORM managers retain their named constructor
    arguments, all of which are consumed to locate the target instance.

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

    identification_arguments = build_identification_arguments(
        generalManagerClass, for_mutation=True
    )
    requires_id = is_orm_identifier_input(generalManagerClass, "id")
    if not identification_arguments:
        identification_arguments = {"id": graphene.Argument(graphene.ID, required=True)}
        requires_id = True

    def delete_mutation(
        self: object,
        info: GraphQLResolveInfo,
        **kwargs: object,
    ) -> MutationPayload:
        identification = pop_identification(identification_arguments, kwargs)
        if requires_id and identification.get("id") is None:
            raise handle_graph_ql_error(MissingManagerIdentifierError())
        history_comment = _pop_history_comment(kwargs)
        try:
            delete = cast(
                _ManagerDeleteMethod, generalManagerClass(**identification).delete
            )
            if isinstance(history_comment, _UnsetHistoryComment):
                delete(creator_id=info.context.user.id)
            else:
                delete(
                    creator_id=info.context.user.id,
                    history_comment=history_comment,
                )
        except GraphQLError:
            raise
        except Exception as error:
            raise handle_graph_ql_error(
                error,
                field_name_mapper=lambda field_name: _graphql_mutation_field_name(
                    generalManagerClass, field_name
                ),
            ) from error
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
                    "history_comment": graphene.String(),
                    **identification_arguments,
                },
            ),
            "mutate": delete_mutation,
        },
    )
