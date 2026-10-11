"""Executable output provenance for the bounded opt-in proof paths."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from inspect import getattr_static
from typing import Any
from weakref import WeakKeyDictionary, ref

from graphene.types import resolver as graphene_resolvers
from graphql import GraphQLObjectType, GraphQLSchema, get_named_type

from general_manager.api.graphql_resolvers import field_value_source
from general_manager.manager.general_manager import (
    _COMMON_MANAGER_GETATTR,
    _COMMON_MANAGER_EXECUTABLES,
)
from general_manager.manager.meta import GeneralManagerMeta, common_attribute_descriptor
from general_manager.utils.executable_contract import (
    executable_matches,
    executable_signature,
)

_ATTR = graphene_resolvers.attr_resolver
_DICT = graphene_resolvers.dict_resolver
_DEFAULT = graphene_resolvers.dict_or_attr_resolver
_DEFAULT_SIGNATURES = tuple(
    executable_signature(function) for function in (_ATTR, _DICT, _DEFAULT)
)
_MISSING_ATTRIBUTE = object()


@dataclass(frozen=True)
class _ValueBinding:
    field: Any
    resolver: Any
    attribute: str
    transform: str
    generated: Any
    generated_field: Any
    owner: Any
    type_key: object
    semantics: object


_VALUES: WeakKeyDictionary[GraphQLObjectType, dict[str, _ValueBinding]] = (
    WeakKeyDictionary()
)


def _source(resolver: Any, attribute: str) -> str | None:
    """Never accept a current foreign callback as a canonical getter."""
    if field_value_source(resolver) == (attribute, "identity"):
        return "identity"
    if field_value_source(resolver) == (attribute, "measurement"):
        return "measurement"
    if (
        type(resolver) is partial
        and any(resolver.func is function for function in (_ATTR, _DICT, _DEFAULT))
        and len(resolver.args) == 2
        and type(resolver.args[0]) is str
        and resolver.args[0] == attribute
        and resolver.args[1] is None
        and not resolver.keywords
        and graphene_resolvers.attr_resolver is _ATTR
        and graphene_resolvers.dict_resolver is _DICT
        and graphene_resolvers.dict_or_attr_resolver is _DEFAULT
        and all(
            executable_matches(function, signature)
            for function, signature in zip(
                (_ATTR, _DICT, _DEFAULT), _DEFAULT_SIGNATURES, strict=True
            )
        )
    ):
        return "identity"
    return None


def _type_key(value: Any) -> object:
    from general_manager.api.graphql_filter_contract import _type_key as key

    return key(value)


def _manager_source(owner: Any, attribute: str) -> bool:
    """Bind the GraphQL getter to the actual manager attribute source as well."""
    if (
        type(owner) is not GeneralManagerMeta
        or getattr_static(owner, "__getattribute__") is not object.__getattribute__
        or getattr_static(owner, "__getattr__") is not _COMMON_MANAGER_GETATTR
        or not executable_matches(
            _COMMON_MANAGER_GETATTR, _COMMON_MANAGER_EXECUTABLES[2][1]
        )
    ):
        return False
    descriptor = getattr_static(owner, attribute, _MISSING_ATTRIBUTE)
    if descriptor is _MISSING_ATTRIBUTE:
        # Generic interfaces may yield direct attribute/dict rows. A custom
        # manager property cannot acquire that direct-row authority.
        return attribute not in vars(owner).get("_attributes", {})
    return common_attribute_descriptor(owner, attribute, descriptor)


def attach_value_contracts(
    schema: GraphQLSchema, managers: Mapping[str, Any], types: Mapping[str, Any]
) -> None:
    """Attest only fields whose actual getter already proves the source name."""
    from general_manager.api.graphql_units import _public_name
    from general_manager.api.graphql_search import _COMMON_FILTER_SOURCE_SIGNATURE

    for manager, owner in managers.items():
        generated = types.get(manager)
        metadata = getattr(generated, "_meta", None)
        native = schema.get_type(getattr(metadata, "name", ""))
        if (
            metadata is None
            or not isinstance(native, GraphQLObjectType)
            or native in _VALUES
        ):
            continue
        attributes = owner.Interface.get_attribute_types()
        pins = {}
        for attribute in attributes:
            if attribute not in metadata.fields:
                continue
            public = _public_name(metadata, native, attribute)
            field = native.fields[public]
            transform = _source(field.resolve, attribute)
            if transform is None or not _manager_source(owner, attribute):
                continue
            pins[public] = _ValueBinding(
                ref(field),
                ref(field.resolve),
                attribute,
                transform,
                ref(generated),
                ref(metadata.fields[attribute]),
                owner,
                _type_key(field.type),
                _COMMON_FILTER_SOURCE_SIGNATURE(owner),
            )
        _VALUES[native] = pins


def public_value_contracts(native: Any) -> dict[int, dict[str, str]]:
    """Capture only immutable public meanings whose live getter still matches."""
    from general_manager.api.graphql_filter_contract import _leaf_valid
    from general_manager.api.graphql_search import _COMMON_FILTER_SOURCE_SIGNATURE

    pins = _VALUES.get(native) if isinstance(native, GraphQLObjectType) else None
    if pins is None:
        return {}
    result = {}
    for public, pin in pins.items():
        field = native.fields.get(public)
        generated = pin.generated()
        if (
            field is not pin.field()
            or generated is None
            or generated._meta.fields.get(pin.attribute) is not pin.generated_field()
            or field.resolve is not pin.resolver()
            or _type_key(field.type) != pin.type_key
            or _source(field.resolve, pin.attribute) != pin.transform
            or not _manager_source(pin.owner, pin.attribute)
            or _COMMON_FILTER_SOURCE_SIGNATURE(pin.owner) != pin.semantics
        ):
            continue
        leaf = get_named_type(field.type)
        if not isinstance(leaf, GraphQLObjectType) and not _leaf_valid(leaf):
            continue
        if pin.transform == "measurement" and not _measurement_shape_valid(leaf):
            continue
        result[id(field)] = {
            "source": "interface_attribute",
            "field": public,
            "transform": pin.transform,
        }
    return result


def _measurement_shape_valid(native: Any) -> bool:
    from general_manager.api.graphql_errors import MeasurementType
    from general_manager.api.graphql_filter_contract import _leaf_valid
    from graphql import GraphQLFloat, GraphQLString

    if (
        not isinstance(native, GraphQLObjectType)
        or getattr(native, "graphene_type", None) is not MeasurementType
    ):
        return False
    for name, scalar in (("value", GraphQLFloat), ("unit", GraphQLString)):
        field = native.fields.get(name)
        if (
            field is None
            or field.type is not scalar
            or not _leaf_valid(scalar)
            or _source(field.resolve, name) != "identity"
        ):
            return False
    return True


def list_envelope_valid(field: Any) -> bool:
    """The common result's items and count must survive GraphQL projection."""
    page = get_named_type(field.type)
    if not isinstance(page, GraphQLObjectType):
        return False
    items = page.fields.get("items")
    page_info = page.fields.get("pageInfo")
    if (
        items is None
        or page_info is None
        or _source(items.resolve, "items") != "identity"
        or _source(page_info.resolve, "pageInfo") != "identity"
    ):
        return False
    info = get_named_type(page_info.type)
    count = (
        info.fields.get("totalCount") if isinstance(info, GraphQLObjectType) else None
    )
    from general_manager.api.graphql_filter_contract import _leaf_valid
    from graphql import GraphQLInt

    return (
        count is not None
        and get_named_type(count.type) is GraphQLInt
        and _leaf_valid(GraphQLInt)
        and _source(count.resolve, "total_count") == "identity"
    )
