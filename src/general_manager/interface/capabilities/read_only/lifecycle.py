"""Lifecycle tweaks for read-only interfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Type

from ..base import CapabilityName
from ..orm import OrmLifecycleCapability
from general_manager.interface.utils.models import GeneralManagerBasisModel

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.read_only import ReadOnlyInterface
    from general_manager.interface.orm_interface import OrmInterfaceBase
    from general_manager.manager.general_manager import GeneralManager


class ReadOnlyLifecycleCapability(OrmLifecycleCapability):
    """Ensure read-only interfaces enforce soft-delete and registration."""

    name: ClassVar[CapabilityName] = OrmLifecycleCapability.name

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["OrmInterfaceBase[Any]"],
        base_model_class: type[GeneralManagerBasisModel],
    ) -> tuple[
        dict[str, Any],
        type["OrmInterfaceBase[Any]"],
        type[GeneralManagerBasisModel],
    ]:
        meta = getattr(interface, "Meta", None)
        if meta is None:
            meta = type("Meta", (), {})
            interface.Meta = meta  # type: ignore[attr-defined]
        meta.use_soft_delete = True  # type: ignore[union-attr]
        return super().pre_create(
            name=name,
            attrs=attrs,
            interface=interface,
            base_model_class=GeneralManagerBasisModel,
        )

    def post_create(
        self,
        *,
        new_class: Type["GeneralManager"],
        interface_class: type["OrmInterfaceBase[Any]"],
        model: Type["GeneralManagerBasisModel"] | None,
    ) -> None:
        super().post_create(
            new_class=new_class,
            interface_class=interface_class,
            model=model,
        )
        from general_manager.manager.meta import GeneralManagerMeta

        if new_class not in GeneralManagerMeta.read_only_classes:
            GeneralManagerMeta.read_only_classes.append(new_class)
