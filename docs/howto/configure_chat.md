# Configure chat providers, roles and domain context

Complete [installation](install_llm_chat.md) first. Keep settings in the server's
Django configuration; clients cannot select profiles, trust groups or credentials.
Use the [API reference](../api/chat.md#settings) for the complete settings table.
Examples here use environment-selected models so the application can evaluate
and deploy a model available in its own environment.

## Use one provider for all roles

A normal configuration can omit explicit profiles. The provider/configuration
becomes the implicit `default` profile for planner, executor, synthesizer and
fallback:

```python
import os

GENERAL_MANAGER = {
    "CHAT": {
        "enabled": True,
        "provider": "general_manager.chat.providers.OllamaProvider",
        "provider_config": {
            "model": os.environ["CHAT_MODEL"],
            "base_url": "http://127.0.0.1:11434",
            "timeout_seconds": 60,
        },
        "permission": "myproject.chat_permissions.can_use_chat",
        "planned": {
            "max_concurrent_tasks": 3,
            "evidence_timeout_seconds": 90,
            "synthesis_timeout_seconds": 30,
        },
    },
}
```

Provider request timeouts and stage deadlines serve different purposes: a request
also has to fit within the remaining stage time. A configured request timeout
does not extend a stage. The evidence default includes planning.

## Assign explicit role profiles

Use named profiles when the roles need distinct configurations. Every explicit
profile needs `provider`, `provider_config` and `trust_group`. Map all four roles;
all mapped profiles must share one trust group:

```python
GENERAL_MANAGER["CHAT"].update({
    "provider_profiles": {
        "local": {
            "provider": "general_manager.chat.providers.OllamaProvider",
            "provider_config": {"model": os.environ["CHAT_MODEL"]},
            "trust_group": "application-local",
        },
    },
    "planned": {
        "roles": {
            "planner": "local",
            "executor": "local",
            "synthesizer": "local",
            "fallback": "local",
        },
        "max_concurrent_tasks": 3,
        "evidence_timeout_seconds": 90,
        "synthesis_timeout_seconds": 30,
    },
})
```

The same trust-group label is an application declaration, not a credential or a
runtime proof of where a remote service stores data. Choose provider destinations
accordingly. Custom explicit profile providers implement configured construction
through `from_config`; see [the provider protocol](../api/chat.md#provider-protocol).
Do not keep obsolete complexity-based executor roles or recovery-role names.

## Add manager discovery metadata

A catalog can be a mapping, callable or dotted callable path:

```python
# myproject/chat_catalog.py

def catalog():
    return {
        "PartManager": {
            "domain": "manufacturing",
            "aliases": ["part", "component"],
            "use_when": "The question concerns designed components.",
            "distinguish_from": ["MaterialManager"],
        },
    }
```

```python
GENERAL_MANAGER["CHAT"]["planned"]["catalog"] = "myproject.chat_catalog.catalog"
```

Replace manager names with exact registered names. Catalog entries for hidden or
absent managers fail validation; metadata does not expose them. Keep aliases and
descriptions useful for distinguishing similar entities rather than repeating
the entire schema.

## Supply application meanings

Use `system_prompt` for actual business conventions and scope; see
[definitions and choices](../concepts/chat_prompting.md). For example, document
which price is net, whether returns reduce actual quantity and which calendar
applies. Do not use a prompt to supply invented record values or bypass native
permission/confirmation checks. Custom planners must support the contextual
envelope when definitions or previous runtime questions require it.

## Set operational limits

```python
GENERAL_MANAGER["CHAT"].update({
    "max_results": 200,
    "query_timeout_seconds": 5,
    "rate_limit": {
        "requests": 60,
        "tokens": 100_000,
        "window_seconds": 3600,
    },
    "ttl_hours": 24,
    "allowed_mutations": [],
    "confirm_mutations": [],
})
```

The query timeout above is an explicit example, not a default or a portable
backend cancellation guarantee. Use a shared Django cache for cross-process
rate limits. `max_retries_per_message`, `max_mutations_per_message` and
`max_total_rounds_per_message` describe the retained mutation/legacy-provider
workflow; they are not a successful-round cap for planned reads. Installed eval
request admission has a separate cap.

Run `python manage.py check` and evaluate representative reads and permitted
writes in a non-production environment before rollout. For rollback, disable
chat globally or deploy the previous release. `planned.enabled=False` does not
restore a previous read loop.
