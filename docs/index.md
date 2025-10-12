# Welcome to GeneralManager

GeneralManager is a modular data management framework for Django projects. It combines a declarative model layer, automatic GraphQL integration, and measurement-aware fields to help you expose consistent domain objects to multiple clients.

The documentation is organised around the developer workflows you follow when building with GeneralManager:

- Understand the [core concepts](concepts/index.md) such as managers, buckets, interfaces, and permissions.
- Follow the [Quickstart](quickstart.md) to bootstrap a new project or integrate the package into an existing Django app.
- Dive into advanced topics like [GraphQL auto-generation](concepts/graphql/index.md), [attribute-based access control](concepts/permission/index.md), and [unit-aware calculations](concepts/measurement/index.md).
- Explore hands-on [tutorials](howto/index.md) and a curated [cookbook](examples/index.md) with real-world snippets.
- Reference the [API documentation](api/core.md) generated directly from the source code when you need precise signatures.

## Key capabilities

- **Declarative managers**: describe domain entities with Python type hints and let interfaces handle persistence, caching, and identification.
- **Multiple interfaces**: combine database-backed, read-only, and computed managers without changing your business logic.
- **Attribute-based permissions**: secure operations through reusable permission rules and contextual checks.
- **GraphQL integration**: expose managers, relationships, and custom mutations automatically, with fine-grained security controls.
- **Measurement support**: work with physical units and currencies using intuitive arithmetic and Django model fields.

## Getting help

If you are new, start with the [Quickstart guide](quickstart.md). For troubleshooting, consult the [FAQ](faq.md) or open an issue on the [GitHub repository](https://github.com/TimKleindick/general_manager).

The documentation is continuously evolving. Contributions that clarify explanations, add examples, or cover missing topics are welcome.
