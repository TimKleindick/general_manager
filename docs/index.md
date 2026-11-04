# Welcome to GeneralManager

GeneralManager adds a domain layer to Django: define a manager once, use it in
Python, and expose it through a generated GraphQL API. Interfaces decide where
the data comes from; managers give application code a consistent way to work
with it.

## Start with one working application

Follow the [quickstart](quickstart.md) to create a `Project` manager, save an
Apollo project, and retrieve it through GraphQL. Then continue with
[authenticated writes](howto/create_project.md) and
[your first permission policy](howto/add_permission_rule.md).

You do not need to understand capability manifests, cache internals, or custom
interfaces to complete this path. The [learning paths](learning_paths.md) show
what to read next for your task.

## Find the right kind of explanation

| I want to… | Start here |
| --- | --- |
| Understand managers, interfaces, and buckets | [Concepts](concepts/index.md) |
| Build or change part of an application | [Tutorials and task guides](howto/index.md) |
| Adapt a focused example | [Cookbook](examples/index.md) |
| Check an exact signature, input, or result | [API reference](api/core.md) |
| Install or upgrade a deployment | [Installation](installation.md) and [upgrade guide](howto/migrate-to-stable-api.md) |
| Diagnose a problem | [FAQ](faq.md) and [operations map](howto/index.md#operate-and-maintain-an-application) |

## Add capabilities when your application needs them

- Store unit-aware values with [measurements and currencies](concepts/measurement/index.md).
- Combine database, calculated, remote, and spreadsheet data through [interfaces](concepts/interfaces/index.md).
- Expose queries and mutations with [GraphQL](concepts/graphql/index.md).
- Build [search](howto/search.md), [workflows](concepts/workflow.md), or [file uploads](howto/graphql_file_uploads.md).
- Add [LLM chat](howto/install_llm_chat.md) over explicitly exposed managers.

For questions or documentation gaps, open an issue on the
[GitHub repository](https://github.com/TimKleindick/general_manager).
