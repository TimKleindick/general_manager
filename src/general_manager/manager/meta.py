"""Metaclass infrastructure for registering GeneralManager subclasses."""

from __future__ import annotations

from collections.abc import Callable
import threading
from _thread import LockType
from typing import TYPE_CHECKING, ClassVar, Iterable, TypeVar, cast

from general_manager.interface.base_interface import InterfaceBase
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.interface.manifests import ManifestCapabilityBuilder
    from django.db.models import Model


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
type MetaPreCreationHook = Callable[
    [str, dict[str, object], type[InterfaceBase]],
    tuple[dict[str, object], type[InterfaceBase], type["Model"] | None],
]

logger = get_logger("manager.meta")


class InvalidInterfaceTypeError(TypeError):
    """Raised when a GeneralManager is configured with an incompatible Interface class."""

    def __init__(self, interface_name: str) -> None:
        """
        Initialize an error for an invalid manager ``Interface`` declaration.

        Parameters:
            interface_name: Name of the configured interface class, or a best
                effort type name for non-class declarations.
        """
        super().__init__(f"{interface_name} must be a subclass of InterfaceBase.")


class MissingAttributeError(AttributeError):
    """Raised when a dynamically generated descriptor cannot locate the attribute."""

    def __init__(self, attribute_name: str, class_name: str) -> None:
        """
        Initialize the MissingAttributeError with the missing attribute and its owning class.

        Parameters:
            attribute_name: Name of the descriptor-backed manager field that was
                absent from the instance's ``_attributes`` mapping.
            class_name: Name of the manager class where the attribute lookup
                occurred.

        The exception message is set to "`{attribute_name} not found in {class_name}.`".
        """
        super().__init__(f"{attribute_name} not found in {class_name}.")


class AttributeEvaluationError(AttributeError):
    """Raised when evaluating a callable attribute raises an exception."""

    def __init__(self, attribute_name: str, error: Exception) -> None:
        """
        Initialize an AttributeEvaluationError that wraps an exception raised while evaluating a descriptor attribute.

        Parameters:
            attribute_name: Name of the descriptor-backed manager field whose
                callable value failed.
            error: Original exception raised by the callable value; it is
                chained as the cause by the descriptor.
        """
        super().__init__(f"Error calling attribute {attribute_name}: {error}.")


class InvalidManagerStateError(AttributeError):
    """Raised when reading manager fields after the instance was invalidated."""

    def __init__(
        self, manager_name: str, reason: str, attribute_name: str | None
    ) -> None:
        """
        Initialize an invalid-state access error.

        Parameters:
            manager_name: Concrete manager class name being accessed.
            reason: Stored invalidation reason, usually set by delete flows.
            attribute_name: Descriptor-backed field name being read, or
                ``None`` when the caller is checking the whole manager.
        """
        detail = (
            f"Cannot access attribute {attribute_name!r} on invalidated "
            f"{manager_name}: {reason}."
            if attribute_name is not None
            else f"Cannot access invalidated {manager_name}: {reason}."
        )
        super().__init__(detail)


class _nonExistent:
    pass


class GeneralManagerMeta(type):
    """
    Metaclass responsible for wiring GeneralManager interfaces and registries.

    The metaclass validates declared ``Interface`` classes, lets interface
    lifecycle hooks alter class creation, tracks manager classes for startup
    initialization and GraphQL generation, and lazily installs descriptor-backed
    fields for managers imported after startup. The process-global registries
    are append-only for class creation; this class does not deduplicate entries
    or lock registry mutation outside descriptor initialization.
    """

    all_classes: ClassVar[list[type[GeneralManager]]] = []
    read_only_classes: ClassVar[list[type[GeneralManager]]] = []
    pending_graphql_interfaces: ClassVar[list[type[GeneralManager]]] = []
    pending_attribute_initialization: ClassVar[list[type[GeneralManager]]] = []
    _attribute_initialization_lock: ClassVar[LockType] = threading.Lock()
    Interface: type[InterfaceBase]

    def __getattribute__(cls, attribute_name: str) -> object:
        """
        Initialize late-imported field descriptors before class attribute lookup.

        ``__getattr__`` is only reached for missing names, so inherited
        ``GeneralManager`` attributes must pass through here to let declared
        fields override inherited names the same way bootstrap initialization
        does. Once a manager class has completed descriptor initialization,
        attributes already present on that class use normal type lookup without
        rechecking initialization. Missing names and inherited public names
        still pass through initialization so late-discovered fields keep the
        existing override behavior. "Non-private" means the requested name does
        not start with ``"_"`` and is not exactly ``"Interface"``. Probing an
        unknown public name may call ``Interface.get_attributes()``, but it
        installs descriptors only when the probed name is declared by the
        interface.

        Parameters:
            attribute_name: Class attribute being read.

        Returns:
            The attribute returned by ``type.__getattribute__`` after optional
            descriptor initialization.

        Raises:
            AttributeError: Propagated from normal class attribute lookup.
            Exception: Exceptions from ``Interface.get_attributes()`` other than
                ``NotImplementedError`` propagate unchanged.
        """
        if not attribute_name.startswith("_") and attribute_name != "Interface":
            class_dict = type.__getattribute__(cls, "__dict__")
            initialized = class_dict.get("_gm_attributes_initialized", False)
            if initialized and attribute_name in class_dict:
                return type.__getattribute__(cls, attribute_name)
            manager_class = cast(type["GeneralManager"], cls)
            GeneralManagerMeta.ensure_attributes_initialized(
                manager_class, attribute_name
            )
        return type.__getattribute__(cls, attribute_name)

    def __getattr__(cls, attribute_name: str) -> object:
        """
        Lazily install field descriptors for manager classes imported after startup.

        Django app initialization wires descriptors for managers known at startup.
        Managers defined later, for example in an interactive shell or a test scratch
        module, still register with this metaclass but have not had descriptors
        attached yet. If the missing class attribute is a declared manager field,
        initialize the class and retry the lookup. Unknown names may call the
        interface attribute provider, but they do not cache ``_attributes`` or
        install descriptors unless the name is declared.

        Parameters:
            attribute_name: Missing class attribute being resolved.

        Returns:
            The descriptor-backed attribute value after initialization.

        Raises:
            AttributeError: If the name is not an interface-backed field.
            Exception: Exceptions from ``Interface.get_attributes()`` other than
                ``NotImplementedError`` propagate unchanged.
        """
        manager_class = cast(type["GeneralManager"], cls)
        if GeneralManagerMeta.ensure_attributes_initialized(
            manager_class, attribute_name
        ):
            return getattr(cls, attribute_name)
        raise AttributeError(attribute_name)

    @staticmethod
    def ensure_attributes_initialized(
        manager_class: type["GeneralManager"],
        attribute_name: str | None = None,
    ) -> bool:
        """
        Ensure descriptor-backed fields are installed for ``manager_class``.

        Returns ``True`` when the class exposes ``attribute_name`` after
        initialization, or when no specific attribute was requested and
        descriptors were installed. Returns ``False`` for unknown attributes or
        classes that do not expose interface-backed fields. The class-level
        ``manager_class._attributes`` cache stores the ``dict[str, object]``
        interface attribute mapping used to build descriptors; manager
        instances also store resolved per-instance values on
        ``instance._attributes``. This shared attribute name is intentional
        compatibility behavior. Attribute mapping key order is preserved when
        descriptors are installed, empty mappings still count as successful
        initialization when no specific ``attribute_name`` was requested, and
        non-string keys are not validated here but are incompatible with normal
        descriptor installation.

        Parameters:
            manager_class: Manager class whose descriptors should be installed.
            attribute_name: Optional single field name to validate before
                installing descriptors.

        Returns:
            ``True`` when descriptors were already present or successfully
            installed for the requested field; otherwise ``False``. A missing
            ``get_attributes`` method or a ``NotImplementedError`` from that
            method returns ``False``.

        Raises:
            Exception: Exceptions from ``Interface.get_attributes()`` other than
                ``NotImplementedError`` propagate unchanged.
        """
        try:
            interface = type.__getattribute__(manager_class, "Interface")
        except AttributeError:
            return False
        if not hasattr(interface, "get_attributes"):
            return False

        with GeneralManagerMeta._attribute_initialization_lock:
            if "_attributes" in vars(manager_class):
                attributes = manager_class._attributes
                if attribute_name is not None and attribute_name not in attributes:
                    return False
                if attribute_name is None or attribute_name not in vars(manager_class):
                    GeneralManagerMeta.create_at_properties_for_attributes(
                        attributes.keys(), manager_class
                    )
                type.__setattr__(manager_class, "_gm_attributes_initialized", True)
                return True

            try:
                attributes = interface.get_attributes()
            except NotImplementedError:
                return False
            if attribute_name is not None and attribute_name not in attributes:
                return False
            manager_class._attributes = attributes
            GeneralManagerMeta.create_at_properties_for_attributes(
                attributes.keys(), manager_class
            )
            type.__setattr__(manager_class, "_gm_attributes_initialized", True)
            try:
                GeneralManagerMeta.pending_attribute_initialization.remove(
                    manager_class
                )
            except ValueError:
                pass
            return True

    @staticmethod
    def ensure_manager_is_valid(
        instance: "GeneralManager",
        attribute_name: str | None = None,
    ) -> None:
        """
        Raise when descriptor-backed field access targets an invalidated manager.

        Missing ``_manager_state_valid`` is treated as valid. Missing
        ``_manager_state_reason`` falls back to ``"manager state is invalid"``
        when the manager is marked invalid.

        Parameters:
            instance: Manager instance being accessed.
            attribute_name: Field name being read, or ``None`` for a whole
                manager validity check.

        Raises:
            InvalidManagerStateError: If the manager carries an invalidated
                state flag.
        """
        if getattr(instance, "_manager_state_valid", True):
            return
        reason = getattr(instance, "_manager_state_reason", "manager state is invalid")
        raise InvalidManagerStateError(
            instance.__class__.__name__,
            reason,
            attribute_name,
        )

    def __new__(
        mcs: type["GeneralManagerMeta"],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, object],
    ) -> type:
        """
        Create a GeneralManager subclass, integrate any declared Interface hooks, and register the class for pending initialization and GraphQL processing.

        If the class body directly defines an `Interface` key in ``attrs``, validates it is a subclass of `InterfaceBase`, calls ``interface.handle_interface()`` on that class object, invokes the returned pre-creation hook to allow modification of the class namespace, creates the class, then invokes the returned post-creation hook and registers the class for attribute initialization and global tracking. Inherited ``Interface`` attributes are not treated as declared by this creation path; subclasses that should be managers must declare their own ``Interface`` class body entry. ``InterfaceBase`` itself satisfies the subclass check, but its default lifecycle path raises ``NotImplementedError`` unless a lifecycle capability or override is available. ``handle_interface()`` is a classmethod on ``InterfaceBase``; concrete interfaces may inherit the capability-driven implementation or override it. It must return ``(pre_creation, post_creation)`` callables. ``pre_creation`` is called with ``(name, attrs, interface)`` and must return ``(attrs, interface_cls, model)`` where ``attrs`` is a ``dict[str, object]`` namespace passed to ``type.__new__``, ``interface_cls`` is a ``type[InterfaceBase]`` used for post-creation and capability selection, and ``model`` is a Django ``Model`` subclass or ``None`` passed to ``post_creation``. The metaclass does not separately assign ``new_class.Interface = interface_cls``; the returned ``attrs`` mapping must contain the final ``"Interface"`` entry when the created class should expose that interface. ``post_creation`` is called with ``(new_class, interface_cls, model)`` and returns ``None``. ``model`` is lifecycle pass-through owned by the interface capability; the metaclass does not store or validate it except by passing it to ``post_creation``. Return values are not type-validated beyond tuple unpacking and the later calls that consume them. If `Interface` is not defined directly in ``attrs``, creates the class directly. If `settings.AUTOCREATE_GRAPHQL` is true, registers the created class for GraphQL interface processing, including plain classes without an interface; later GraphQL bootstrap owns any filtering or failure behavior. If class creation or any interface-backed setup step raises before the settings check, ``pending_graphql_interfaces`` is not appended.

        Capability selection is the interface capability manifest chosen by
        ``ManifestCapabilityBuilder`` for the returned ``interface_cls``. It is built by
        ``ManifestCapabilityBuilder.build(interface_cls)`` and stored by
        ``interface_cls.set_capability_selection(selection)`` for later
        capability-handler lookup. This metaclass
        does not append to ``read_only_classes``; the read-only lifecycle
        capability owns that registry.

        Parameters:
            mcs (type): The metaclass creating the class.
            name (str): Name of the class being created.
            bases (tuple[type, ...]): Base classes for the new class.
            attrs (dict[str, object]): Class namespace supplied during creation.

        Returns:
            type: The newly created subclass, possibly modified by Interface hooks.

        Raises:
            InvalidInterfaceTypeError: If a declared ``Interface`` is not an
                ``InterfaceBase`` subclass.
            NotImplementedError: Propagated from interfaces that cannot provide
                lifecycle hooks through ``handle_interface()``.
            TypeError: Propagated from malformed hook call signatures, invalid
                class namespace values passed to ``type.__new__``, invalid
                returned interface classes consumed by capability setup, or
                descriptor/class creation operations.
            ValueError: Propagated from malformed lifecycle hook return
                unpacking.
            NotImplementedError: Propagated from interfaces that cannot provide
                lifecycle hooks through ``handle_interface()``.
            Exception: Other exceptions from interface pre/post creation hooks,
                capability selection, or setting the capability selection
                propagate unchanged.
        """
        logger.debug(
            "creating manager class",
            context={
                "class_name": name,
                "module": attrs.get("__module__"),
                "has_interface": "Interface" in attrs,
            },
        )

        def create_new_general_manager_class(
            mcs: type["GeneralManagerMeta"],
            name: str,
            bases: tuple[type, ...],
            attrs: dict[str, object],
        ) -> type["GeneralManager"]:
            """Helper to instantiate the class via the default ``type.__new__``."""
            return cast(type["GeneralManager"], type.__new__(mcs, name, bases, attrs))

        if "Interface" in attrs:
            interface_candidate = attrs.pop("Interface")
            if not isinstance(interface_candidate, type) or not issubclass(
                interface_candidate, InterfaceBase
            ):
                interface_name = getattr(
                    interface_candidate,
                    "__name__",
                    type(interface_candidate).__name__,
                )
                raise InvalidInterfaceTypeError(interface_name)
            interface = interface_candidate
            pre_creation, post_creation = interface.handle_interface()
            pre_creation_for_meta = cast(MetaPreCreationHook, pre_creation)
            attrs, interface_cls, model = pre_creation_for_meta(name, attrs, interface)
            new_class = create_new_general_manager_class(mcs, name, bases, attrs)
            post_creation(new_class, interface_cls, model)
            selection = _capability_builder().build(interface_cls)
            interface_cls.set_capability_selection(selection)
            mcs.pending_attribute_initialization.append(new_class)
            mcs.all_classes.append(new_class)
            logger.debug(
                "registered manager class with interface",
                context={
                    "class_name": new_class.__name__,
                    "interface": interface_cls.__name__,
                },
            )

        else:
            new_class = create_new_general_manager_class(mcs, name, bases, attrs)
            logger.debug(
                "registered manager class without interface",
                context={
                    "class_name": new_class.__name__,
                },
            )

        from general_manager.conf import get_setting

        if get_setting("AUTOCREATE_GRAPHQL", False):
            mcs.pending_graphql_interfaces.append(new_class)
            logger.debug(
                "queued manager for graphql generation",
                context={
                    "class_name": new_class.__name__,
                },
            )

        return new_class

    @staticmethod
    def create_at_properties_for_attributes(
        attributes: Iterable[str], new_class: type[GeneralManager]
    ) -> None:
        """
        Attach descriptor properties to new_class for each name in attributes.

        Each generated descriptor returns the interface field type when accessed on the class and resolves the corresponding value from instance._attributes when accessed on an instance. Existing attributes with the same names are overwritten unconditionally, matching bootstrap descriptor installation. Generated descriptors implement only ``__get__``; assignment to the same name on the class replaces the descriptor, and instance assignment follows normal non-data-descriptor shadowing rules. Descriptor reads cache resolved values on the manager instance and replay dependency tracking when returning a cached manager value. Duplicate names are processed in order, so later duplicates overwrite earlier descriptors. Non-string names or iterables that raise during iteration propagate their original exception and may leave descriptors from earlier names installed. If called through ``ensure_attributes_initialized()``, those failures can occur after ``manager_class._attributes`` is cached and before pending-initialization removal. If the stored value is callable it is always treated as a deferred evaluator and invoked with instance._interface; expose literal callables by wrapping them in a non-callable container or by using a custom descriptor path. A missing stored key raises MissingAttributeError, but a missing ``instance._attributes`` mapping or missing ``instance._interface`` attribute raises the normal ``AttributeError``. A present but malformed ``_interface`` is passed to the callable unchanged; callable failures are wrapped in ``AttributeEvaluationError``.

        Parameters:
            attributes (Iterable[str]): Names of attributes for which descriptors will be created.
            new_class (type[GeneralManager]): Class that will receive the generated descriptor attributes.

        Raises:
            MissingAttributeError: Later raised by generated descriptors when an
                instance does not contain the requested attribute.
            AttributeEvaluationError: Later raised by generated descriptors when
                a callable attribute value fails.
            InvalidManagerStateError: Later raised by generated descriptors when
                reading an invalidated manager.
        """

        def descriptor_method(
            attr_name: str,
            new_class: type[GeneralManager],
        ) -> object:
            """
            Create a descriptor that provides attribute access backed by an instance's interface attributes.

            When accessed on the class, the descriptor returns the field type by delegating to the class's `Interface.get_field_type` for the configured attribute name. When accessed on an instance, it returns the value stored in `instance._attributes[attr_name]`. If the stored value is callable, it is invoked with `instance._interface` and the resulting value is returned. If the attribute is not present on the instance, a `MissingAttributeError` is raised. If invoking a callable attribute raises an exception, that error is wrapped in `AttributeEvaluationError`.

            Parameters:
                attr_name (str): The name of the attribute the descriptor resolves.
                new_class (type[GeneralManager]): The class that will receive the descriptor; used to access its `Interface`.

            Returns:
                descriptor (object): A descriptor object suitable for assigning as a class attribute.
            """

            class Descriptor:
                def __init__(
                    self,
                    descriptor_attr_name: str,
                    descriptor_class: type[GeneralManager],
                ) -> None:
                    self._attr_name = descriptor_attr_name
                    self._class = descriptor_class

                def __get__(
                    self,
                    instance: GeneralManager | None,
                    owner: type[GeneralManager] | None = None,
                ) -> object:
                    """
                    Provide the class field type when accessed on the class, or resolve and return the stored attribute value for an instance.

                    When accessed on a class, returns the field type from the class's Interface via Interface.get_field_type.
                    When accessed on an instance, retrieves the value stored in instance._attributes for this descriptor's attribute name;
                    if the stored value is callable, it is invoked with instance._interface and the result is returned.

                    Returns:
                        The field type (when accessed on the class) or the resolved attribute value from the instance.

                    Raises:
                        KeyError: If class-level field type resolution cannot
                            find the field in the interface metadata.
                        InvalidManagerStateError: If the instance was
                            invalidated before access.
                        MissingAttributeError: If the attribute is not present in instance._attributes.
                        AttributeEvaluationError: If calling a callable
                            attribute raises an exception; the original
                            exception is chained as ``__cause__`` and the
                            message starts with
                            ``"Error calling attribute {name}:"``.
                    """
                    if instance is None:
                        return self._class.Interface.get_field_type(self._attr_name)
                    GeneralManagerMeta.ensure_manager_is_valid(
                        instance, self._attr_name
                    )
                    cache = getattr(instance, "_attribute_value_cache", None)
                    if isinstance(cache, dict) and self._attr_name in cache:
                        cached_attribute = cache[self._attr_name]
                        track_dependency = getattr(
                            cached_attribute.__class__,
                            "_track_identification_dependency",
                            None,
                        )
                        identification = getattr(
                            cached_attribute,
                            "identification",
                            None,
                        )
                        if callable(track_dependency) and isinstance(
                            identification,
                            dict,
                        ):
                            track_dependency(identification)
                        return cached_attribute
                    attribute = instance._attributes.get(self._attr_name, _nonExistent)
                    if attribute is _nonExistent:
                        logger.warning(
                            "missing attribute on manager instance",
                            context={
                                "attribute": self._attr_name,
                                "manager": instance.__class__.__name__,
                            },
                        )
                        raise MissingAttributeError(
                            self._attr_name, instance.__class__.__name__
                        )
                    if callable(attribute):
                        try:
                            attribute = attribute(instance._interface)
                        except Exception as e:
                            logger.exception(
                                "attribute evaluation failed",
                                context={
                                    "attribute": self._attr_name,
                                    "manager": instance.__class__.__name__,
                                    "error": type(e).__name__,
                                },
                            )
                            raise AttributeEvaluationError(self._attr_name, e) from e
                    if isinstance(cache, dict):
                        cache[self._attr_name] = attribute
                    return attribute

            return Descriptor(attr_name, new_class)

        for attr_name in attributes:
            setattr(new_class, attr_name, descriptor_method(attr_name, new_class))
        type.__setattr__(new_class, "_gm_attributes_initialized", True)


_CAPABILITY_BUILDER: "ManifestCapabilityBuilder | None" = None


def _capability_builder() -> "ManifestCapabilityBuilder":
    """
    Lazily initialize and return the module-level ManifestCapabilityBuilder instance.

    Creates a ManifestCapabilityBuilder on first invocation, caches it in the module-global `_CAPABILITY_BUILDER`, and returns the cached instance on subsequent calls.

    Returns:
        ManifestCapabilityBuilder: The module-level ManifestCapabilityBuilder instance.
    """
    global _CAPABILITY_BUILDER
    if _CAPABILITY_BUILDER is None:
        from general_manager.interface.manifests import ManifestCapabilityBuilder

        _CAPABILITY_BUILDER = ManifestCapabilityBuilder()
    return _CAPABILITY_BUILDER
