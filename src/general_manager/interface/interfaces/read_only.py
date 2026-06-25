"""Read-only interface that mirrors JSON datasets into Django models."""

from __future__ import annotations

from typing import ClassVar

from general_manager.interface.orm_interface import (
    OrmInterfaceBase,
)
from general_manager.interface.bundles.database import READ_ONLY_CAPABILITIES
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.utils.models import GeneralManagerBasisModel

from general_manager.manager.general_manager import GeneralManager

__all__ = ["ReadOnlyInterface"]


class ReadOnlyInterface(OrmInterfaceBase[GeneralManagerBasisModel]):
    """Capability shell for read-only datasets mirrored into generated models.

    Subclasses are declared as a manager's nested ``Interface`` and define the
    Django fields for a generated model. The parent manager, not this class,
    provides the ``_data`` payload as either a JSON string or a list of row
    dictionaries. The configured read-only lifecycle forces
    ``Meta.use_soft_delete = True``, generates a model based on
    ``GeneralManagerBasisModel``, and registers the created manager for startup
    schema checks and synchronization.

    Construction and row loading are inherited from ``OrmInterfaceBase``: the
    public input is the mirrored row ``id`` parsed by ``Input(int)`` plus
    optional ``search_date: datetime | None``. Missing current or historical
    rows propagate the generated model's ``DoesNotExist`` exception. Read,
    query, history, validation, soft-delete, schema-check, and synchronization
    behavior comes from ``READ_ONLY_CAPABILITIES``; this shell class does not
    define separate public mutation methods.

    Read-only synchronization can raise the dedicated read-only configuration
    errors from the management capability when the parent manager is not bound,
    ``_data`` is missing or malformed, no unique row identity can be found, or
    relation lookups do not resolve exactly one row. Lifecycle and ORM errors
    propagate unchanged from the capability that raises them. The public
    manager surface inherited from ``GeneralManager`` is construction by id,
    attribute reads, ``all()``, ``filter()``, ``exclude()``, and history access.
    Those successful reads and queries return manager instances or buckets of
    manager instances backed by generated model rows; history access returns
    the generated model's history queryset. Because no create, update, or
    delete capability is configured, inherited mutation entry points are not
    read-only data APIs.

    Underscored attributes such as ``_interface_type`` and ``_parent_class`` are
    framework wiring, not application configuration. The parent manager's
    ``_data`` payload is the public read-only data source despite its legacy
    underscore name. Duplicate composite identities in ``_data`` are not
    rejected by the shell; the management capability falls back to full
    synchronization and processes rows in order for the same identity. Relation
    lookup dictionaries are flattened into Django ``__`` lookups; zero or
    multiple matches raise ``ReadOnlyRelationLookupError``, while malformed
    lookup keys or values propagate the Django query error. For many-to-many
    payloads, an omitted key leaves the relation unchanged, a present ``None``
    clears it, and a present list replaces it.
    """

    _interface_type: ClassVar[str] = "readonly"
    _parent_class: ClassVar[type[GeneralManager]]
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        READ_ONLY_CAPABILITIES,
    )
