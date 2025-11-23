"""Abstract interface layer shared by all GeneralManager implementations."""

from __future__ import annotations
from abc import ABC
import warnings
import inspect
from typing import (
    Type,
    TYPE_CHECKING,
    Any,
    TypeVar,
    Iterable,
    ClassVar,
    Callable,
    TypedDict,
    cast,
)
from django.conf import settings
from django.db.models import Model

from general_manager.utils.args_to_kwargs import args_to_kwargs
from general_manager.api.property import GraphQLProperty
from general_manager.interface.capabilities.base import Capability, CapabilityName
from general_manager.interface.capabilities.configuration import (
    CapabilityConfigEntry,
    InterfaceCapabilityConfig,
    iter_capability_entries,
)
from general_manager.interface.capabilities.factory import CapabilityOverride
from general_manager.interface.infrastructure.startup_hooks import register_startup_hook
from general_manager.interface.infrastructure.system_checks import register_system_check

if TYPE_CHECKING:
    from general_manager.manager.input import Input
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.bucket.base_bucket import Bucket
    from general_manager.interface.manifests.capability_models import (
        CapabilitySelection,
    )
    from general_manager.interface.utils.models import GeneralManagerBasisModel
    from general_manager.interface.manifests.capability_builder import (
        ManifestCapabilityBuilder,
    )


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
type generalManagerClassName = str
type attributes = dict[str, Any]
type interfaceBaseClass = Type[InterfaceBase]
type newlyCreatedInterfaceClass = Type[InterfaceBase]
type relatedClass = Type[Model] | None
type newlyCreatedGeneralManagerClass = Type[GeneralManager]

type classPreCreationMethod = Callable[
    [
        generalManagerClassName,
        attributes,
        interfaceBaseClass,
        type["GeneralManagerBasisModel"] | None,
    ],
    tuple[attributes, interfaceBaseClass, relatedClass],
]

type classPostCreationMethod = Callable[
    [newlyCreatedGeneralManagerClass, newlyCreatedInterfaceClass, relatedClass],
    None,
]


class AttributeTypedDict(TypedDict):
    """Describe metadata captured for each interface attribute."""

    type: type
    default: Any
    is_required: bool
    is_editable: bool
    is_derived: bool


class UnexpectedInputArgumentsError(TypeError):
    """Raised when parseInputFields receives keyword arguments not defined by the interface."""

    def __init__(self, extra_args: Iterable[str]) -> None:
        """
        Initialize the exception with a message listing unexpected input argument names.

        Parameters:
            extra_args (Iterable[str]): Names of the unexpected keyword arguments to include in the error message.
        """
        extras = ", ".join(extra_args)
        super().__init__(f"Unexpected arguments: {extras}.")


class MissingInputArgumentsError(TypeError):
    """Raised when required interface inputs are not supplied."""

    def __init__(self, missing_args: Iterable[str]) -> None:
        """
        Initialize the exception for missing required input arguments.

        Parameters:
            missing_args (Iterable[str]): Names of required input arguments that were not provided; these will be joined into the exception message.
        """
        missing = ", ".join(missing_args)
        super().__init__(f"Missing required arguments: {missing}.")


class CircularInputDependencyError(ValueError):
    """Raised when input fields declare circular dependencies."""

    def __init__(self, unresolved: Iterable[str]) -> None:
        """
        Initialize the CircularInputDependencyError with the names of inputs involved in the cycle.

        Parameters:
            unresolved (Iterable[str]): Iterable of input names that form the detected circular dependency.
        """
        names = ", ".join(unresolved)
        super().__init__(f"Circular dependency detected among inputs: {names}.")


class InvalidInputTypeError(TypeError):
    """Raised when an input value does not match its declared type."""

    def __init__(self, name: str, provided: type, expected: type) -> None:
        """
        Initialize the InvalidInputTypeError with a message describing a type mismatch for a named input.

        Parameters:
            name (str): The name of the input field with the invalid type.
            provided (type): The actual type that was provided.
            expected (type): The type that was expected.
        """
        super().__init__(f"Invalid type for {name}: {provided}, expected: {expected}.")


class InvalidPossibleValuesTypeError(TypeError):
    """Raised when an input's possible_values configuration is not callable or iterable."""

    def __init__(self, name: str) -> None:
        """
        Exception raised when an input's `possible_values` configuration is neither callable nor iterable.

        Parameters:
            name (str): The input field name whose `possible_values` is invalid; included in the exception message.
        """
        super().__init__(f"Invalid type for possible_values of input {name}.")


class InvalidInputValueError(ValueError):
    """Raised when a provided input value is not within the allowed set."""

    def __init__(self, name: str, value: object, allowed: Iterable[object]) -> None:
        """
        Initialize the exception with a message describing an invalid input value for a specific field.

        Parameters:
            name (str): The name of the input field that received the invalid value.
            value (object): The value that was provided and deemed invalid.
            allowed (Iterable[object]): An iterable of permitted values for the field; used to include allowed options in the exception message.
        """
        super().__init__(
            f"Invalid value for {name}: {value}, allowed: {list(allowed)}."
        )


class InterfaceBase(ABC):
    """Common base API for interfaces backing GeneralManager classes."""

    _parent_class: ClassVar[Type["GeneralManager"]]
    _interface_type: ClassVar[str]
    input_fields: ClassVar[dict[str, "Input"]]
    lifecycle_capability_name: ClassVar[CapabilityName | None] = None
    _capabilities: ClassVar[frozenset[CapabilityName]] = frozenset()
    _capability_selection: ClassVar["CapabilitySelection | None"] = None
    _capability_handlers: ClassVar[dict[CapabilityName, "Capability"]] = {}
    capability_overrides: ClassVar[dict[CapabilityName, CapabilityOverride]] = {}
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = tuple()
    _configured_capabilities_applied: ClassVar[bool] = False
    _automatic_capability_builder: ClassVar["ManifestCapabilityBuilder | None"] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Initialize capability-related class state for newly created subclasses.

        This method resets per-subclass capability registries and configuration to a clean default, merges configured capability overrides into the class's capability_overrides mapping, and clears the flag that marks configured capabilities as applied. Any keyword arguments are forwarded to the superclass implementation.
        """
        super().__init_subclass__(**kwargs)
        cls._capabilities = frozenset()
        cls._capability_selection = None
        cls._capability_handlers = {}
        cls.capability_overrides = dict(getattr(cls, "capability_overrides", {}))
        cls.configured_capabilities = tuple(
            getattr(cls, "configured_capabilities", tuple()),
        )
        configured_overrides = cls._build_configured_capability_overrides()
        for name, override in configured_overrides.items():
            cls.capability_overrides.setdefault(name, override)
        cls._configured_capabilities_applied = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the interface using the provided identification inputs.

        Positional arguments are mapped to the interface's declared input fields by position; keyword arguments are matched by name. Inputs are validated and normalized according to the interface's input field definitions and the resulting normalized identification is stored on the instance as `self.identification`.

        Parameters:
            *args: Positional identification values corresponding to the interface's input field order.
            **kwargs: Named identification values matching the interface's input field names.
        """
        identification = self.parse_input_fields_to_identification(*args, **kwargs)
        self.identification = self.format_identification(identification)

    @classmethod
    def set_capability_selection(cls, selection: "CapabilitySelection") -> None:
        """
        Attach a resolved capability selection to the interface and update its active capability names.

        Parameters:
            selection (CapabilitySelection): The resolved capability selection whose `all` set will become the interface's active capability names.
        """
        cls._capability_selection = selection
        cls._capabilities = selection.all

    @classmethod
    def get_capabilities(cls) -> frozenset[CapabilityName]:
        """
        Get the capability names attached to this interface class.

        Returns:
            frozenset[CapabilityName]: A frozenset of capability names registered on the interface class.
        """
        cls._ensure_capabilities_initialized()
        return cls._capabilities

    @classmethod
    def get_capability_handler(cls, name: CapabilityName) -> "Capability | None":
        """
        Retrieve the capability instance associated with the given capability name.

        Parameters:
            name (CapabilityName): The capability identifier to look up.

        Returns:
            Capability | None: The capability handler registered for `name`, or `None` if no handler is bound.
        """
        cls._ensure_capabilities_initialized()
        return cls._capability_handlers.get(name)

    @classmethod
    def iter_capability_configs(cls) -> Iterable[InterfaceCapabilityConfig]:
        """
        Iterate configured capability entries declared on the interface.

        Returns:
            Iterable[InterfaceCapabilityConfig]: An iterable of capability configuration entries registered on the interface.
        """
        return iter_capability_entries(cls.configured_capabilities)

    @classmethod
    def require_capability(
        cls,
        name: CapabilityName,
        *,
        expected_type: type["Capability"] | None = None,
    ) -> "Capability":
        """
        Retrieve the configured capability handler for the interface by name.

        Parameters:
            name (CapabilityName): The capability identifier to look up.
            expected_type (type[Capability] | None): If provided, require the returned handler to be an instance of this type.

        Returns:
            Capability: The capability handler instance corresponding to `name`.

        Raises:
            NotImplementedError: If the interface has no capability configured under `name`.
            TypeError: If `expected_type` is provided and the handler is not an instance of that type.
        """
        handler = cls.get_capability_handler(name)
        if handler is None:
            raise NotImplementedError(
                f"{cls.__name__} does not have the '{name}' capability configured."
            )
        if expected_type is not None and not isinstance(handler, expected_type):
            message = (
                f"Capability '{name}' on {cls.__name__} must be an instance of "
                f"{expected_type.__name__}."
            )
            raise TypeError(message)
        return handler

    def _require_capability(
        self,
        name: CapabilityName,
        *,
        expected_type: type["Capability"] | None = None,
    ) -> "Capability":
        """
        Retrieve the capability handler with the given name from this interface's class, enforcing an expected handler type if provided.

        Parameters:
                name (CapabilityName): The capability name to retrieve.
                expected_type (type[Capability] | None): If provided, the returned handler must be an instance of this type.

        Returns:
                Capability: The capability handler associated with `name`.

        Raises:
                NotImplementedError: If the named capability is not available.
                TypeError: If the found capability is not an instance of `expected_type`.
        """
        return self.__class__.require_capability(
            name,
            expected_type=expected_type,
        )

    @classmethod
    def capability_selection(cls) -> "CapabilitySelection | None":
        """
        Return the resolved capability selection associated with this interface.

        @returns
            `CapabilitySelection` if a selection has been set, `None` otherwise.
        """
        cls._ensure_capabilities_initialized()
        return cls._capability_selection

    @classmethod
    def _lifecycle_capability(cls) -> "Capability | None":
        """
        Retrieve the lifecycle capability handler attached to the interface, if one is configured.

        Returns:
            Capability | None: The `Capability` instance identified by the class's `lifecycle_capability_name`, or `None` if no lifecycle capability is configured.
        """
        name = getattr(cls, "lifecycle_capability_name", None)
        if not name:
            return None
        return cls.get_capability_handler(name)

    @classmethod
    def _ensure_capabilities_initialized(cls) -> None:
        """
        Ensure the interface's capability registry is initialized and configured for this class.

        If no capability selection has been attached, construct or reuse an automatic ManifestCapabilityBuilder to build the class's capabilities. Afterward, instantiate and bind any configured capability overrides so capability handlers, startup hooks, and system checks are registered.
        """
        if cls._capability_selection is None:
            from general_manager.interface.manifests import ManifestCapabilityBuilder

            builder = cls._automatic_capability_builder
            if builder is None:
                builder = ManifestCapabilityBuilder()
                cls._automatic_capability_builder = builder
            builder.build(cls)
        cls._apply_configured_capabilities()

    @classmethod
    def _apply_configured_capabilities(cls) -> None:
        """
        Apply and bind the interface's configured capability handlers exactly once.

        Instantiates each entry returned by iter_capability_configs and binds the resulting capability handlers to the class. This method is idempotent: if configured capabilities have already been applied it does nothing. It also sets the internal flag that marks the configured capabilities as applied.
        """
        if cls._configured_capabilities_applied:
            return
        configs = tuple(cls.iter_capability_configs())
        if not configs:
            cls._configured_capabilities_applied = True
            return
        for config in configs:
            handler = cls._instantiate_configured_capability(config)
            cls._bind_capability_handler(handler)
        cls._configured_capabilities_applied = True

    @classmethod
    def _build_configured_capability_overrides(
        cls,
    ) -> dict[CapabilityName, CapabilityOverride]:
        """
        Builds a mapping of configured capability names to capability handlers or factory callables.

        Instantiates an entry for each configured capability: if the configured entry supplies options, the value is a factory callable (created via _make_capability_factory) that will produce the capability when invoked; otherwise the value is the capability handler class itself. Configured handlers that do not expose a `name` attribute are skipped.

        Returns:
            overrides (dict[CapabilityName, CapabilityOverride]): Mapping from capability name to either a capability handler class or a factory callable that produces a capability instance.
        """
        overrides: dict[CapabilityName, CapabilityOverride] = {}
        for config in iter_capability_entries(cls.configured_capabilities):
            handler_cls = config.handler
            name = getattr(handler_cls, "name", None)
            if name is None:
                continue
            if config.options:
                overrides[name] = cls._make_capability_factory(
                    handler_cls,
                    dict(config.options),
                )
            else:
                overrides[name] = handler_cls
        return overrides

    @staticmethod
    def _make_capability_factory(
        handler_cls: type[Capability],
        options: dict[str, Any],
    ) -> CapabilityOverride:
        """
        Create a factory callable that produces instances of a capability handler using the given options.

        Parameters:
            handler_cls (type[Capability]): The Capability subclass to instantiate.
            options (dict[str, Any]): Keyword arguments to supply to the handler constructor.

        Returns:
            CapabilityOverride: A zero-argument callable that, when invoked, returns a new instance of `handler_cls` constructed with `options`.
        """

        def _factory(
            handler_cls: type[Capability] = handler_cls,
            options: dict[str, Any] = options,
        ) -> Capability:
            return handler_cls(**dict(options))

        return _factory

    @classmethod
    def _instantiate_configured_capability(
        cls, config: InterfaceCapabilityConfig
    ) -> Capability:
        """
        Instantiate a configured capability and verify it implements the Capability protocol.

        Parameters:
            config (InterfaceCapabilityConfig): Configuration entry whose `instantiate()` method produces a capability handler.

        Returns:
            Capability: The instantiated capability handler.

        Raises:
            TypeError: If the instantiated handler does not implement the expected Capability protocol (missing a `setup` method).
        """
        handler = config.instantiate()
        if not hasattr(handler, "setup"):
            message = (
                "Configured capability "
                f"{handler!r} does not implement the Capability protocol."
            )
            raise TypeError(message)
        return handler

    @classmethod
    def _bind_capability_handler(cls, handler: Capability) -> None:
        """
        Bind a capability handler to the interface class, replacing any existing handler with the same name.

        Binds the provided capability to the interface by tearing down a previously bound handler with the same name (if any), setting up the new handler, adding its name to the class capability set, and registering any startup hooks and system checks exposed by the handler.

        Parameters:
            handler (Capability): Capability instance to bind. Must expose a `name` attribute.

        Raises:
            AttributeError: If `handler` does not have a `name` attribute.
        """
        name = getattr(handler, "name", None)
        if name is None:
            message = (
                f"Capability instance {handler!r} does not expose a name attribute."
            )
            raise AttributeError(message)
        existing = cls._capability_handlers.get(name)
        if existing is handler:
            return
        if existing is not None:
            existing.teardown(cls)
        handler.setup(cls)
        cls._capabilities = frozenset({*cls._capabilities, name})
        cls._register_startup_hooks(handler)
        cls._register_system_checks(handler)

    @classmethod
    def _register_startup_hooks(cls, handler: Capability) -> None:
        """
        Register startup hooks provided by a capability handler onto the interface class.

        If the handler defines a callable `get_startup_hooks(cls)`, that is called with the interface class to obtain hooks. Otherwise, if the handler exposes a `startup_hooks` attribute, that value is used. Each callable hook found is registered via `register_startup_hook`. Non-callable hook entries and empty/None hook collections are ignored.

        Parameters:
            cls: The interface class to which startup hooks will be registered.
            handler: A capability handler that provides startup hooks via `get_startup_hooks` or `startup_hooks`.
        """
        hooks: Iterable[Callable[[], None]] | None = None
        get_hooks = getattr(handler, "get_startup_hooks", None)
        if callable(get_hooks):
            hooks = get_hooks(cls)
        elif hasattr(handler, "startup_hooks"):
            hooks = handler.startup_hooks
        if not hooks:
            return
        for hook in hooks:
            if callable(hook):
                register_startup_hook(cls, hook)

    @classmethod
    def _register_system_checks(cls, handler: Capability) -> None:
        """
        Register any system check callables exposed by a capability handler for the given interface class.

        The handler may expose checks via a callable `get_system_checks(cls)` or a `system_checks` iterable attribute.
        Each callable found is registered with register_system_check for the provided interface class; non-callable entries are ignored.

        Parameters:
            cls (type): The interface class to associate the system checks with.
            handler (Capability): Capability instance that may provide system checks.
        """
        hooks = None
        get_checks = getattr(handler, "get_system_checks", None)
        if callable(get_checks):
            hooks = get_checks(cls)
        elif hasattr(handler, "system_checks"):
            hooks = handler.system_checks
        if not hooks:
            return
        for hook in hooks:
            if callable(hook):
                register_system_check(cls, hook)

    def parse_input_fields_to_identification(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Convert positional and keyword inputs into a validated identification mapping for the interface's input fields.

        Parameters:
            *args: Positional arguments matched, in order, to the interface's defined input fields.
            **kwargs: Keyword arguments supplying input values by name.

        Returns:
            dict[str, Any]: Mapping of input field names to their validated values.

        Raises:
            UnexpectedInputArgumentsError: If extra keyword arguments are provided that do not match any input field (after allowing keys suffixed with "_id").
            MissingInputArgumentsError: If one or more required input fields are not provided.
            CircularInputDependencyError: If input fields declare dependencies that form a cycle and cannot be resolved.
            InvalidInputTypeError: If a provided value does not match the declared type for an input.
            InvalidPossibleValuesTypeError: If an input's `possible_values` configuration is neither callable nor iterable.
            InvalidInputValueError: If a provided value is not in the allowed set defined by an input's `possible_values`.
        """
        identification: dict[str, Any] = {}
        kwargs = cast(
            dict[str, Any], args_to_kwargs(args, self.input_fields.keys(), kwargs)
        )
        # Check for extra arguments
        extra_args = set(kwargs.keys()) - set(self.input_fields.keys())
        if extra_args:
            handled: set[str] = set()
            for extra_arg in list(extra_args):
                if extra_arg.endswith("_id"):
                    base = extra_arg[:-3]
                    if base in self.input_fields:
                        kwargs[base] = kwargs.pop(extra_arg)
                        handled.add(extra_arg)
            # recompute remaining unknown keys after handling known *_id aliases
            remaining = (extra_args - handled) | (
                set(kwargs.keys()) - set(self.input_fields.keys())
            )
            if remaining:
                raise UnexpectedInputArgumentsError(remaining)

        missing_args = set(self.input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise MissingInputArgumentsError(missing_args)

        # process input fields with dependencies
        processed: set[str] = set()
        while len(processed) < len(self.input_fields):
            progress_made = False
            for name, input_field in self.input_fields.items():
                if name in processed:
                    continue
                depends_on = input_field.depends_on
                if all(dep in processed for dep in depends_on):
                    value = self.input_fields[name].cast(kwargs[name])
                    self._process_input(name, value, identification)
                    identification[name] = value
                    processed.add(name)
                    progress_made = True
            if not progress_made:
                # detect circular dependencies
                unresolved = set(self.input_fields.keys()) - processed
                raise CircularInputDependencyError(unresolved)
        return identification

    @staticmethod
    def format_identification(identification: dict[str, Any]) -> dict[str, Any]:
        """
        Normalise identification data by replacing manager instances with their IDs.

        Parameters:
            identification (dict[str, Any]): Raw identification mapping possibly containing manager instances.

        Returns:
            dict[str, Any]: Identification mapping with nested managers replaced by their identifications.
        """
        from general_manager.manager.general_manager import GeneralManager

        for key, value in identification.items():
            if isinstance(value, GeneralManager):
                identification[key] = value.identification
            elif isinstance(value, (list, tuple)):
                identification[key] = []
                for v in value:
                    if isinstance(v, GeneralManager):
                        identification[key].append(v.identification)
                    elif isinstance(v, dict):
                        identification[key].append(
                            InterfaceBase.format_identification(v)
                        )
                    else:
                        identification[key].append(v)
            elif isinstance(value, dict):
                identification[key] = InterfaceBase.format_identification(value)
        return identification

    def _process_input(
        self, name: str, value: Any, identification: dict[str, Any]
    ) -> None:
        """
        Validate a single input value against its declared Input definition.

        Checks that the provided value matches the declared Python type and, when DEBUG is enabled, verifies the value is allowed by the input's `possible_values` (which may be an iterable or a callable that receives dependent input values).

        Parameters:
            name: The input field name being validated.
            value: The value to validate.
            identification: Partially resolved identification mapping used to supply dependent input values when evaluating `possible_values`.

        Raises:
            InvalidInputTypeError: If `value` is not an instance of the input's declared `type`.
            InvalidPossibleValuesTypeError: If `possible_values` is neither callable nor iterable.
            InvalidInputValueError: If `value` is not contained in the evaluated `possible_values` (only checked when DEBUG is true).
        """
        input_field = self.input_fields[name]
        if not isinstance(value, input_field.type):
            raise InvalidInputTypeError(name, type(value), input_field.type)
        if settings.DEBUG:
            # `possible_values` can be a callable or an iterable
            possible_values = input_field.possible_values
            if possible_values is not None:
                if callable(possible_values):
                    depends_on = input_field.depends_on
                    dep_values = {
                        dep_name: identification.get(dep_name)
                        for dep_name in depends_on
                    }
                    allowed_values = possible_values(**dep_values)
                elif isinstance(possible_values, Iterable):
                    allowed_values = possible_values
                else:
                    raise InvalidPossibleValuesTypeError(name)

                if value not in allowed_values:
                    raise InvalidInputValueError(name, value, allowed_values)

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """
        Create a new managed record in the underlying data store using the interface's inputs.

        Parameters:
            *args: Positional input values corresponding to the interface's defined input fields.
            **kwargs: Input values provided by name; unexpected extra keywords will be rejected.

        Returns:
            The created record or a manager-specific representation of the newly created entity.
        """
        observer = cls.get_capability_handler("observability")

        def _invoke() -> dict[str, Any]:
            """
            Invoke the configured "create" capability handler for this interface and return its result.

            Returns:
                dict[str, Any]: The payload returned by the create handler.

            Raises:
                NotImplementedError: If no create capability is available or the handler does not implement `create`.
            """
            handler = cls.require_capability("create")
            if hasattr(handler, "create"):
                create_handler = handler.create
                return create_handler(cls, *args, **kwargs)
            raise NotImplementedError(f"{cls.__name__} does not support create.")

        return cls._execute_with_observability(
            target=cls,
            operation="create",
            payload={"args": args, "kwargs": kwargs},
            func=_invoke,
            observer=observer,
        )

    def update(self, *args: Any, **kwargs: Any) -> Any:
        """
        Update the underlying managed record.

        Returns:
            The updated record or a manager-specific result.

        Raises:
            NotImplementedError: If this interface does not provide an update capability.
        """
        observer = self.get_capability_handler("observability")

        def _invoke() -> Any:
            """
            Invoke the update capability handler to perform an update operation.

            Returns:
                The result returned by the capability's `update` handler.

            Raises:
                NotImplementedError: If the interface does not provide an `update` capability.
            """
            handler = self._require_capability("update")
            if hasattr(handler, "update"):
                update_handler = handler.update
                return update_handler(self, *args, **kwargs)
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support update."
            )

        return self._execute_with_observability(
            target=self,
            operation="update",
            payload={"args": args, "kwargs": kwargs},
            func=_invoke,
            observer=observer,
        )

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        """
        Delete the underlying record managed by this interface.

        Delegates the deletion to the interface's configured delete capability and executes the operation with observability hooks.

        Returns:
            The result of the delete operation as returned by the delete capability.

        Raises:
            NotImplementedError: If the interface does not provide a delete capability.
        """
        observer = self.get_capability_handler("observability")

        def _invoke() -> Any:
            """
            Invoke the bound delete capability to remove the managed record.

            Returns:
                The result returned by the capability's `delete` handler.

            Raises:
                NotImplementedError: If the interface has no `delete` handler implemented.
            """
            handler = self._require_capability("delete")
            if hasattr(handler, "delete"):
                delete_handler = handler.delete
                return delete_handler(self, *args, **kwargs)
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support delete."
            )

        return self._execute_with_observability(
            target=self,
            operation="delete",
            payload={"args": args, "kwargs": kwargs},
            func=_invoke,
            observer=observer,
        )

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        """
        Provide a deprecated compatibility wrapper that issues a DeprecationWarning and performs the record deletion.

        Parameters:
            *args: Positional arguments forwarded to the underlying deletion implementation.
            **kwargs: Keyword arguments forwarded to the underlying deletion implementation.

        Returns:
            The result returned by the deletion operation.
        """
        warnings.warn(
            "deactivate() is deprecated; use delete() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.delete(*args, **kwargs)

    def get_data(self) -> Any:
        """
        Get the materialized data for this manager.

        Returns:
            The materialized data for this manager (implementation-defined).

        Raises:
            NotImplementedError: if reading is not supported for this manager.
        """
        observer = self.get_capability_handler("observability")

        def _invoke() -> Any:
            """
            Invoke the configured read capability to retrieve this manager's materialized data.

            Returns:
                The materialized data returned by the read capability.

            Raises:
                NotImplementedError: If this interface does not support read (no `get_data` on the read capability).
            """
            handler = self._require_capability("read")
            if hasattr(handler, "get_data"):
                read_handler = handler.get_data
                return read_handler(self)
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support read."
            )

        return self._execute_with_observability(
            target=self,
            operation="read",
            payload={"identification": getattr(self, "identification", None)},
            func=_invoke,
            observer=observer,
        )

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        """
        Retrieve metadata describing each attribute exposed by the manager.

        Returns:
            dict[str, AttributeTypedDict]: Mapping from attribute name to its metadata (keys include `type`, `default`, `is_required`, `is_editable`, and `is_derived`).

        Raises:
            NotImplementedError: If the manager does not provide a read capability implementing `get_attribute_types`.
        """
        handler = cls.get_capability_handler("read")
        if handler is not None and hasattr(handler, "get_attribute_types"):
            return handler.get_attribute_types(cls)  # type: ignore[return-value]
        raise NotImplementedError(
            f"{cls.__name__} must provide a read capability implementing get_attribute_types."
        )

    @classmethod
    def get_attributes(cls) -> dict[str, Any]:
        """
        Retrieve attribute values exposed by the interface.

        Returns:
            dict[str, Any]: Mapping of attribute names to their current values.

        Raises:
            NotImplementedError: If the interface does not provide a read capability implementing `get_attributes`.
        """
        handler = cls.get_capability_handler("read")
        if handler is not None and hasattr(handler, "get_attributes"):
            return handler.get_attributes(cls)  # type: ignore[return-value]
        raise NotImplementedError(
            f"{cls.__name__} must provide a read capability implementing get_attributes."
        )

    @classmethod
    def get_graph_ql_properties(cls) -> dict[str, GraphQLProperty]:
        """
        Collect GraphQLProperty descriptors declared on the interface's parent manager class.

        Returns:
            dict[str, GraphQLProperty]: Mapping from attribute name to the corresponding GraphQLProperty instance found on the parent manager class. Returns an empty dict if no parent class is set or none of its attributes are GraphQLProperty instances.
        """
        if not hasattr(cls, "_parent_class"):
            return {}
        return {
            name: prop
            for name, prop in vars(cls._parent_class).items()
            if isinstance(prop, GraphQLProperty)
        }

    @classmethod
    def filter(cls, **kwargs: Any) -> Bucket[Any]:
        """
        Filter records using the provided lookup expressions and return a Bucket of matches.

        Parameters:
            **kwargs: Lookup expressions mapping field lookups (e.g., "name__icontains") to values.

        Returns:
            Bucket[Any]: Bucket containing records that match the lookup expressions.

        Raises:
            NotImplementedError: If the interface's query capability does not implement filtering.
        """
        handler = cls.require_capability("query")
        if hasattr(handler, "filter"):
            return handler.filter(cls, **kwargs)
        raise NotImplementedError

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Any]:
        """
        Obtain a Bucket of records excluding those that match the given lookup expressions.

        Parameters:
            **kwargs: Lookup expressions accepted by the query capability (e.g., field=value, field__lookup=value).

        Returns:
            Bucket[Any]: A Bucket containing records that do not match the provided lookup expressions.

        Raises:
            NotImplementedError: If the interface's query capability does not implement an `exclude` operation.
        """
        handler = cls.require_capability("query")
        if hasattr(handler, "exclude"):
            return handler.exclude(cls, **kwargs)
        raise NotImplementedError

    @classmethod
    def all(cls) -> Bucket[Any]:
        """
        Retrieve a Bucket containing all records for this interface.

        Returns:
            Bucket[Any]: A Bucket containing every record accessible via this interface.

        Raises:
            NotImplementedError: If the configured query capability does not implement `all`.
        """
        handler = cls.require_capability("query")
        if hasattr(handler, "all"):
            return handler.all(cls)
        raise NotImplementedError

    @staticmethod
    def _execute_with_observability(
        *,
        target: object,
        operation: str,
        payload: dict[str, Any],
        func: Callable[[], Any],
        observer: "Capability | None",
    ) -> Any:
        """
        Execute a callable while invoking optional observer lifecycle hooks before, after, and on error.

        Parameters:
            target (object): The subject of the operation (passed to observer hooks).
            operation (str): A short name of the operation (passed to observer hooks).
            payload (dict[str, Any]): Contextual data about the operation (passed to observer hooks).
            func (Callable[[], Any]): The callable to execute.
            observer (Capability | None): Optional capability providing `before_operation`, `after_operation`, and/or `on_error` hooks.

        Returns:
            Any: The value returned by `func`.

        Notes:
            If `func` raises an exception, the exception is propagated after calling `observer.on_error(...)` if available.
        """
        if observer is not None and hasattr(observer, "before_operation"):
            observer.before_operation(
                operation=operation,
                target=target,
                payload=payload,
            )
        try:
            result = func()
        except Exception as error:
            if observer is not None and hasattr(observer, "on_error"):
                observer.on_error(
                    operation=operation,
                    target=target,
                    payload=payload,
                    error=error,
                )
            raise
        if observer is not None and hasattr(observer, "after_operation"):
            observer.after_operation(
                operation=operation,
                target=target,
                payload=payload,
                result=result,
            )
        return result

    @staticmethod
    def _invoke_lifecycle_callable(
        lifecycle_callable: Callable[..., Any],
        **kwargs: Any,
    ) -> Any:
        """
        Invoke a lifecycle callable using only the keyword arguments that match its signature.

        Parameters:
            lifecycle_callable (Callable[..., Any]): The callable to invoke.
            **kwargs: Candidate keyword arguments; only those with names present in the callable's parameter list will be passed.

        Returns:
            Any: The value returned by calling `lifecycle_callable` with the filtered arguments.
        """
        signature = inspect.signature(lifecycle_callable)
        allowed = {
            name: kwargs[name] for name in signature.parameters.keys() if name in kwargs
        }
        return lifecycle_callable(**allowed)

    @staticmethod
    def _default_base_model_class() -> type["GeneralManagerBasisModel"]:
        """
        Return the default base model class used by GeneralManager implementations.

        Returns:
            GeneralManagerBasisModel: The concrete model class used as the default base for managers.
        """
        from general_manager.interface.utils.models import GeneralManagerBasisModel

        return GeneralManagerBasisModel

    @classmethod
    def handle_interface(
        cls,
    ) -> tuple[
        classPreCreationMethod,
        classPostCreationMethod,
    ]:
        """
        Provide pre- and post-creation hooks for GeneralManager class construction derived from the interface's lifecycle capability.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]:
                - pre-create callable accepting (name, attrs, interface, base_model_class=None) and returning (attrs, interface_class, related_class).
                - post-create callable accepting (new_class, interface_class, model) and returning None.

        Raises:
            NotImplementedError: If no lifecycle capability is declared and the class does not override handle_interface.
        """
        lifecycle = cls._lifecycle_capability()
        if lifecycle is not None:
            pre = getattr(lifecycle, "pre_create", None)
            post = getattr(lifecycle, "post_create", None)
            if callable(pre) and callable(post):

                def pre_wrapper(
                    name: generalManagerClassName,
                    attrs: attributes,
                    interface: interfaceBaseClass,
                    base_model_class: type["GeneralManagerBasisModel"] | None = None,
                ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
                    """
                    Wraps and invoke the lifecycle pre-creation hook for a GeneralManager class.

                    Calls the configured pre-create lifecycle callable with the provided name, attrs, interface, and a base_model_class (uses the interface's default base model class when None) and returns the possibly-modified creation trio.

                    Parameters:
                        name (str): Proposed class name for the GeneralManager.
                        attrs (dict[str, Any]): Attribute dictionary for the class being created.
                        interface (Type[InterfaceBase]): Interface base class passed to the lifecycle hook.
                        base_model_class (type[GeneralManagerBasisModel] | None): Base model class to supply to the lifecycle hook; if None, the interface's default is used.

                    Returns:
                        tuple[dict[str, Any], Type[InterfaceBase], Type[Model] | None]: A tuple of (attributes, interface class, related model class) as returned or transformed by the lifecycle pre-create callable.
                    """
                    if base_model_class is None:
                        base_model_class = cls._default_base_model_class()
                    return cls._invoke_lifecycle_callable(
                        pre,
                        name=name,
                        attrs=attrs,
                        interface=interface,
                        base_model_class=base_model_class,
                    )

                def post_wrapper(
                    new_class: newlyCreatedGeneralManagerClass,
                    interface_class: newlyCreatedInterfaceClass,
                    model: relatedClass,
                ) -> None:
                    """
                    Invoke the post-creation lifecycle callable for a newly created GeneralManager class.

                    Parameters:
                        new_class (Type[GeneralManager]): The newly created GeneralManager subclass.
                        interface_class (Type[InterfaceBase]): The interface class used to create the manager.
                        model (Type[Model] | None): The related Django model class, or None if not applicable.
                    """
                    cls._invoke_lifecycle_callable(
                        post,
                        new_class=new_class,
                        interface_class=interface_class,
                        model=model,
                    )

                return pre_wrapper, post_wrapper

        raise NotImplementedError(
            f"{cls.__name__} must override handle_interface or declare a lifecycle capability."
        )

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Resolve the declared Python type for the named input field.

        Parameters:
            field_name (str): Name of the input field to look up.

        Returns:
            type: The Python type declared for the specified field.

        Raises:
            KeyError: If no input field with the given name is defined.
        """
        handler = cls.get_capability_handler("read")
        if handler is not None and hasattr(handler, "get_field_type"):
            return handler.get_field_type(cls, field_name)  # type: ignore[return-value]
        field = cls.input_fields.get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field.type
