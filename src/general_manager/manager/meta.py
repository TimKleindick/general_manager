"""Metaclass infrastructure for registering GeneralManager subclasses."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import wraps
import threading
from _thread import RLock as RLockType
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Iterable,
    TypeVar,
    cast,
)
from weakref import WeakKeyDictionary

from django.apps import apps

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.interface.base_interface import InterfaceBase
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.interface.manifests import ManifestCapabilityBuilder
    from general_manager.rule.rule import Rule
    from django.db.models import Model


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
PublicUseCallable = TypeVar("PublicUseCallable", bound=Callable[..., object])
type MetaPreCreationHook = Callable[
    [str, dict[str, object], type[InterfaceBase]],
    tuple[dict[str, object], type[InterfaceBase], type["Model"] | None],
]

logger = get_logger("manager.meta")
_MANAGER_DEPENDENCY_TRACKING_CLASS_CACHE: WeakKeyDictionary[
    type,
    type["GeneralManager"] | None,
] = WeakKeyDictionary()
_DESCRIPTOR_DEPENDENCY_TRACKING_CLASS_UNKNOWN = object()
_MANAGER_CLASS_STATE_MISSING = object()


def _manager_dependency_tracking_class(
    value_class: type,
) -> type["GeneralManager"] | None:
    """Return `value_class` when its instances can track manager dependencies."""
    try:
        return _MANAGER_DEPENDENCY_TRACKING_CLASS_CACHE[value_class]
    except KeyError:
        pass
    for candidate_class in value_class.__mro__:
        if "_track_identification_dependency" in candidate_class.__dict__:
            manager_class = cast(type["GeneralManager"], value_class)
            _MANAGER_DEPENDENCY_TRACKING_CLASS_CACHE[value_class] = manager_class
            return manager_class
    _MANAGER_DEPENDENCY_TRACKING_CLASS_CACHE[value_class] = None
    return None


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


class _InvalidManagerRuleCollectionError(TypeError):
    """Raised when manager rules are configured as a one-shot iterator."""

    def __init__(self, source_name: str) -> None:
        super().__init__(
            f"{source_name} must be a reusable iterable; "
            "one-shot iterators are not supported."
        )


class _nonExistent:
    pass


def _iter_manager_validation_rules(
    manager_class: type["GeneralManager"],
) -> Iterator["Rule[GeneralManager]"]:
    """Yield attached rules once after preflighting both reusable sources."""
    from general_manager.rule.rule import Rule

    interface = type.__getattribute__(manager_class, "Interface")
    model = getattr(interface, "_model", None)
    model_meta = getattr(model, "_meta", None)
    rule_sources = (
        ("Interface.rules", getattr(interface, "rules", ())),
        (
            "Interface._model._meta.rules",
            getattr(model_meta, "rules", ()),
        ),
    )
    reusable_rule_sources: list[Iterator[object]] = []
    for source_name, rule_source in rule_sources:
        if isinstance(rule_source, (str, bytes)) or not isinstance(
            rule_source, Iterable
        ):
            continue
        rule_iterator = iter(rule_source)
        if rule_iterator is rule_source:
            raise _InvalidManagerRuleCollectionError(source_name)
        reusable_rule_sources.append(rule_iterator)

    seen_rule_ids: set[int] = set()
    for rule_iterator in reusable_rule_sources:
        for candidate in rule_iterator:
            if not isinstance(candidate, Rule):
                continue
            candidate_id = id(candidate)
            if candidate_id in seen_rule_ids:
                continue
            seen_rule_ids.add(candidate_id)
            yield cast("Rule[GeneralManager]", candidate)


def _validate_rule_templates_before_public_use(
    method: PublicUseCallable,
) -> PublicUseCallable:
    """Validate templates before an explicit public manager operation runs."""

    @wraps(method)
    def wrapper(
        manager_or_class: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        manager_class = (
            manager_or_class
            if isinstance(manager_or_class, GeneralManagerMeta)
            else type(manager_or_class)
        )
        GeneralManagerMeta.ensure_rule_templates_validated_after_readiness(
            cast(type["GeneralManager"], manager_class)
        )
        return method(manager_or_class, *args, **kwargs)

    return cast(PublicUseCallable, wrapper)


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
    _attribute_initialization_lock: ClassVar[RLockType] = threading.RLock()
    Interface: type[InterfaceBase]

    def __getattribute__(cls, attribute_name: str) -> object:
        """
        Initialize late-imported field descriptors before class attribute lookup.

        ``__getattr__`` is only reached for missing names, so inherited
        ``GeneralManager`` attributes must pass through here to let declared
        fields override inherited names the same way bootstrap initialization
        does. Once a manager class has completed descriptor initialization,
        attributes already present on that class use normal type lookup without
        reinstalling descriptors. If those descriptors were installed before
        Django finished app loading, their first access after readiness validates
        attached rule templates before returning. Missing names and inherited
        public names still pass through initialization so late-discovered fields
        keep the existing override behavior. "Non-private" means the requested
        name does not start with ``"_"`` and is not exactly ``"Interface"``.
        Probing an unknown public name may call ``Interface.get_attributes()``,
        but it installs descriptors only when the probed name is declared by the
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
                if apps.ready and not class_dict.get(
                    "_gm_rule_templates_validated",
                    False,
                ):
                    manager_class = cast(type["GeneralManager"], cls)
                    GeneralManagerMeta.ensure_rule_templates_validated(manager_class)
                return type.__getattribute__(cls, attribute_name)
            attributes = class_dict.get("_attributes")
            if (
                initialized
                and isinstance(attributes, dict)
                and attribute_name not in attributes
            ):
                manager_class = cast(type["GeneralManager"], cls)
                GeneralManagerMeta.ensure_rule_templates_validated_after_readiness(
                    manager_class
                )
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
        if attribute_name.startswith("_") or attribute_name == "Interface":
            raise AttributeError(attribute_name)
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
                if apps.ready:
                    GeneralManagerMeta._ensure_rule_templates_validated_locked(
                        manager_class
                    )
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
            if apps.ready:
                GeneralManagerMeta._ensure_rule_templates_validated_locked(
                    manager_class
                )
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
    def _ensure_rule_templates_validated_locked(
        manager_class: type["GeneralManager"],
    ) -> None:
        """Validate attached rules while the attribute initialization lock is held."""
        if vars(manager_class).get("_gm_rule_templates_validated", False):
            return
        if vars(manager_class).get(
            "_gm_rule_templates_validation_in_progress",
            False,
        ):
            return
        class_state_before = dict(vars(manager_class))
        pending_positions = tuple(
            index
            for index, pending_manager in enumerate(
                GeneralManagerMeta.pending_attribute_initialization
            )
            if pending_manager is manager_class
        )
        type.__setattr__(
            manager_class,
            "_gm_rule_templates_validation_in_progress",
            True,
        )
        try:
            for rule in _iter_manager_validation_rules(manager_class):
                rule.validate_custom_error_message(manager_class)
            type.__setattr__(manager_class, "_gm_rule_templates_validated", True)
        except Exception:
            GeneralManagerMeta._restore_rule_validation_initialization_state_locked(
                manager_class,
                class_state_before,
                pending_positions,
            )
            raise
        finally:
            if "_gm_rule_templates_validation_in_progress" in vars(manager_class):
                type.__delattr__(
                    manager_class,
                    "_gm_rule_templates_validation_in_progress",
                )

    @staticmethod
    def _restore_rule_validation_initialization_state_locked(
        manager_class: type["GeneralManager"],
        class_state_before: dict[str, object],
        pending_positions: tuple[int, ...],
    ) -> None:
        """Roll back same-manager initialization committed by reentrant validation."""
        current_state = vars(manager_class)
        previous_attributes = class_state_before.get(
            "_attributes",
            _MANAGER_CLASS_STATE_MISSING,
        )
        current_attributes = current_state.get(
            "_attributes",
            _MANAGER_CLASS_STATE_MISSING,
        )
        field_names: set[str] = set()
        for attributes in (previous_attributes, current_attributes):
            if isinstance(attributes, dict):
                field_names.update(
                    field_name
                    for field_name in attributes
                    if isinstance(field_name, str)
                )

        for field_name in field_names:
            previous_field = class_state_before.get(
                field_name,
                _MANAGER_CLASS_STATE_MISSING,
            )
            if previous_field is _MANAGER_CLASS_STATE_MISSING:
                if field_name in vars(manager_class):
                    type.__delattr__(manager_class, field_name)
            else:
                type.__setattr__(manager_class, field_name, previous_field)

        for state_name in (
            "_attributes",
            "_gm_attributes_initialized",
            "_gm_rule_templates_validated",
        ):
            previous_value = class_state_before.get(
                state_name,
                _MANAGER_CLASS_STATE_MISSING,
            )
            if previous_value is _MANAGER_CLASS_STATE_MISSING:
                if state_name in vars(manager_class):
                    type.__delattr__(manager_class, state_name)
            else:
                type.__setattr__(manager_class, state_name, previous_value)

        pending = GeneralManagerMeta.pending_attribute_initialization
        pending[:] = [
            pending_manager
            for pending_manager in pending
            if pending_manager is not manager_class
        ]
        for position in pending_positions:
            pending.insert(min(position, len(pending)), manager_class)

    @staticmethod
    def ensure_rule_templates_validated(
        manager_class: type["GeneralManager"],
    ) -> None:
        """Validate attached rules once using the shared initialization lock."""
        with GeneralManagerMeta._attribute_initialization_lock:
            GeneralManagerMeta._ensure_rule_templates_validated_locked(manager_class)

    @staticmethod
    def ensure_rule_templates_validated_after_readiness(
        manager_class: type["GeneralManager"],
    ) -> None:
        """Validate templates for an eligible public use after app loading."""
        if apps.ready:
            GeneralManagerMeta.ensure_rule_templates_validated(manager_class)

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
            uses_default_identification_dependency_active = (
                name == "GeneralManager"
                or (
                    "_track_identification_dependency_active" not in attrs
                    and all(
                        bool(
                            getattr(
                                base,
                                "_gm_uses_default_identification_dependency_active",
                                False,
                            )
                        )
                        for base in bases
                    )
                )
            )
            new_class = cast(
                type["GeneralManager"],
                type.__new__(mcs, name, bases, attrs),
            )
            type.__setattr__(
                new_class,
                "_gm_uses_default_identification_dependency_active",
                uses_default_identification_dependency_active,
            )
            return new_class

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
                    self._dependency_tracking_value_class: type | object = (
                        _DESCRIPTOR_DEPENDENCY_TRACKING_CLASS_UNKNOWN
                    )
                    self._non_tracking_value_class: type | object = (
                        _DESCRIPTOR_DEPENDENCY_TRACKING_CLASS_UNKNOWN
                    )
                    self._dependency_tracking_manager_class: (
                        type[GeneralManager] | None
                    ) = None

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
                    GeneralManagerMeta.ensure_rule_templates_validated_after_readiness(
                        self._class
                    )
                    try:
                        ensure_as_of_compatible = object.__getattribute__(
                            instance, "_ensure_as_of_compatible"
                        )
                    except AttributeError:
                        pass
                    else:
                        ensure_as_of_compatible()
                    try:
                        manager_state_valid = instance._manager_state_valid
                    except AttributeError:
                        manager_state_valid = True
                    if not manager_state_valid:
                        reason = getattr(
                            instance,
                            "_manager_state_reason",
                            "manager state is invalid",
                        )
                        raise InvalidManagerStateError(
                            instance.__class__.__name__,
                            reason,
                            self._attr_name,
                        )
                    try:
                        cache = instance._attribute_value_cache
                    except AttributeError:
                        cache = None
                    if cache is not None:
                        try:
                            cached_attribute = cache[self._attr_name]
                        except (KeyError, TypeError):
                            pass
                        else:
                            cached_attribute_class = cached_attribute.__class__
                            if cached_attribute_class is self._non_tracking_value_class:
                                return cached_attribute
                            if (
                                cached_attribute_class
                                is self._dependency_tracking_value_class
                            ):
                                manager_class = self._dependency_tracking_manager_class
                            else:
                                manager_class = _manager_dependency_tracking_class(
                                    cached_attribute_class
                                )
                                self._dependency_tracking_value_class = (
                                    cached_attribute_class
                                )
                                self._dependency_tracking_manager_class = manager_class
                                if manager_class is None:
                                    self._non_tracking_value_class = (
                                        cached_attribute_class
                                    )
                            if (
                                manager_class is not None
                                and DependencyTracker.is_active()
                            ):
                                manager_attribute = cast(
                                    "GeneralManager", cached_attribute
                                )
                                manager_attribute._track_own_identification_dependency_active()
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
                    if cache is not None:
                        try:
                            cache[self._attr_name] = attribute
                        except TypeError:
                            pass
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
