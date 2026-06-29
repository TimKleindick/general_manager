# Factory API

::: general_manager.factory.auto_factory.AutoFactory

`AutoFactory` is the generated factory base attached to ORM-backed managers as
`Manager.Factory`. It fills missing fields from interface metadata, strips
many-to-many values before model construction, applies many-to-many assignments
after saved creation, and uses the interface's `input_fields` plus
`format_identification()` to wrap saved objects back into manager instances.
`build()` returns unsaved Django model instances; `create()` validates, saves,
and returns GeneralManager wrappers. Batch calls keep the same distinction:
build batches return model lists and create batches return manager lists.
Factory relation reuse and sequence counters use the interface database alias
when one is configured, so factories query and save against the same database.

The generation order is:

1. `_generate()` validates `Meta.model`, asks the interface for custom-field
   metadata, and adds missing generated or declared defaults. Caller-supplied
   keyword arguments already present in the payload win over generated defaults.
   Missing foreign-key and one-to-one values use the configured related-factory
   mode.
2. factory_boy dispatches to `_build()` or `_create()`.
3. `_adjust_kwargs()` removes many-to-many values from constructor kwargs and
   coerces foreign-key/one-to-one values.
4. If `_adjustmentMethod` is configured, it receives those generated/default
   filled, many-to-many-stripped, relation-coerced kwargs and returns one or
   more record payloads. Otherwise the normalized kwargs are assigned directly.
5. Create strategy calls `full_clean()` and `save()` for each record; build
   strategy only constructs unsaved model instances.
6. `_generate()` applies many-to-many assignments to saved create-strategy
   model instances, then wraps those model instances into manager instances.

Call-time keyword arguments override generated defaults. Foreign-key and
one-to-one values are normalized through the related model resolver, so callers
may pass a Django model instance, a GeneralManager wrapper, or an identifier
value that Django accepts.

Automatic relation defaults use `reuse_existing` mode unless configured
otherwise. Foreign-key and one-to-one defaults prefer reusable existing related
rows; one-to-one reuse excludes rows already linked through that one-to-one
field. If no reusable row exists and the related model exposes a
GeneralManager factory, AutoFactory creates a related row through that factory.
Nullable relations and relations with a declared default of `None` keep their
nullable behavior in default mode and may remain `None` even when related rows
or factories are available. When the factory interface defines a database alias,
existing-row lookup and one-to-one linked-row filtering use that alias.

Factories can change automatic relation generation with `_related_factory_mode`
for all generated relations or `_related_factory_modes` for individual fields.
`"create"` forces a new related object when a related factory exists and
bypasses nullable/default-`None` relation short-circuiting. If no related
factory exists, nullable relations may still resolve to `None` and required
relations raise `MissingFactoryOrInstancesError`. `"random"` restores the
legacy foreign-key behavior that may create through the related factory or reuse
an existing related row.

Many-to-many keyword values are removed before instantiation and assigned after
saved `create()` calls. A manager/queryset/list/tuple/set is expanded to a
list; a scalar is treated as a one-item assignment. Values that cannot be
resolved to Django model instances are passed through to Django's relation
manager, so normal Django validation or assignment errors surface. Omitted
blank many-to-many fields stay empty in default `reuse_existing` mode. A
field-specific `_related_factory_modes = {"field_name": "create"}` entry can
generate and assign many-to-many values after creation. `build()` returns
unsaved model instances and skips many-to-many assignment.

`Factory._adjustmentMethod`, when defined, receives the normalized keyword
arguments and returns either one `dict[str, object]` record payload or a
`list[dict[str, object]]` for fan-out creation. AutoFactory does not validate
that shape before using it: unsupported return values fail through normal Python
unpacking, model assignment, `full_clean()`, save, or factory_boy errors. Lists
are processed in order; an empty list returns an empty list. AutoFactory does
not create a transaction around an adjustment-method list, so create-mode
failures can leave earlier records saved. When create-strategy wrapping cannot
find the parent manager class it raises
`MissingManagerClassError`; when an identification value cannot be read from a
generated model, it raises `MissingIdentificationFieldError`. Non-model factory
outputs raise `InvalidGeneratedObjectError`, and invalid `Meta.model`
configuration raises `InvalidAutoFactoryModelError`.

Created manager wrapping reads each key in `interface.input_fields` from the
model instance, falling back to `<name>_id`; related model objects are converted
to their primary keys. The mapping returned by `interface.format_identification()`
must be accepted as keyword arguments by the parent manager constructor. Factory
class attributes are used as declared defaults only when they are present and
not callable, `classmethod`, or `staticmethod`. A declared value of `None` is
treated the same as no declared default by the current generation path.

`AutoFactory._setup_next_sequence()` returns at least the target model's current
row count, using `interface._get_database_alias()` when the interface provides a
database alias. For example, if one row already exists, the first generated
factory_boy sequence index is `1`. Override `_setup_next_sequence()` in a custom
factory when uniqueness depends on parsing existing field values, such as
extracting numeric suffixes from names or codes, rather than on the simple row
count.

`general_manager.factory.factories` contains lower-level generation helpers used
by `AutoFactory`. They are importable for advanced factory customization, but
application factory classes should prefer the exported lazy helpers below when a
specific generated default is needed.

`get_field_value(field)` accepts a Django `Field` or relation descriptor and
returns a factory_boy declaration, model instance, scalar default, or `None`
that can be assigned to that field by factory_boy. It handles choices,
measurement fields, common scalar fields, regex-backed `CharField`s, nullable
fields, foreign keys, and one-to-one relations. It does not generate
many-to-many assignment lists; if a many-to-many field is passed here it returns
`None`, and callers should use `get_many_to_many_field_value()` for
`ManyToManyField` values. Unsupported scalar or custom Django field classes also
return `None`. Relation fields default to `relation_generation="reuse_existing"`:
nullable/default-`None` relations may return `None`, reusable existing rows are
preferred, and a related GeneralManager factory is used only when no reusable
row exists. `relation_generation="create"` forces related-factory creation when
available; `relation_generation="random"` provides legacy foreign-key behavior
that may create or reuse. The optional `database_alias` argument scopes existing
related-row lookup to a specific Django database alias. Nullable foreign-key and
one-to-one fields with no factory and no existing rows return `None`, while
non-nullable relation fields in the same situation raise
`MissingFactoryOrInstancesError`. Missing relation metadata raises
`MissingRelatedModelError`; non-model relation targets and non-relational scalar
fields passed to relation helpers raise `InvalidRelatedModelTypeError`.

`get_many_to_many_field_value(field)` accepts a Django `ManyToManyField` and
returns related model instances for assignment after object creation. In default
mode it samples existing related rows when any exist; if no existing rows are
available, it creates rows through the related GeneralManager factory when one
is registered. `relation_generation="create"` bypasses existing rows and uses
the related factory. The optional `database_alias` argument scopes existing-row
sampling to that alias. The helper raises `MissingFactoryOrInstancesError` when
no related factory or existing rows are available. GeneralManager factory
outputs are normalized to Django model instances; if a manager output cannot be
resolved to its model row, `UnableToResolveManagerInstanceError` is raised.
`blank=True` fields may generate an empty list; `blank=False` fields request at
least one related instance.

The factory helper exception classes are stable by type. Their messages are
diagnostic and are not a stable parsing contract.

::: general_manager.factory.factories.get_field_value

::: general_manager.factory.factories.get_many_to_many_field_value

::: general_manager.factory.factories.get_related_model

::: general_manager.factory.factories.MissingFactoryOrInstancesError

::: general_manager.factory.factories.MissingRelatedModelError

::: general_manager.factory.factories.InvalidRelatedModelTypeError

::: general_manager.factory.factories.UnableToResolveManagerInstanceError

::: general_manager.factory.factory_methods.lazy_measurement

::: general_manager.factory.factory_methods.lazy_delta_date

::: general_manager.factory.factory_methods.lazy_project_name

::: general_manager.factory.factory_methods.lazy_date_today

::: general_manager.factory.factory_methods.lazy_date_between

::: general_manager.factory.factory_methods.lazy_date_time_between

::: general_manager.factory.factory_methods.lazy_integer

::: general_manager.factory.factory_methods.lazy_decimal

::: general_manager.factory.factory_methods.lazy_choice

::: general_manager.factory.factory_methods.lazy_sequence

::: general_manager.factory.factory_methods.lazy_boolean

::: general_manager.factory.factory_methods.lazy_uuid

::: general_manager.factory.factory_methods.lazy_faker_name

::: general_manager.factory.factory_methods.lazy_faker_email

::: general_manager.factory.factory_methods.lazy_faker_sentence

::: general_manager.factory.factory_methods.lazy_faker_address

::: general_manager.factory.factory_methods.lazy_faker_url
