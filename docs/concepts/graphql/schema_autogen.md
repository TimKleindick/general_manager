# Schema Auto-Generation

`general_manager.api.graphql.GraphQL` inspects manager interfaces and creates matching Graphene types and mutations.

## Type mapping

For each manager class, GeneralManager:

1. Registers a Graphene `ObjectType` with fields derived from interface attribute types.
2. Creates resolvers that read values from the underlying manager or bucket.
3. Adds fields for every `@graph_ql_property` method. Union types and optional hints are converted into GraphQL-friendly types.
4. Registers measurement scalars (`MeasurementScalar`) and object wrappers so units stay intact.

## Mutations

Create, update, and delete mutations are added automatically when the interface overrides the base method. Each mutation returns:

- `success`: boolean indicating whether the operation completed.
- `errors`: list of validation or permission errors when present.
- A field with the manager name containing the affected object.

Custom mutations use the `@graph_ql_mutation` decorator from `general_manager.api.mutation`. The decorator analyses the function signature to generate GraphQL input arguments and return types.

### Relation input contract

Automatic GraphQL mutations accept relation inputs in the GraphQL-facing forms below and normalize them to the ORM mutation contract before persistence:

- Single-valued relations: `<field>` or `<field>Id`
- Many-valued relations: `<field>List` or `<field>IdList`

Internally, GeneralManager treats the canonical mutation payload as:

- Single-valued relations: `<field>_id`
- Many-valued relations: `<field>_id_list`

These public names assume Graphene's default `auto_camelcase=True`. If your schema disables auto-camelcase, the Python-side argument names remain available as `<field>`, `<field>_id`, `<field>_list`, and `<field>_id_list`.

This keeps GraphQL mutations compatible with Graphene field naming while preserving a predictable backend contract for ORM-backed interfaces.

### Schema-generation expectations

Schema generation should remain resilient when interface metadata includes edge-case field types:

- Measurement fields continue to map to `MeasurementScalar` / `MeasurementType`
- Large integer ORM fields may opt into `BigIntScalar` through `graphql_scalar="bigint"`
- Non-relational field types that do not map cleanly to a specific GraphQL scalar fall back to string-like handling instead of aborting schema construction

The intended behavior is that startup and schema registration remain reviewable and predictable even when a manager exposes less common field metadata.

## Buckets and pagination

For bucket-returning fields, the schema registers list fields and page types. `PageInfo` exposes `total_count`, `current_page`, `total_pages`, and optional `page_size` so clients can implement cursor-less pagination quickly.

## Extending the schema

- Override `_map_field_to_graphene_read` to customise how specific Python types map to GraphQL fields (for example, using Relay nodes).
- Register additional scalars or enums by updating `GraphQL.graphql_type_registry` before building the schema.
- Combine auto-generated queries with handcrafted ones by subclassing the generated query root and adding custom fields.
- Register additional schema directives with `GENERAL_MANAGER["GRAPHQL_DIRECTIVES"]`:

```python
from graphql import DirectiveLocation, GraphQLDirective

GENERAL_MANAGER = {
    "GRAPHQL_DIRECTIVES": [
        GraphQLDirective(
            name="scenario",
            locations=[DirectiveLocation.FIELD],
        )
    ]
}
```

This setting only adds directives to the generated schema. If a directive needs runtime behavior, implement that separately with Graphene middleware or a custom execution context.
