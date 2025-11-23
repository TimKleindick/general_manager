"""Lifecycle tweaks for read-only interfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Type

from ..base import CapabilityName
from ..orm import OrmLifecycleCapability
from general_manager.interface.utils.models import GeneralManagerBasisModel

if TYPE_CHECKING:  # pragma: no cover
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
        """
        Ensure the interface is configured to use soft-delete and delegate creation to the superclass using GeneralManagerBasisModel.

        Parameters:
            name (str): The name of the model/class being created.
            attrs (dict[str, Any]): Attribute dictionary for the new class.
            interface (type[OrmInterfaceBase[Any]]): The interface class; a Meta class will be added if missing and configured for soft-delete.
            base_model_class (type[GeneralManagerBasisModel]): Ignored by this implementation; the superclass is called with GeneralManagerBasisModel.

        Returns:
            tuple[dict[str, Any], type[OrmInterfaceBase[Any]], type[GeneralManagerBasisModel]]: The possibly-updated attrs, the (possibly-modified) interface class, and the base model class used for creation (GeneralManagerBasisModel).
        """
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
        """
        Register the newly created manager class as a read-only class in GeneralManagerMeta.

        Ensures base class post-create behavior is executed and then appends `new_class` to GeneralManagerMeta.read_only_classes if it is not already present.

        Parameters:
                new_class (Type[GeneralManager]): The newly created GeneralManager subclass to register as read-only.
                interface_class (type[OrmInterfaceBase[Any]]): The interface class that was used to create `new_class`.
                model (Type[GeneralManagerBasisModel] | None): The ORM model class associated with the new manager, or `None` if not applicable.
        """
        super().post_create(
            new_class=new_class,
            interface_class=interface_class,
            model=model,
        )
        from general_manager.manager.meta import GeneralManagerMeta

        if new_class not in GeneralManagerMeta.read_only_classes:
            GeneralManagerMeta.read_only_classes.append(new_class)
