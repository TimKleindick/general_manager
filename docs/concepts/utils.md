# Public Utilities

GeneralManager exposes a small set of utility helpers for projects that need the same parsing, formatting, JSON encoding, or cache-key behavior used internally by the package.

## Naming helpers

Use `snake_to_pascal`, `snake_to_camel`, `pascal_to_snake`, and `camel_to_snake` when project code needs to match GeneralManager's GraphQL and Python naming conventions.

## Filter helpers

`parse_filters` and `create_filter_function` parse simple filter expressions into callable predicates. They are useful for tests, in-memory filtering, and adapter code. Prefer manager `filter()` and `exclude()` APIs for normal application queries.

## Serialization and cache keys

`CustomJSONEncoder` serializes package-specific values such as measurements for JSON payloads. `make_cache_key` builds deterministic cache keys for values that need to align with GeneralManager cache behavior.

## Path mapping and small transforms

`PathMap` supports nested path mapping where integrations need stable source-to-target field paths. `args_to_kwargs` and `none_to_zero` are small compatibility helpers kept public for callers that need package-consistent behavior.

See the [Utilities API reference](../api/utils.md) for signatures.
