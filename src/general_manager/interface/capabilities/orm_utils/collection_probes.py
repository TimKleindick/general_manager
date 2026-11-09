"""Native collection emptiness proven by the owning row's SQL query."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import getattr_static
from types import FunctionType
from typing import TYPE_CHECKING, cast
from weakref import ReferenceType, WeakKeyDictionary, ref

from django.db import models
from django.core.exceptions import FieldDoesNotExist

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.interface.orm_interface import OrmInterfaceBase


@dataclass(frozen=True, eq=False)
class NativeCollectionProbe:
    """Bind an emptiness hint to the exact generated collection accessor."""

    source_model: type[models.Model]
    manager: type[GeneralManager]
    filters: tuple[str, ...]

    def can_probe(self, database: str) -> bool:
        """Unknown query implementations must keep their normal evaluation."""
        from general_manager.interface.base_interface import InterfaceBase
        from general_manager.interface.capabilities.orm.support import (
            OrmPersistenceSupportCapability,
            OrmQueryCapability,
            get_support_capability,
        )
        from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
            PayloadNormalizer,
        )
        from general_manager.manager.general_manager import GeneralManager
        from general_manager.interface.orm_interface import OrmInterfaceBase

        interface = self.manager.Interface
        if not issubclass(interface, OrmInterfaceBase) or (
            getattr_static(self.manager, "filter")
            is not getattr_static(GeneralManager, "filter")
            or getattr_static(interface, "filter")
            is not getattr_static(InterfaceBase, "filter")
            or type(interface.get_capability_handler("query")) is not OrmQueryCapability
        ):
            return False
        for lookup in self.filters:
            try:
                relation = interface._model._meta.get_field(lookup)
                if isinstance(relation, models.ManyToManyRel):
                    through_field = relation.field
                    field_name = getattr(through_field, "m2m_field_name", None)
                elif isinstance(relation, models.ManyToManyField):
                    through_field = relation
                    field_name = getattr(relation, "m2m_reverse_field_name", None)
                else:
                    through_field = None
                    field_name = None
                source_field = relation
                if through_field is not None:
                    through = getattr(through_field.remote_field, "through", None)
                    if (
                        not isinstance(through, type)
                        or not issubclass(through, models.Model)
                        or not callable(field_name)
                    ):
                        return False
                    source_field = through._meta.get_field(field_name())
            except FieldDoesNotExist:
                return False
            # Django's direct-M2M prefetch can follow a non-PK through FK.
            # Such a relation cannot be probed using the owner's primary key.
            if (
                getattr(source_field, "target_field", None)
                is not self.source_model._meta.pk
            ):
                return False
        support = get_support_capability(interface)
        if type(support) is not OrmPersistenceSupportCapability:
            return False
        queryset = support.get_queryset(interface)
        return (
            type(queryset) is models.QuerySet
            and queryset.db == database
            and type(support.get_payload_normalizer(interface)) is PayloadNormalizer
        )

    def expression(self, database: str) -> models.Exists:
        # Use every physical related row, including inactive rows. An empty
        # superset proves emptiness without relying on default-manager filters.
        # This also preserves the exact native accessor's relation lookup.
        interface = cast("type[OrmInterfaceBase[models.Model]]", self.manager.Interface)
        queryset: models.QuerySet[models.Model] = models.QuerySet(
            interface._model, using=database
        )
        return models.Exists(
            queryset.filter(**dict.fromkeys(self.filters, models.OuterRef("pk")))
        )


@dataclass(frozen=True, slots=True)
class CollectionProbeHint:
    """An empty-relation witness valid for one unmodified calculation run."""

    probe: NativeCollectionProbe
    context: ReferenceType[object]
    lifetime_generation: int
    invalidation_generation: int


_COLLECTION_PROBES: WeakKeyDictionary[
    Callable[..., object], ReferenceType[NativeCollectionProbe]
] = WeakKeyDictionary()


def register_collection_probe(
    accessor: Callable[..., object], probe: NativeCollectionProbe
) -> None:
    """Register only accessors built by the native ORM descriptor factory."""
    _COLLECTION_PROBES[accessor] = ref(probe)


def collection_probe(accessor: object) -> NativeCollectionProbe | None:
    if not isinstance(accessor, FunctionType):
        return None
    binding = _COLLECTION_PROBES.get(accessor)
    return None if binding is None else binding()


def bind_collection_probe_hint(
    probe: NativeCollectionProbe,
) -> CollectionProbeHint | None:
    """Bind a SQL emptiness witness to the active cache lifetime and generation."""
    from general_manager.cache.run_context import current_calculation_run_context

    context = current_calculation_run_context()
    if context is None:
        return None
    return CollectionProbeHint(
        probe=probe,
        context=ref(context),
        lifetime_generation=context.lifetime_generation,
        invalidation_generation=context.orm_bucket_invalidation_generation,
    )


def collection_probe_hint_is_current(hint: CollectionProbeHint) -> bool:
    """Return whether a witness belongs to the active, unmodified calculation run."""
    from general_manager.cache.run_context import current_calculation_run_context

    context = current_calculation_run_context()
    return (
        context is not None
        and hint.context() is context
        and hint.lifetime_generation == context.lifetime_generation
        and hint.invalidation_generation == context.orm_bucket_invalidation_generation
    )


def collection_probe_hint(
    row: models.Model, probe: NativeCollectionProbe
) -> CollectionProbeHint | None:
    """Return the current row witness for one native collection accessor."""
    for hint in vars(row).get("_gm_empty_collection_probes", ()):
        if (
            isinstance(hint, CollectionProbeHint)
            and hint.probe is probe
            and collection_probe_hint_is_current(hint)
        ):
            return hint
    return None


def collection_is_proven_empty(row: models.Model, probe: NativeCollectionProbe) -> bool:
    """Return whether this row has a current native SQL emptiness witness."""
    return collection_probe_hint(row, probe) is not None
