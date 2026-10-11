"""Publish common predicates only while their executable bindings are unchanged."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from types import MethodType, MappingProxyType
from typing import Any, cast
from weakref import WeakKeyDictionary, ref

import graphene
from graphene.types.inputobjecttype import InputObjectTypeContainer
from graphene.types.definitions import GrapheneEnumType
from graphene.utils.str_converters import to_camel_case
from graphql import (
    GraphQLSchema,
    GraphQLInputObjectType,
    GraphQLObjectType,
    GraphQLScalarType,
    GraphQLEnumType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLString,
    GraphQLInt,
    GraphQLFloat,
    GraphQLBoolean,
    GraphQLID,
    Undefined,
    get_named_type,
)

from general_manager.api.graphql_errors import BigIntScalar, MeasurementScalar
from general_manager.manager.input import Input, InputDomain, _COMMON_INPUT_EXECUTABLES
from general_manager.utils.executable_contract import (
    executable_matches,
    executable_signature,
)

FILTER_EXTENSION = "gm_filter_contract"
_OPERATIONS = ("serialize", "parse_value", "parse_literal")
_BUILTINS = {
    graphene.String: GraphQLString,
    graphene.Int: GraphQLInt,
    graphene.Float: GraphQLFloat,
    graphene.Boolean: GraphQLBoolean,
    graphene.ID: GraphQLID,
}
_SCALAR_OPERATIONS = {
    source: tuple(getattr(native, name) for name in _OPERATIONS)
    for source, native in _BUILTINS.items()
}
_SCALAR_OPERATIONS.update(
    {
        source: tuple(getattr(source, name) for name in _OPERATIONS)
        for source in (
            graphene.Date,
            graphene.DateTime,
            graphene.Time,
            BigIntScalar,
            MeasurementScalar,
        )
    }
)
_CONTAINER_INIT = InputObjectTypeContainer.__init__
_ENUM_OPERATIONS = (
    GrapheneEnumType.serialize,
    GraphQLEnumType.parse_value,
    GraphQLEnumType.parse_literal,
)
_PY_ENUM_CALL = type(Enum).__call__
_PY_ENUM_GETITEM = type(Enum).__getitem__
_PY_ENUM_NEW = Enum.__new__
_PY_ENUM_MISSING = cast(MethodType, Enum._missing_).__func__
_INPUT_CAST, _INPUT_NORMALIZE = Input.cast, Input.normalize
_SCALAR_SIGNATURES = {
    source: tuple(executable_signature(function) for function in operations)
    for source, operations in _SCALAR_OPERATIONS.items()
}
_ENUM_SIGNATURES = tuple(
    executable_signature(function) for function in _ENUM_OPERATIONS
)
_PY_ENUM_SIGNATURES = tuple(
    (function, executable_signature(function))
    for function in (_PY_ENUM_CALL, _PY_ENUM_GETITEM, _PY_ENUM_NEW, _PY_ENUM_MISSING)
)
_CONTAINER_SIGNATURE = executable_signature(_CONTAINER_INIT)


@dataclass(frozen=True)
class _GeneratedInput:
    generated: Any
    owner: Any
    container: Any
    fields: Mapping[str, tuple[Any, Any, object, str]]
    declarations: Mapping[str, Mapping[str, str]]
    semantics: object


@dataclass(frozen=True)
class _NativeField:
    field: Any
    type_key: object
    default_key: object
    out_name: str | None
    public: Mapping[str, str]
    output: Any
    output_field: Any
    output_type_key: object


@dataclass(frozen=True)
class _EnumBinding:
    generated: Any
    enum: Any
    members: tuple[tuple[str, Any, object], ...]
    member_names: tuple[str, ...]
    raw_index: object


_GENERATED: WeakKeyDictionary[Any, _GeneratedInput] = WeakKeyDictionary()
_NATIVE: WeakKeyDictionary[GraphQLInputObjectType, dict[str, _NativeField]] = (
    WeakKeyDictionary()
)
_ENUMS: WeakKeyDictionary[Any, _EnumBinding] = WeakKeyDictionary()
_ROOT_ARGUMENTS: dict[
    int, tuple[Any, dict[str, tuple[Any, object, object, str | None]]]
] = {}


def _callable_is(actual: Any, expected: Any) -> bool:
    if isinstance(actual, MethodType) and isinstance(expected, MethodType):
        return (
            actual.__self__ is expected.__self__
            and actual.__func__ is expected.__func__
        )
    return actual is expected


def _default_key(value: Any) -> object:
    """Compare only supported literal defaults; never traverse private objects."""
    if value is Undefined:
        return ("undefined",)
    if value is None or type(value) in (str, int, float, bool):
        return (type(value), value)
    if type(value) in (date, datetime):
        return (type(value), value.isoformat())
    if isinstance(value, Enum):
        return (type(value), id(value))
    if type(value) in (list, tuple):
        return (type(value), tuple(_default_key(item) for item in value))
    if type(value) is dict and all(type(key) is str for key in value):
        return (dict, tuple((key, _default_key(item)) for key, item in value.items()))
    # An opaque default cannot acquire executable authority through observation.
    return ("unsupported", id(value))


def _type_key(value: Any) -> object:
    if type(value) in (GraphQLList, GraphQLNonNull):
        return (type(value), id(value), _type_key(value.of_type))
    return (type(value), id(value))


def _register_enum(value: Any) -> None:
    if (
        isinstance(value, type)
        and issubclass(value, graphene.Enum)
        and value not in _ENUMS
    ):
        source = cast(Any, value)
        enum = source._meta.enum
        _ENUMS[value] = _EnumBinding(
            ref(value),
            enum,
            tuple(
                (name, member, _default_key(member.value))
                for name, member in enum.__members__.items()
            ),
            tuple(enum._member_names_),
            _enum_raw_index(enum),
        )


def _enum_raw_index(enum: Any) -> object:
    if type(enum._value2member_map_) is not dict:
        return None
    if any(
        value is not None and type(value) not in (str, int, float, bool)
        for value in enum._value2member_map_
    ):
        return None
    return tuple(
        (_default_key(value), id(member))
        for value, member in enum._value2member_map_.items()
    )


def register_filter_input(
    generated: Any,
    owner: Any,
    declarations: Mapping[str, Mapping[str, str]],
    semantics: object,
) -> None:
    """Called by the common builder, before native schema attachment/coercion."""
    fields = {}
    for key, field in generated._meta.fields.items():
        expected = field.type
        leaf = expected
        while type(leaf) in (graphene.List, graphene.NonNull):
            leaf = leaf.of_type
        _register_enum(leaf)
        fields[key] = (
            field,
            expected,
            _default_key(field.default_value),
            field.name or to_camel_case(key),
        )
    _GENERATED[generated] = _GeneratedInput(
        ref(generated),
        owner,
        ref(generated._meta.container),
        MappingProxyType(fields),
        MappingProxyType(
            {key: MappingProxyType(dict(value)) for key, value in declarations.items()}
        ),
        semantics,
    )


def _leaf_valid(native: Any, expected: Any | None = None) -> bool:
    source: Any
    if isinstance(native, GraphQLScalarType):
        source = next(
            (source for source, builtin in _BUILTINS.items() if native is builtin),
            getattr(native, "graphene_type", None),
        )
        operations = _SCALAR_OPERATIONS.get(source)
        return (
            operations is not None
            and (expected is None or source is expected)
            and all(
                _callable_is(getattr(native, name), function)
                and executable_matches(function, signature)
                for name, function, signature in zip(
                    _OPERATIONS, operations, _SCALAR_SIGNATURES[source], strict=True
                )
            )
        )
    if isinstance(native, GraphQLEnumType):
        source = getattr(native, "graphene_type", None)
        binding = _ENUMS.get(source) if isinstance(source, type) else None
        if (
            binding is None
            or binding.generated() is not source
            or (expected is not None and source is not expected)
        ):
            return False
        enum, members = binding.enum, binding.members
        if (
            source._meta.enum is not enum
            or type(enum) is not type(Enum)
            or type(enum).__call__ is not _PY_ENUM_CALL
            or type(enum).__getitem__ is not _PY_ENUM_GETITEM
            or enum.__new__ is not _PY_ENUM_NEW
            or getattr(enum._missing_, "__func__", None) is not _PY_ENUM_MISSING
            or getattr(enum._missing_, "__self__", None) is not enum
            or not all(
                executable_matches(function, signature)
                for function, signature in _PY_ENUM_SIGNATURES
            )
            or type(enum._member_map_) is not dict
            or type(enum._member_names_) is not list
            or tuple(enum._member_names_) != binding.member_names
            or tuple(enum.__members__) != tuple(name for name, _, _ in members)
            or binding.raw_index is None
            or _enum_raw_index(enum) != binding.raw_index
            or any(
                member.value is not None
                and type(member.value) not in (str, int, float, bool)
                for _, member, _ in members
            )
        ):
            return False
        if set(native.values) != {name for name, _, _ in members}:
            return False
        if any(
            native.values[name].value is not member
            or enum.__members__[name] is not member
            or _default_key(member.value) != value
            for name, member, value in members
        ):
            return False
        if not all(
            isinstance(getattr(native, name), MethodType)
            and getattr(native, name).__self__ is native
            and getattr(native, name).__func__ is function
            and executable_matches(function, signature)
            for name, function, signature in zip(
                _OPERATIONS, _ENUM_OPERATIONS, _ENUM_SIGNATURES, strict=True
            )
        ):
            return False
        lookup = native.__dict__.get("_value_lookup")
        expected_lookup: dict[Any, str] = {}
        for name, member, _ in members:
            expected_lookup.setdefault(member, name)
        return lookup is None or (
            type(lookup) is dict
            and len(lookup) == len(expected_lookup)
            and all(
                any(key is member for key in lookup) and lookup.get(member) == name
                for member, name in expected_lookup.items()
            )
        )
    return False


def _matches_type(native: Any, expected: Any) -> bool:
    if type(expected) in (graphene.List, graphene.NonNull):
        wrapper = GraphQLList if type(expected) is graphene.List else GraphQLNonNull
        return type(native) is wrapper and _matches_type(
            native.of_type, expected.of_type
        )
    if type(native) in (GraphQLList, GraphQLNonNull):
        return False
    if isinstance(native, GraphQLInputObjectType):
        return (
            getattr(native, "graphene_type", None) is expected
            and expected in _GENERATED
        )
    return _leaf_valid(native, expected)


def _input_container_valid(native: Any) -> bool:
    generated: Any = getattr(native, "graphene_type", None)
    binding = _GENERATED.get(generated) if isinstance(generated, type) else None
    if (
        binding is None
        or binding.generated() is not generated
        or not isinstance(native, GraphQLInputObjectType)
        or native not in _NATIVE
    ):
        return False
    container = binding.container()
    return (
        container is not None
        and native.out_type is container
        and generated._meta.container is container
        and type(container).__call__ is type.__call__
        and container.__init__ is _CONTAINER_INIT
        and executable_matches(_CONTAINER_INIT, _CONTAINER_SIGNATURE)
        and container.__new__ is dict.__new__
        and container.__setattr__ is object.__setattr__
        and container.__getattribute__ is dict.__getattribute__
        and all(
            getattr(container, name) is getattr(dict, name)
            for name in (
                "items",
                "keys",
                "values",
                "get",
                "__iter__",
                "__getitem__",
                "__setitem__",
            )
        )
    )


def _input_valid(native: Any, seen: set[int] | None = None) -> bool:
    from general_manager.api import graphql_search

    generated: Any = getattr(native, "graphene_type", None)
    binding = _GENERATED.get(generated) if isinstance(generated, type) else None
    fields = _NATIVE.get(native) if isinstance(native, GraphQLInputObjectType) else None
    if binding is None or binding.generated() is not generated or fields is None:
        return False
    if not graphql_search.common_filter_execution_valid():
        return False
    for identifier in graphql_search._filter_identity_inputs(binding.owner).values():
        if type(identifier) is not Input:
            return False
        input_spec: Input[Any] = identifier
        if (
            input_spec.type not in (str, int)
            or getattr(input_spec.cast, "__func__", None) is not _INPUT_CAST
            or getattr(input_spec.normalize, "__func__", None) is not _INPUT_NORMALIZE
            or getattr(input_spec.cast, "__self__", None) is not input_spec
            or getattr(input_spec.normalize, "__self__", None) is not input_spec
            or input_spec.normalizer is not None
            or isinstance(input_spec.possible_values, InputDomain)
            or not all(
                executable_matches(function, signature)
                for function, signature in _COMMON_INPUT_EXECUTABLES
            )
        ):
            return False
    if (
        not _input_container_valid(native)
        or binding.semantics
        != graphql_search._COMMON_FILTER_SOURCE_SIGNATURE(binding.owner)
        or set(native.fields) != set(fields)
        or set(generated._meta.fields) != set(binding.fields)
    ):
        return False
    seen = set() if seen is None else seen
    if id(native) in seen:
        return False
    seen = {*seen, id(native)}
    for key, (original, expected, default, name) in binding.fields.items():
        current = native.fields.get(name)
        pin = fields.get(name)
        if (
            generated._meta.fields[key] is not original
            or original.type is not expected
            or _default_key(original.default_value) != default
            or pin is None
            or current is not pin.field
            or _type_key(current.type) != pin.type_key
            or current.out_name != pin.out_name
            or _default_key(current.default_value) != pin.default_key
            or not _matches_type(current.type, expected)
        ):
            return False
        named = get_named_type(current.type)
        if isinstance(named, GraphQLInputObjectType) and not _input_valid(named, seen):
            return False
    return True


def public_filter_contracts(
    native: GraphQLInputObjectType,
) -> dict[int, dict[str, str]]:
    """Revalidate the whole generated input codec before publishing any predicate."""
    if not _input_valid(native):
        return {}
    from general_manager.api import graphql_search
    from general_manager.api.graphql_value_contract import public_value_contracts

    binding = _GENERATED[getattr(native, "graphene_type", None)]
    owner = binding.owner
    source_fields = {
        binding.fields[key][3]: declaration["field"]
        for key, declaration in binding.declarations.items()
    }
    identity_inputs = graphql_search._filter_identity_inputs(owner)
    attributes = owner.Interface.get_attribute_types()
    outputs = {
        id(pin.output): pin.output for pin in _NATIVE[native].values() if pin.public
    }
    output_contracts = {
        identity: public_value_contracts(output) for identity, output in outputs.items()
    }
    result = {}
    for name, pin in _NATIVE[native].items():
        if not pin.public:
            continue
        field = pin.field
        extensions = getattr(field, "extensions", None)
        if (
            not isinstance(extensions, Mapping)
            or extensions.get(FILTER_EXTENSION) != pin.public
        ):
            continue
        output_field = pin.output.fields.get(pin.public["field"])
        if (
            output_field is not pin.output_field
            or _type_key(output_field.type) != pin.output_type_key
            or output_contracts[id(pin.output)].get(id(output_field))
            != {
                "source": "interface_attribute",
                "field": pin.public["field"],
                "transform": "identity",
            }
        ):
            continue
        if pin.public["operator"] != "relation":
            metadata = attributes.get(source_fields[name], {})
            if metadata.get("is_identifier"):
                converter = graphql_search._identity_filter_converter(
                    owner, source_fields[name]
                )
                input_spec = getattr(converter, "__self__", None)
                if not (
                    type(input_spec) is Input
                    and getattr(converter, "__func__", None) is _INPUT_CAST
                    and any(
                        input_spec is declared_input
                        for declared_input in identity_inputs.values()
                    )
                    and metadata.get("type") in (str, int)
                    and input_spec.type is metadata.get("type")
                ):
                    # ID wire text alone cannot prove native equality: execution
                    # must use a declared input with the actual attribute's type.
                    # Keep the GraphQL field available for ordinary queries.
                    continue
            source, target = (
                get_named_type(field.type),
                get_named_type(output_field.type),
            )
            if not _leaf_valid(target) or isinstance(output_field.type, GraphQLList):
                continue
            if source is not target and not (
                pin.public["field"] == "id"
                and source is GraphQLID
                and target in (GraphQLString, GraphQLInt)
            ):
                continue
        result[id(field)] = dict(pin.public)
    return result


def _root_arguments_valid(field: Any) -> bool:
    # Small synthetic resolver-only callers cannot publish a schema field.
    if not hasattr(field, "args"):
        return True
    stored = _ROOT_ARGUMENTS.get(id(field))
    if stored is None or stored[0]() is not field or set(field.args) != set(stored[1]):
        return False
    for name, (original, type_key, default, out_name) in stored[1].items():
        argument = field.args[name]
        if (
            argument is not original
            or _type_key(argument.type) != type_key
            or _default_key(argument.default_value) != default
            or argument.out_name != out_name
        ):
            return False
        role = argument.out_name or name
        if role in ("filter", "exclude") and not _input_container_valid(
            get_named_type(argument.type)
        ):
            return False
        if role in ("page", "page_size") and not _leaf_valid(
            get_named_type(argument.type), graphene.Int
        ):
            return False
    return True


def attach_filter_contracts(
    schema: GraphQLSchema, managers: Mapping[str, Any], types: Mapping[str, Any]
) -> None:
    """Pin genuine common builder definitions without blessing custom coercers."""
    from general_manager.api.graphql_units import _public_name
    from general_manager.api.graphql_resolvers import root_population_owner

    for native in schema.type_map.values():
        if not isinstance(native, GraphQLInputObjectType) or native in _NATIVE:
            continue
        generated: Any = getattr(native, "graphene_type", None)
        binding = _GENERATED.get(generated) if isinstance(generated, type) else None
        if binding is None or binding.generated() is not generated:
            continue
        manager = next(
            (name for name, item in managers.items() if item is binding.owner), None
        )
        if manager is None:
            continue
        metadata = types[manager]._meta
        output = schema.get_type(metadata.name)
        if not isinstance(output, GraphQLObjectType):
            continue
        native_pins: dict[str, _NativeField] = {}
        for key, (_original, expected, default, name) in binding.fields.items():
            field = native.fields.get(name)
            if (
                field is None
                or (field.out_name or name) != key
                or _default_key(field.default_value) != default
                or not _matches_type(field.type, expected)
            ):
                continue
            declaration = binding.declarations.get(key)
            public = (
                {
                    **declaration,
                    "field": _public_name(metadata, output, declaration["field"]),
                }
                if declaration
                else {}
            )
            output_field = output.fields.get(public.get("field"))
            if public and output_field is None:
                continue
            if public.get("operator") == "relation":
                target = next(
                    (
                        name
                        for name, value in types.items()
                        if getattr(
                            get_named_type(output_field.type), "graphene_type", None
                        )
                        is value
                    ),
                    None,
                )
                if target != public["manager"]:
                    public = {}
            if public:
                field.extensions = {**field.extensions, FILTER_EXTENSION: dict(public)}
            native_pins[name] = _NativeField(
                field,
                _type_key(field.type),
                default,
                field.out_name,
                MappingProxyType(public),
                output,
                output_field,
                _type_key(output_field.type) if output_field else None,
            )
        _NATIVE[native] = native_pins
    if schema.query_type is None:
        return
    graphene_query = getattr(schema.query_type, "graphene_type", None)
    for name, field in schema.query_type.fields.items():
        if id(field) in _ROOT_ARGUMENTS or root_population_owner(field.resolve) is None:
            continue
        generated = (
            next(
                (
                    value
                    for key, value in graphene_query._meta.fields.items()
                    if (value.name or to_camel_case(key)) == name
                ),
                None,
            )
            if graphene_query
            else None
        )
        if generated is None or len(generated.args) != len(field.args):
            continue
        argument_pins: dict[str, tuple[Any, object, object, str | None]] = {}
        for key, original in generated.args.items():
            public_name = original.name or to_camel_case(key)
            argument = field.args.get(public_name)
            if (
                argument is None
                or (argument.out_name or public_name) != key
                or _default_key(argument.default_value)
                != _default_key(original.default_value)
            ):
                break
            if key in ("filter", "exclude", "page", "page_size") and not _matches_type(
                argument.type, original.type
            ):
                break
            argument_pins[public_name] = (
                argument,
                _type_key(argument.type),
                _default_key(argument.default_value),
                argument.out_name,
            )
        else:
            identity = id(field)

            def release(_reference: Any, key: int = identity) -> None:
                _ROOT_ARGUMENTS.pop(key, None)

            _ROOT_ARGUMENTS[identity] = (ref(field, release), argument_pins)


def public_population_contract(
    field: Any, managers: Mapping[str, Any]
) -> dict[str, str] | None:
    """Prove the exact common root and its unchanged executable arguments."""
    from general_manager.api.graphql_resolvers import root_population_owner
    from general_manager.api.graphql_value_contract import list_envelope_valid

    owner = root_population_owner(getattr(field, "resolve", None))
    manager = next((name for name, item in managers.items() if item is owner), None)
    if (
        manager is None
        or not _root_arguments_valid(field)
        or not list_envelope_valid(field)
    ):
        return None
    return {
        "source": "interface_list",
        "manager": manager,
        "filter_source": "interface_filter",
    }
