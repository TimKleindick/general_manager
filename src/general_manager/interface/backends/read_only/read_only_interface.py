"""Read-only interface that mirrors JSON datasets into Django models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, ClassVar, Type

import json
from django.core.checks import Warning
from django.db import connection, models, transaction, IntegrityError
from general_manager.logging import get_logger

from general_manager.interface.backends.database.database_based_interface import (
    OrmPersistenceInterface,
    GeneralManagerBasisModel,
    attributes,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    interfaceBaseClass,
)
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)
from general_manager.interface.utils.errors import MissingUniqueFieldError

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


logger = get_logger("interface.read_only")


class ReadOnlyInterface(OrmPersistenceInterface[GeneralManagerBasisModel]):
    """Interface that reads static JSON data into a managed read-only model."""

    _interface_type: ClassVar[str] = "readonly"
    _parent_class: ClassVar[Type["GeneralManager"]]
    capability_overrides = OrmPersistenceInterface.capability_overrides.copy()
    capability_overrides.update({"read_only_management": ReadOnlyManagementCapability})

    @classmethod
    def get_unique_fields(cls, model: Type[models.Model]) -> set[str]:
        """
        Determine which fields on the given Django model uniquely identify its instances.

        The result includes fields declared with `unique=True` (excluding a primary key named "id"), any fields in `unique_together` tuples, and fields referenced by `UniqueConstraint` objects.
        """
        capability = cls._read_only_capability()
        return capability.get_unique_fields(model)

    @classmethod
    def sync_data(cls) -> None:
        """Synchronize the managed data using the configured capability."""
        capability = cls._read_only_capability()
        warnings = cls.ensure_schema_is_up_to_date(cls._parent_class, cls._model)
        if warnings:
            logger.warning(
                "readonly schema out of date",
                context={
                    "manager": cls._parent_class.__name__,
                    "model": cls._model.__name__,
                },
            )
            return
        unique_fields = cls.get_unique_fields(cls._model)
        if not unique_fields:
            raise MissingUniqueFieldError(cls._parent_class.__name__)
        capability.sync_data(
            cls,
            connection=connection,
            transaction=transaction,
            integrity_error=IntegrityError,
            json_module=json,
            logger_instance=logger,
            unique_fields=unique_fields,
            schema_validated=True,
        )

    @classmethod
    def ensure_schema_is_up_to_date(
        cls, new_manager_class: Type[GeneralManager], model: Type[models.Model]
    ) -> list[Warning]:
        """
        Check whether the database schema matches the model definition.
        """
        capability = cls._read_only_capability()
        return capability.ensure_schema_is_up_to_date(
            cls,
            new_manager_class,
            model,
            connection=connection,
        )

    @classmethod
    def read_only_post_create(cls, func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator for post-creation hooks that registers a new manager class as read-only.

        After the wrapped post-creation function is executed, the newly created manager class is added to the meta-class's list of read-only classes, marking it as a read-only interface.
        """

        capability = cls._read_only_capability()
        return capability.wrap_post_create(func)

    @classmethod
    def read_only_pre_create(cls, func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Wrap a manager pre-creation function to ensure the interface has a Meta with use_soft_delete=True before invocation.

        The returned wrapper creates a dummy Meta on the provided interface if one does not exist, sets Meta.use_soft_delete = True, and then calls the original pre-creation function with the same arguments (including the original `base_model_class`).

        Parameters:
            func (Callable[..., Any]): A pre-creation hook that accepts (name, attrs, interface, base_model_class) and returns (attrs, interface, base_model_class | None).

        Returns:
            Callable[..., Any]: A wrapper function that performs the Meta initialization and soft-delete enabling, then returns the wrapped function's result.
        """

        capability = cls._read_only_capability()
        return capability.wrap_pre_create(func)

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Return the pre- and post-creation hook methods for integrating the interface with a manager meta-class system.

        The returned tuple includes:
        - A pre-creation method that ensures the base model class is set for read-only operation.
        - A post-creation method that registers the manager class as read-only.

        Returns:
            tuple: The pre-creation and post-creation hook methods for manager class lifecycle integration.
        """
        return cls.read_only_pre_create(cls._pre_create), cls.read_only_post_create(
            cls._post_create
        )

    @classmethod
    def _read_only_capability(cls) -> ReadOnlyManagementCapability:
        handler = cls.get_capability_handler("read_only_management")
        if isinstance(handler, ReadOnlyManagementCapability):
            return handler
        return ReadOnlyManagementCapability()
