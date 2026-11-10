"""Share declared relation metadata between permission checks and read proofs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from general_manager.manager.general_manager import GeneralManager


def orm_relation_normalizer(
    manager: type[GeneralManager] | None,
) -> PayloadNormalizer | None:
    """Reuse ORM metadata without loading backends for manager-free payloads."""
    interface = getattr(manager, "Interface", None)
    if not isinstance(interface, type):
        return None

    from general_manager.interface.orm_interface import OrmInterfaceBase
    from general_manager.interface.capabilities.orm.support import (
        get_support_capability,
    )
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )

    if not issubclass(interface, OrmInterfaceBase):
        return None
    normalizer = get_support_capability(interface).get_payload_normalizer(interface)
    return normalizer if isinstance(normalizer, PayloadNormalizer) else None


def permission_attribute_names(
    manager: type[GeneralManager] | None, attribute: str
) -> tuple[str, ...]:
    """Pair only declared singular ORM or supported request relation aliases."""
    interface = getattr(manager, "Interface", None)
    if not isinstance(interface, type):
        return (attribute,)

    normalizer = orm_relation_normalizer(manager)
    if normalizer is not None:
        field = normalizer.get_relation_field(attribute)
        return (field.name, field.attname) if field is not None else (attribute,)

    from general_manager.interface.interfaces.request import RequestInterface
    from general_manager.interface.capabilities.request.relation_inputs import (
        request_relations,
    )

    if issubclass(interface, RequestInterface):
        for relation in request_relations(interface):
            if attribute in (relation.name, relation.id_name):
                return (relation.name, relation.id_name)
    return (attribute,)
