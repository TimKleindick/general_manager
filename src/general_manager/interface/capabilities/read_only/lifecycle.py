"""Lifecycle tweaks for read-only interfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.db import models

from ..base import CapabilityName
from ..orm import OrmLifecycleCapability
from general_manager.interface.utils.models import GeneralManagerBasisModel

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase
    from general_manager.manager.general_manager import GeneralManager


class ReadOnlyLifecycleCapability(OrmLifecycleCapability):
    """Configure generated read-only managers for soft-delete synchronization."""

    name: ClassVar[CapabilityName] = OrmLifecycleCapability.name

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, object],
        interface: type["OrmInterfaceBase[models.Model]"],
        base_model_class: type[GeneralManagerBasisModel],
    ) -> tuple[
        dict[str, object],
        type["OrmInterfaceBase[models.Model]"],
        type[GeneralManagerBasisModel],
    ]:
        """
        Force read-only model generation onto the soft-delete base model.

        Read-only synchronization uses inactive rows to represent payload rows
        that disappeared from `_data`. This hook creates a nested `Meta` class
        when the interface does not define one, overwrites
        `Meta.use_soft_delete` to `True` even when it was explicitly `False`,
        ignores the caller-supplied `base_model_class`, and delegates to the
        ORM lifecycle with `GeneralManagerBasisModel`.

        Parameters:
            name: Name used for the generated model, factory, and manager
                lifecycle artifacts.
            attrs: Class namespace for the new manager. This mapping is passed
                to the parent ORM lifecycle, which mutates and returns it with
                generated interface, factory, and interface-type entries.
            interface: ORM interface class to configure; for normal use this
                is a `ReadOnlyInterface` subclass.
            base_model_class: Ignored by this capability; read-only managers
                always delegate with `GeneralManagerBasisModel`.

        Returns:
            The updated manager namespace, concrete interface subclass, and
            generated read-only model class returned by the ORM lifecycle.

        Raises:
            Exception: Specific exceptions are defined by the downstream
                component that raises them. Invalid interface attributes,
                Django model fields, Meta options, factory definitions, rule
                setup, support-capability lookup, or custom-field discovery
                errors propagate unchanged from the ORM lifecycle or the code
                that raised them.
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
        new_class: type["GeneralManager"],
        interface_class: type["OrmInterfaceBase[models.Model]"],
        model: type["GeneralManagerBasisModel"] | None,
    ) -> None:
        """
        Register the newly created manager class as a read-only class in GeneralManagerMeta.

        Runs the ORM post-create lifecycle first so the manager, interface, and
        generated model are linked. It then appends `new_class` to
        `GeneralManagerMeta.read_only_classes` when the exact class object is
        not already present, which lets startup hooks discover read-only
        managers for schema checks and data synchronization. When `model` is
        `None`, the ORM post-create lifecycle returns without linking model
        state, but this capability still registers the class.

        Parameters:
            new_class: Newly created `GeneralManager` subclass to register.
            interface_class: Concrete ORM interface class, normally a
                read-only interface bound to the generated model.
            model: Generated ORM model class, or `None` when no model was
                created by an upstream lifecycle.

        Raises:
            Exception: Specific exceptions are defined by the downstream
                component that raises them. Errors from ORM post-create
                linking, manager lookup, or soft-delete manager setup propagate
                unchanged before registry mutation.
        """
        super().post_create(
            new_class=new_class,
            interface_class=interface_class,
            model=model,
        )
        from general_manager.manager.meta import GeneralManagerMeta

        if new_class not in GeneralManagerMeta.read_only_classes:
            GeneralManagerMeta.read_only_classes.append(new_class)
