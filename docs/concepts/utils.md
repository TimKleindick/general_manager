# Public Utilities

GeneralManager exposes a small set of utility helpers for projects that need the same parsing, formatting, JSON encoding, or cache-key behavior used internally by the package.

## Naming helpers

Use `snake_to_pascal`, `snake_to_camel`, `pascal_to_snake`, and `camel_to_snake` when project code needs to match GeneralManager's GraphQL and Python naming conventions.

## Filter helpers

`parse_filters` parses simple filter expressions into structured criteria used by filtering helpers, including `filter_kwargs` for `GeneralManager`-typed fields. `create_filter_function` builds callable predicates for in-memory and filter-helper use where appropriate. Prefer manager `filter()` and `exclude()` APIs for normal application queries.

## Serialization and cache keys

`CustomJSONEncoder` serializes package-specific values such as measurements for JSON payloads. `make_cache_key` builds deterministic cache keys for values that need to align with GeneralManager cache behavior.

## Path mapping and small transforms

`PathMap` supports nested path mapping where integrations need stable source-to-target field paths. `args_to_kwargs` and `none_to_zero` are small compatibility helpers kept public for callers that need package-consistent behavior.

See the [Utilities API reference](../api/utils.md) for signatures.
