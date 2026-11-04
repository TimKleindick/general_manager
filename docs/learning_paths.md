# Learning Paths

Choose a path by the result you need. The tutorial pages show what to do;
concept pages explain why it works; API references define the exact contract.
The optional integrations are independent paths, not prerequisites for the core.

## Build your first application

1. [Quickstart](quickstart.md): define one manager and retrieve a saved row through GraphQL.
2. [Write application data](howto/create_project.md): create and update a row as an ordinary user.
3. [Understand the architecture](concepts/architecture.md): connect a manager, its interface, and a bucket to those operations.
4. [Define permissions](howto/add_permission_rule.md): allow an editor's update and reject a reader's update.
5. [Expose data over GraphQL](howto/expose_via_graphql.md): select fields, filter lists, and call mutations.
6. [Test application behavior](howto/testing_guides.md): decide which writes, permission boundaries, and response shapes your tests should protect.

The quickstart deliberately allows public reads. The permission tutorial changes
that policy. When combining examples, use the current manager definition and
an appropriate authenticated user; a shell read and a GraphQL read have
[different authorization boundaries](concepts/permission/index.md).

## Model and calculate domain data

Start with [managers and buckets](concepts/models_entities.md), then
[choose an interface](concepts/interfaces/index.md). Use the
[measurement tutorial](howto/add_model_with_measurement.md) when values have
units. For values derived from other managers, follow the finite input example
in [calculation interfaces](concepts/interfaces/computed_data_interfaces.md).
Use the [volume-curve recipe](examples/project_volume_curve.md) when you are
ready to aggregate related records. Add [caching](concepts/caching.md) after the
calculation is correct and you know which reuse scope it needs.

## Extend an existing Django application

Read [ExistingModelInterface](concepts/interfaces/existing_model_interface.md)
before adding a second model for data you already own. The
[legacy-model recipes](examples/existing_model_interface.md) show validation and
database routing. Review [migrations](howto/migrations_gotchas.md) and
[atomic writes](howto/orm_atomic_writes.md) before deploying changes.

## Connect external data

| Source | Explanation | Task guide | Complete example |
| --- | --- | --- | --- |
| HTTP-style service | [Request interfaces](concepts/interfaces/request_interface.md) | [Configure a request interface](howto/request_interface.md) | [Request recipe](examples/request_interface_end_to_end.md) |
| Another GeneralManager service | [Remote API](concepts/interfaces/remote_api.md) | [Expose and consume a service](howto/remote_manager_interface.md) | [Remote manager recipe](examples/remote_manager_interface_end_to_end.md) |
| Excel workbook | [Excel interfaces](concepts/interfaces/excel_interface.md) | [Read and write a workbook](howto/excel_interface.md) | [Excel recipe](examples/excel_interface_end_to_end.md) |

Start with a supplied interface. The [custom interface](howto/write_custom_interface.md)
and [custom capability](howto/create_custom_capability.md) guides are for backend
implementers who need operations the supplied interfaces do not provide.

## Operate an application

Use the [operations map](howto/index.md#operate-and-maintain-an-application) to
find deployment, migration, search reconciliation, workflow delivery, upload
cleanup, and observability instructions. Each integration owns its configuration;
follow that integration's guide when enabling it.
