# Utilities API

::: general_manager.utils.none_to_zero.none_to_zero

`none_to_zero(value)` returns the integer `0` only when `value is None`.
Non-`None` `int`, `float`, and `Measurement` values are returned unchanged,
without copying or coercion, so existing falsey numeric values such as `0` and
`0.0` remain the original values.

::: general_manager.utils.args_to_kwargs.args_to_kwargs

`args_to_kwargs(args, keys, existing_kwargs=None)` materializes `keys` once,
maps positional values to those keys in order, and merges `existing_kwargs`
afterward. Fewer positional values than keys leave the remaining keys absent.
Duplicate generated keys follow normal dictionary overwrite behavior while the
positional mapping is built, so the later paired value wins. `existing_kwargs`
is inspected and merged whenever it is not `None`, including falsey custom
mapping objects; overlap with generated keys raises `ConflictingKeywordError`
before merging. More positional values than keys raises
`TooManyArgumentsError`. Errors from iterating `keys` or `existing_kwargs`
and errors from reading keys or values from `existing_kwargs` propagate
unchanged. The returned dictionary is new and preserves insertion order as
generated keys first, then non-conflicting entries from `existing_kwargs`;
`existing_kwargs` itself is not mutated.

::: general_manager.conf.get_setting

`get_setting(key, default=None)` resolves GeneralManager settings from nested
`settings.GENERAL_MANAGER[key]`, then legacy `settings.GENERAL_MANAGER_<key>`,
then legacy top-level `settings.<key>`, then `default`. Only dict-valued
`GENERAL_MANAGER` settings are considered nested config; other values are
ignored. A nested value of `None` is returned as configured. Unexpected settings
attribute errors are not wrapped.

::: general_manager.utils.make_cache_key.make_cache_key

`make_cache_key(func, args, kwargs)` treats `kwargs=None` as empty kwargs and
copies supplied mappings with `dict(...)`, including falsey custom mappings. It
binds arguments with `inspect.Signature.bind_partial()`, applies default values,
serializes the function module, qualified name, and bound arguments with sorted
JSON keys using `CustomJSONEncoder`, then returns a SHA-256 hex digest computed
with `usedforsecurity=False`. Invalid argument combinations raise `TypeError`
from signature binding. Serialization errors from the custom encoder can
propagate.

::: general_manager.utils.filter_parser.UnknownInputFieldError

::: general_manager.utils.filter_parser.parse_filters

::: general_manager.utils.filter_parser.create_filter_function

::: general_manager.utils.format_string.snake_to_pascal

::: general_manager.utils.format_string.snake_to_camel

::: general_manager.utils.format_string.pascal_to_snake

::: general_manager.utils.format_string.camel_to_snake

The casing helpers are simple string transforms used by GeneralManager's public
GraphQL and Python naming layers. `snake_to_pascal()` splits only on
underscores, title-cases every segment, and drops empty segments from leading,
trailing, or repeated underscores. `snake_to_camel()` keeps the first
underscore-delimited segment unchanged and title-cases later segments. The
reverse helpers lower-case uppercase characters one character at a time and
prefix them with underscores after the first character, so acronym-like runs are
not preserved as words (`"ABC"` becomes `"a_b_c"`). Digits, lowercase
characters, existing underscores, and non-underscore punctuation are preserved
unless Python `str.title()` changes a segment during the snake-case helpers.
Empty input returns an empty string.

::: general_manager.utils.json_encoder.CustomJSONEncoder

`CustomJSONEncoder` preserves standard JSON encoding, serializes date/time
objects with `isoformat()`, formats `GeneralManager` instances as
`ClassName(**identification)`, and falls back to `str(value)` for otherwise
unsupported objects. Exceptions from identification access or `str(value)`
propagate.

::: general_manager.utils.path_mapping.PathMap

::: general_manager.utils.path_mapping.PathTracer

::: general_manager.utils.path_mapping.MissingStartInstanceError

::: general_manager.utils.path_mapping.InvalidPathTraversalValueError
