"""Resolve declared request relationships without changing remote wire keys."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace, UnionType
from typing import TYPE_CHECKING, SupportsIndex, Union, get_args, get_origin

if TYPE_CHECKING:
    from general_manager.interface.interfaces.request import RequestInterface
    from general_manager.manager.general_manager import GeneralManager


class RequestRelationReferenceError(ValueError):
    """A request relationship cannot be identified unambiguously."""

    def __init__(self, name: str, target: type[GeneralManager]) -> None:
        """Describe the target and the reference forms needed to identify it."""
        super().__init__(
            f"Request relation {name!r} requires a {target.__name__} instance "
            "or a complete identification mapping; scalar references require "
            "a target with a single 'id' input."
        )


@dataclass(frozen=True)
class RequestRelation:
    """A direct relationship declared by a manager-valued request attribute."""

    name: str
    target: type[GeneralManager]

    @property
    def id_name(self) -> str:
        """Return the identifier alias paired with this canonical relation."""
        return f"{self.name}_id"


def _direct_manager_type(value: object) -> type[GeneralManager] | None:
    """Recognize direct manager types and their optional, but not collection, forms."""
    from general_manager.manager.general_manager import GeneralManager

    if isinstance(value, type) and issubclass(value, GeneralManager):
        return value
    if get_origin(value) in (Union, UnionType):
        members = get_args(value)
        non_null = [member for member in members if member is not type(None)]
        if len(non_null) == 1 and len(members) == 2:
            return _direct_manager_type(non_null[0])
    return None


def request_relations(
    interface_cls: type[RequestInterface],
) -> tuple[RequestRelation, ...]:
    """Use declared types, never scalar names, to recognize direct references."""
    relations = []
    for name, field in interface_cls.fields.items():
        if field.is_derived:
            continue
        target = _direct_manager_type(field.field_type)
        if target is not None:
            relations.append(RequestRelation(name, target))
    return tuple(relations)


def _reference(relation: RequestRelation, value: object) -> GeneralManager | None:
    """Resolve the declared target without guessing a composite scalar identity."""
    from general_manager.manager.general_manager import GeneralManager

    if value is None or isinstance(value, relation.target):
        return value
    if isinstance(value, Mapping):
        return relation.target(**dict(value))
    if isinstance(value, GeneralManager):
        raise RequestRelationReferenceError(relation.name, relation.target)
    inputs = relation.target.Interface.input_fields
    if inputs and tuple(inputs) != ("id",):
        raise RequestRelationReferenceError(relation.name, relation.target)
    return relation.target(id=value)


def _identifier(value: GeneralManager | None) -> object:
    """Expose a single raw ID or the complete composite identification mapping."""
    if value is None:
        return None
    identification = value.identification
    if tuple(identification) == ("id",):
        return identification["id"]
    return dict(identification)


def reconcile_request_relation_kwargs(
    interface_cls: type[RequestInterface],
    kwargs: Mapping[str, object],
    *,
    has_serializer: bool = False,
) -> dict[str, object]:
    """Reconcile explicit pairs while preserving supplied keys and lone values.

    Application serializers still own remote key names and representation. Only
    a conflicting pair deliberately changes the canonical relationship value.
    Lone wire values are not interpreted unless a check reads that relationship.
    """
    reconciled = dict(kwargs)
    for relation in request_relations(interface_cls):
        if relation.name not in kwargs or relation.id_name not in kwargs:
            continue
        reference = _reference(relation, kwargs[relation.id_name])
        original = kwargs[relation.name]
        if reference is None:
            reconciled[relation.name] = None
        elif isinstance(original, Mapping):
            reconciled[relation.name] = dict(reference.identification)
        elif isinstance(original, relation.target) or (
            original is None and has_serializer
        ):
            reconciled[relation.name] = reference
        else:
            # A raw scalar pair was already valid JSON before reconciliation.
            raw_identifier = kwargs[relation.id_name]
            if isinstance(raw_identifier, Mapping) and tuple(
                reference.identification
            ) == ("id",):
                raw_identifier = raw_identifier.get("id", _identifier(reference))
            reconciled[relation.name] = raw_identifier
    return reconciled


def _candidate_relation_values(
    relation: RequestRelation,
    values: Mapping[str, object],
    explicit_values: Mapping[str, object],
) -> dict[str, object]:
    """Expose consistent aliases without turning old state into submitted input."""
    if relation.id_name in explicit_values:
        value = explicit_values[relation.id_name]
    elif relation.name in explicit_values:
        value = explicit_values[relation.name]
    elif relation.name in values:
        value = values[relation.name]
    elif relation.id_name in values:
        value = values[relation.id_name]
    else:
        return {}
    reference = _reference(relation, value)
    return {relation.name: reference, relation.id_name: _identifier(reference)}


def request_relation_values(
    interface_cls: type[RequestInterface],
    values: Mapping[str, object],
    *,
    explicit_values: Mapping[str, object],
    attribute: str,
) -> dict[str, object]:
    """Resolve only the relationship needed by this permission attribute read."""
    for relation in request_relations(interface_cls):
        if attribute in (relation.name, relation.id_name):
            return _candidate_relation_values(relation, values, explicit_values)
    return {}


class RequestRuleCandidate(SimpleNamespace):
    """Keep ordinary Rule attributes strict and resolve relationships on demand."""

    __slots__ = (
        "_gm_existing",
        "_gm_explicit",
        "_gm_interface",
        "_gm_relations",
        "_gm_values",
    )

    def __init__(
        self,
        interface_cls: type[RequestInterface],
        values: Mapping[str, object],
        *,
        explicit_values: Mapping[str, object],
        existing_payload: Mapping[str, object] | None,
    ) -> None:
        """Keep ordinary values separate from deferred relation and snapshot reads."""
        self._gm_interface = interface_cls
        self._gm_values = values
        self._gm_explicit = explicit_values
        self._gm_existing = existing_payload
        self._gm_relations = {
            name: relation
            for relation in request_relations(interface_cls)
            for name in (relation.name, relation.id_name)
        }
        super().__init__(
            **{
                name: value
                for name, value in values.items()
                if name not in self._gm_relations
            }
        )

    def __getattr__(self, name: str) -> object:
        """Resolve one lazy alias pair while preserving normal missing attributes."""
        try:
            relations: dict[str, RequestRelation] = object.__getattribute__(
                self, "_gm_relations"
            )
        except AttributeError:
            raise AttributeError(name) from None
        relation = relations.get(name)
        if relation is None:
            raise AttributeError(name)
        values = self._gm_values
        if (
            relation.name not in self._gm_explicit
            and relation.id_name not in self._gm_explicit
            and self._gm_existing is not None
        ):
            values = {
                **values,
                relation.name: self._gm_interface.resolve_payload_value(
                    self._gm_existing, relation.name
                ),
            }
        resolved = _candidate_relation_values(relation, values, self._gm_explicit)
        if not resolved:
            raise AttributeError(name)
        self.__dict__.update(resolved)
        return resolved[name]

    def __reduce_ex__(
        self, protocol: SupportsIndex
    ) -> tuple[
        object,
        tuple[type[RequestRuleCandidate]],
        tuple[dict[str, object], dict[str, object]],
    ]:
        """Copy namespace and lazy slot state without rerunning the required constructor."""
        slot_state: dict[str, object] = {}
        for name in self.__slots__:
            try:
                slot_state[name] = object.__getattribute__(self, name)
            except AttributeError:
                continue
        return (
            SimpleNamespace.__new__,
            (type(self),),
            (self.__dict__, slot_state),
        )
