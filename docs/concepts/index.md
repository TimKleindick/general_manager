# Concepts Overview

Concept guides explain the model behind the API. Start with three objects:
a **manager** represents one domain entity or calculation, an **interface**
supplies its data and operations, and a **bucket** represents a collection of
managers. A permission policy controls access at the boundaries that enforce it.

## Understand the core

1. [Architecture](architecture.md): follow one read and one write through the layers.
2. [Managers and buckets](models_entities.md): identity, filtering, relations, and grouping.
3. [Interfaces](interfaces/index.md): choose where data lives or how it is calculated.
4. [Permissions](permission/index.md): understand Python writes, explicit Python read checks, and generated GraphQL enforcement.
5. [GraphQL](graphql/index.md): understand how manager declarations become a schema.

Try the [quickstart](../quickstart.md) alongside these pages. For a task-oriented
reading order, use the [learning paths](../learning_paths.md).

## Add a concept when you need it

| Need | Concept |
| --- | --- |
| Units, currencies, and conversions | [Measurements](measurement/index.md) |
| Reject invalid domain values | [Rules and validation](rules_validation.md) |
| Reuse calculations without stale results | [Caching](caching.md) |
| Read a consistent past state | [Historical execution](historical_context.md) |
| Create fixtures or demo data | [Factories](factories/index.md) and [seeding](seeding.md) |
| Search domain objects | [Search](search.md) |
| React to events and run durable work | [Workflows](workflow.md) |
| Inspect runtime behavior | [Observability](observability/index.md) |
| Let a model query exposed domain data | [Chat architecture](chat.md) |

The chat guides also cover [schema discovery](chat_schema.md),
[evidence and calculations](chat_evidence.md), [business definitions](chat_prompting.md),
[reference-data transport](chat_reference_data.md), and [evaluation](chat_evaluation.md).
[Public utilities](utils.md) describes reusable helpers when you need their
specific contracts.
