# Concepts Overview

The concept guides explain the building blocks that power GeneralManager. They provide the mental model you need before diving into tutorials or API references. Each section focuses on a specific layer of the framework:

- [Core architecture](architecture.md) covers managers, buckets, dependency tracking, and the runtime lifecycle.
- [Domain modelling](models_entities.md) explores how to shape managers, buckets, and grouping utilities.
- [Caching](caching.md), [historical execution](historical_context.md), and [rule validation](rules_validation.md) discuss cross-cutting features that keep data fresh, consistent, and trustworthy.
- [Workflow](workflow.md) explains event routing, workflow engines, outbox delivery, and operational state.
- [Public utilities](utils.md) describes exported helper functions for naming, filtering, JSON encoding, cache keys, and path mapping.
- [Measurement handling](measurement/index.md), [permissions](permission/index.md), [interfaces](interfaces/index.md), [GraphQL](graphql/index.md), and [factories & testing](factories/index.md) provide deep dives into their respective subsystems.
- [Seeding](seeding.md) explains dependency-aware manager landscape generation for demos, tests, and local development.

Use this section as a map; each page links to tutorials and API references relevant to the topic.
