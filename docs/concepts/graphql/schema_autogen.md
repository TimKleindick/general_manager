# Schema Auto-Generation

`general_manager.api.graphql.GraphQL` inspects manager interfaces and creates matching Graphene types and mutations.

## Type mapping

For each manager class, GeneralManager:

1. Registers a Graphene `ObjectType` with fields derived from interface attribute types.
2. Creates resolvers that read values from the underlying manager or bucket.
3. Adds fields for every `@graphQlProperty` method. Union types and optional hints are converted into GraphQL-friendly types.
4. Registers measurement scalars (`MeasurementScalar`) and object wrappers so units stay intact.

## Mutations

Create, update, and delete mutations are added automatically when the interface overrides the base method. Each mutation returns:

- `success`: boolean indicating whether the operation completed.
- `errors`: list of validation or permission errors when present.
- A field with the manager name containing the affected object.

Custom mutations use the `@graphQlMutation` decorator from `general_manager.api.mutation`. The decorator analyses the function signature to generate GraphQL input arguments and return types.

## Buckets and pagination

For bucket-returning fields, the schema registers list fields and page types. `PageInfo` exposes `total_count`, `current_page`, `total_pages`, and optional `page_size` so clients can implement cursor-less pagination quickly.

## Extending the schema

- Override `_mapFieldToGrapheneRead` to customise how specific Python types map to GraphQL fields (for example, using Relay nodes).
- Register additional scalars or enums by updating `GraphQL.graphql_type_registry` before building the schema.
- Combine auto-generated queries with handcrafted ones by subclassing the generated query root and adding custom fields.
