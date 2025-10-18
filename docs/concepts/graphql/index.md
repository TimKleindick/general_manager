# GraphQL Integration

GeneralManager can auto-generate a Graphene schema that exposes your managers, buckets, and mutations. The integration covers:

- Type generation based on manager attributes and GraphQL properties.
- CRUD mutations with consistent success/error payloads.
- Measurements, buckets, and pagination helpers.
- Permission enforcement based on the caller's user account.

Use the following guides to customise the schema:

- [Schema auto-generation](schema_autogen.md)
- [Filtering and pagination](filters_pagination.md)
- [Security considerations](security.md)
- [Subscriptions and dependency tracking](subscriptions.md)
