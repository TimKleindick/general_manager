# Interface API

::: general_manager.interface.base_interface.InterfaceBase

::: general_manager.interface.orm_interface.OrmInterfaceBase

::: general_manager.interface.interfaces.database.DatabaseInterface

::: general_manager.interface.interfaces.existing_model.ExistingModelInterface

::: general_manager.interface.interfaces.read_only.ReadOnlyInterface

::: general_manager.interface.interfaces.calculation.CalculationInterface

## Capabilities

::: general_manager.interface.capabilities.orm

::: general_manager.interface.capabilities.read_only.management

::: general_manager.interface.capabilities.calculation.lifecycle

::: general_manager.interface.capabilities.existing_model.resolution

## Request Interfaces

::: general_manager.interface.requests.RequestField

::: general_manager.interface.requests.RequestFilter

::: general_manager.interface.requests.RequestOperation

`RequestQueryOperation` is the public query-operation alias for `RequestOperation`.

::: general_manager.interface.requests.RequestMutationOperation

::: general_manager.interface.requests.RequestPlan

`RequestQueryPlan` is the public query-plan alias for `RequestPlan`.

::: general_manager.interface.requests.RequestQueryResult

::: general_manager.interface.requests.SharedRequestTransport

::: general_manager.interface.requests.UrllibRequestTransport

::: general_manager.interface.requests.RequestTransportConfig

::: general_manager.interface.requests.RequestRetryPolicy

::: general_manager.interface.requests.RequestTransportRequest

::: general_manager.interface.requests.RequestTransportResponse

::: general_manager.interface.requests.RequestAuthProvider

::: general_manager.interface.requests.BearerTokenAuthProvider

::: general_manager.interface.requests.HeaderApiKeyAuthProvider

::: general_manager.interface.requests.QueryApiKeyAuthProvider

::: general_manager.interface.requests.BasicAuthProvider

::: general_manager.interface.requests.FieldMappingSerializer

::: general_manager.interface.requests.RequestMetricsBackend

::: general_manager.interface.requests.NoopRequestMetricsBackend

::: general_manager.interface.requests.RequestTraceBackend

::: general_manager.interface.requests.NoopRequestTraceBackend

## Request Errors

::: general_manager.interface.requests.RequestRemoteError

::: general_manager.interface.requests.RequestTransportError

::: general_manager.interface.requests.RequestTransportStatusError

::: general_manager.interface.requests.RequestAuthenticationError

::: general_manager.interface.requests.RequestAuthorizationError

::: general_manager.interface.requests.RequestNotFoundError

::: general_manager.interface.requests.RequestConflictError

::: general_manager.interface.requests.RequestRateLimitedError

::: general_manager.interface.requests.RequestSchemaError

::: general_manager.interface.requests.RequestServerError

## Interface Infrastructure

::: general_manager.interface.infrastructure.startup_hooks.register_startup_hook

::: general_manager.interface.infrastructure.startup_hooks.iter_interface_startup_hooks

::: general_manager.interface.infrastructure.startup_hooks.registered_startup_hooks

::: general_manager.interface.infrastructure.startup_hooks.registered_startup_hook_entries

::: general_manager.interface.infrastructure.startup_hooks.order_interfaces_by_dependency

::: general_manager.interface.infrastructure.system_checks.register_system_check

::: general_manager.interface.infrastructure.system_checks.iter_interface_system_checks

::: general_manager.interface.infrastructure.system_checks.registered_system_checks
