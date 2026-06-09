# Workflow API

::: general_manager.workflow.events.WorkflowEvent

::: general_manager.workflow.events.manager_created_event

::: general_manager.workflow.events.manager_updated_event

::: general_manager.workflow.events.manager_deleted_event

::: general_manager.workflow.event_registry.EventRegistry

::: general_manager.workflow.event_registry.InMemoryEventRegistry

::: general_manager.workflow.event_registry.DatabaseEventRegistry

::: general_manager.workflow.event_registry.configure_event_registry

::: general_manager.workflow.event_registry.configure_event_registry_from_settings

::: general_manager.workflow.event_registry.get_event_registry

::: general_manager.workflow.engine.WorkflowDefinition

::: general_manager.workflow.engine.WorkflowExecution

::: general_manager.workflow.engine.WorkflowEngine

::: general_manager.workflow.engine.WorkflowEngineError

::: general_manager.workflow.engine.WorkflowExecutionNotFoundError

::: general_manager.workflow.engine.WorkflowCancelledError

::: general_manager.workflow.engine.WorkflowInvalidStateError

::: general_manager.workflow.actions.Action

::: general_manager.workflow.actions.ActionRegistry

::: general_manager.workflow.backend_registry.configure_workflow_engine

::: general_manager.workflow.backend_registry.configure_workflow_engine_from_settings

::: general_manager.workflow.backend_registry.get_workflow_engine

::: general_manager.workflow.backends.local.LocalWorkflowEngine

::: general_manager.workflow.backends.celery.CeleryWorkflowEngine

::: general_manager.workflow.backends.n8n.N8nWorkflowEngine

::: general_manager.workflow.tasks.publish_outbox_batch

::: general_manager.workflow.tasks.route_outbox_event

::: general_manager.workflow.tasks.route_outbox_claims_batch

::: general_manager.workflow.tasks.execute_workflow_handler

::: general_manager.workflow.tasks.resume_execution_task

::: general_manager.workflow.tasks.cancel_execution_task
