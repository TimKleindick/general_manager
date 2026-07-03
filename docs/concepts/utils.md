# Public Utilities

GeneralManager exposes a small set of utility helpers for projects that need the same parsing, formatting, JSON encoding, or cache-key behavior used internally by the package.

## Settings helpers

`get_setting(key, default=None)` reads GeneralManager settings with the package's
legacy-compatible precedence: nested `settings.GENERAL_MANAGER[key]`, then
`settings.GENERAL_MANAGER_<key>`, then top-level `settings.<key>`, then the
provided default. Only dict-valued `GENERAL_MANAGER` settings are used for the
nested lookup; non-dict values are ignored. A nested value of `None` is treated
as an explicit configured value, not as missing. Unexpected errors raised by
Django settings attribute access propagate.

## Naming helpers

Use `snake_to_pascal`, `snake_to_camel`, `pascal_to_snake`, and `camel_to_snake` when project code needs to match GeneralManager's GraphQL and Python naming conventions.
They are intentionally small transforms rather than full identifier parsers:
snake-case helpers split only on `_`, reverse helpers split every uppercase
character individually, and acronym-like runs are not preserved.

```python
from general_manager.utils import camel_to_snake, snake_to_camel

field_name = snake_to_camel("create_invoice_run")  # "createInvoiceRun"
python_name = camel_to_snake("createInvoiceRun")  # "create_invoice_run"
```

Leading, trailing, or repeated underscores collapse for `snake_to_pascal()` and
for non-leading segments in `snake_to_camel()`. Digits and punctuation are
preserved unless Python's `str.title()` changes the segment during the
snake-case helpers.

## Filter helpers

`parse_filters` parses simple Django-style filter expressions into structured criteria used by filtering helpers, including `filter_kwargs` for `GeneralManager`-typed fields and `filter_funcs` predicates for scalar or Python-only values. `GeneralManager`-typed fields are inputs whose declared `Input.type` is a `GeneralManager` subclass. Prefer manager `filter()` and `exclude()` APIs for normal application queries.

Use it when you are implementing a custom calculation or in-memory helper that needs to reuse GeneralManager's input casting rules:

```python
from general_manager import Input
from general_manager.utils import parse_filters

criteria = parse_filters(
    {"name__startswith": "Acme", "age__gte": "18"},
    {"name": Input(str), "age": Input(int)},
)

name_ok = criteria["name"]["filter_funcs"][0]("Acme Ltd")
age_ok = criteria["age"]["filter_funcs"][0](21)
```

Supported predicate lookups are `exact`, `lt`, `lte`, `gt`, `gte`, `contains`, `startswith`, `endswith`, and `in`. Missing lookups default to `exact`; unknown final lookup names are treated as part of the attribute path and then compared with `exact`. Predicate traversal uses Python attributes only, not mapping keys or sequence indexes. Missing nested attributes and malformed attribute paths return `False`.

For `GeneralManager`-typed inputs, `parse_filters` returns `filter_kwargs` instead of Python predicates. For non-manager inputs it always returns `filter_funcs`; non-manager criteria never emit `filter_kwargs`. Nested manager lookups are preserved for the downstream bucket, so `project__name__startswith="A"` becomes `{"project": {"filter_kwargs": {"name__startswith": "A"}}}`. For manager inputs, any suffix after the field name is an explicit downstream lookup path, whether or not the final segment is one of the predicate lookup names. Manager suffix filters preserve the raw filter value unchanged for downstream bucket filtering. A manager field can also be filtered through its id alias: `project_id__in=[1, 2]` is parsed as `{"project": {"filter_kwargs": {"id__in": [1, 2]}}}` when `project` is a configured manager input. Alias values are not cast because they already target the id lookup. Direct manager filters without a suffix, such as `project=raw_value`, cast non-manager values through the manager `Input`; values that are already `GeneralManager` instances skip casting. In both cases the downstream value is `getattr(value, "id", value)`.

`parse_filters` raises `UnknownInputFieldError` when the filter references a field that is not configured and is not a valid manager `_id` alias. The exception exposes the parsed `field_name` and formats as `Unknown input field '<field>' in filter.` Cast errors from the configured `Input` are propagated according to the public `Input.cast()` contract in the core API reference. For non-manager list and tuple values, each item is cast individually when the value is not already an instance of the input type; other non-manager values are cast once. Multiple criteria for one field keep the iteration order produced by `filter_kwargs.items()`. Manager filter kwargs are stored in a mapping, so normalized duplicate lookup keys overwrite earlier values; for example `project_id__in` and `project__id__in` both normalize to `id__in` and the later item wins. Non-manager duplicate criteria append another predicate. Empty result entries are not emitted.

String lookups are case-sensitive and require string candidates and string filter values. `contains` checks whether the filter value appears inside the candidate string. The `in` lookup accepts non-string containers such as lists, tuples, sets, and ranges; strings and bytes are rejected as membership containers to avoid accidental substring filtering. Unsupported lookups, incompatible operand types, and rich comparisons that cannot be evaluated return `False`.

`create_filter_function` builds callable predicates for in-memory and filter-helper use where appropriate:

```python
from general_manager.utils import create_filter_function

city_is_berlin = create_filter_function("address__city__exact", "Berlin")
```

## Serialization and cache keys

`CustomJSONEncoder` serializes values that commonly appear in GeneralManager
payloads and cache-key inputs. Date, datetime, and time values are rendered with
`isoformat()`. `GeneralManager` instances are represented as
`ClassName(**identification)`. Values supported by Python's standard JSON
encoder keep the normal behavior, and unsupported objects fall back to
`str(value)`. Exceptions raised while reading manager identification or while
building the string fallback propagate to the caller.

`make_cache_key(func, args, kwargs)` builds deterministic cache keys for values
that need to align with GeneralManager cache behavior. `kwargs=None` is treated
as empty kwargs, while supplied mappings are copied with `dict(...)` even when a
custom mapping is falsey. The helper binds the supplied arguments with
`inspect.Signature.bind_partial()`, applies default values, and serializes the
function module, qualified name, and normalized bound arguments with sorted JSON
keys using `CustomJSONEncoder`. Positional and keyword forms of the same
function call therefore produce the same key. The final digest is SHA-256 with
`usedforsecurity=False`. Invalid argument combinations raise `TypeError` from
signature binding, and serialization errors from `CustomJSONEncoder` can
propagate.

## Path mapping and small transforms

`PathMap` supports nested path mapping where integrations need stable source-to-target field paths. It keeps singleton graph metadata from registered `GeneralManager` classes and records requested `PathTracer` objects in a partial pair cache keyed by `(start_class_name, destination_class_name)`. A tracer can exist with `tracer.path is None` when both classes are registered but no route is reachable; `PathMap.to(...)` returns `None` when the source or destination is unknown, or when the source and destination are the same class.

Path discovery uses `Interface.get_attribute_types()` entries that expose a `type` key and `@GraphQLProperty` return annotations. Only `GeneralManager` subclasses are traversed, and each manager class is expanded once per lookup to avoid cycles. Direct `PathTracer` construction represents same-class paths as `[]`; `PathMap.to(...)` treats same-class lookups as no traversal and returns `None`. Reachable paths are lists of attribute names, and unreachable paths are cached as tracers with `path is None`.

```python
path_map = PathMap(Project)
tracer = path_map.to(Customer)
assert tracer is not None

customer_or_bucket = PathMap(project).go_to(Customer)
connected_names = path_map.get_all_connected()
```

Use `PathMap(SomeManagerClass).to(TargetManager)` when you need the cached `PathTracer` and its `.path`. Destinations are manager classes or string class names, not manager instances. Path lookup is lazy: constructing `PathMap` only refreshes manager graph metadata, and the first `to()` or `go_to()` for a `(source, destination)` pair resolves and caches only that pair. Missing paths are cached as tracers with `path is None`, so repeated misses return quickly without re-searching the graph.

Use `PathMap(manager_instance).go_to(TargetManager)` when you want to traverse the path from a concrete instance. `go_to()` returns `None` when no mapping key can be created, for same-class lookups, when the cached tracer is unreachable (`path is None`), or when bucket traversal has no entries to merge. If a traversable path exists but the `PathMap` was created from a class or string start, `go_to()` raises `MissingStartInstanceError`.

Use `PathMap(SomeManagerClass).get_all_connected()` to list reachable destination manager class names. It walks the cached adjacency graph and does not materialize every possible source/destination tracer.

Bucket-valued paths are traversed one entry at a time. For each path segment, `PathTracer.traverse_path()` reads the attribute from every bucket entry and unions the resulting managers or buckets into one value. Every traversed attribute must return a `GeneralManager` or `Bucket`; other values raise `InvalidPathTraversalValueError`.

`args_to_kwargs(args, keys, existing_kwargs=None)` is the small helper used when
callers need to accept positional values for a known ordered keyword schema. It
materializes `keys` once, assigns positional values in order, and then merges
`existing_kwargs` when it is not `None`. Fewer positional values simply omit the
remaining keys; more positional values raise `TooManyArgumentsError`. Existing
keyword names that overlap generated names raise `ConflictingKeywordError`
before merging. Falsey custom mappings are still merged, and iteration errors
from `keys` or `existing_kwargs` propagate. The helper returns a new dictionary,
does not mutate `existing_kwargs`, and preserves insertion order as generated
keys first followed by non-conflicting existing keyword entries.

`none_to_zero(value)` is a small compatibility helper for optional numeric
inputs. It returns `0` only for `None` and otherwise returns the original
`int`, `float`, or `Measurement` object unchanged:

```python
from general_manager.utils import none_to_zero

quantity = none_to_zero(optional_quantity)
total = none_to_zero(existing_total) + quantity
```

Use it when package-consistent `None` handling is more useful than a broader
truthiness check; `0`, `0.0`, and zero-valued measurements are preserved.

See the [Utilities API reference](../api/utils.md) for signatures.
