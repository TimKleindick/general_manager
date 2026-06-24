# Factory API

::: general_manager.factory.auto_factory.AutoFactory

`AutoFactory` is the generated factory base attached to ORM-backed managers as
`Manager.Factory`. It fills missing fields from interface metadata, strips
many-to-many values before model construction, applies many-to-many assignments
after build/create, and uses the interface's `input_fields` plus
`format_identification()` to wrap saved objects back into manager instances.
`build()` returns unsaved Django model instances; `create()` validates, saves,
and returns GeneralManager wrappers. Batch calls keep the same distinction:
build batches return model lists and create batches return manager lists.

The generation order is:

1. `_generate()` validates `Meta.model`, asks the interface for custom-field
   metadata, and adds missing generated or declared defaults. Caller-supplied
   keyword arguments already present in the payload win over generated defaults.
2. factory_boy dispatches to `_build()` or `_create()`.
3. `_adjust_kwargs()` removes many-to-many values from constructor kwargs and
   coerces foreign-key/one-to-one values.
4. If `_adjustmentMethod` is configured, it receives those generated/default
   filled, many-to-many-stripped, relation-coerced kwargs and returns one or
   more record payloads. Otherwise the normalized kwargs are assigned directly.
5. Create strategy calls `full_clean()` and `save()` for each record; build
   strategy only constructs unsaved model instances.
6. `_generate()` applies many-to-many assignments from the original payload and
   wraps create-strategy model instances into manager instances.

Call-time keyword arguments override generated defaults. Foreign-key and
one-to-one values are normalized through the related model resolver, so callers
may pass a Django model instance, a GeneralManager wrapper, or an identifier
value that Django accepts. Many-to-many keyword values are removed before
instantiation and assigned afterward. A manager/queryset/list/tuple/set is
expanded to a list; a scalar is treated as a one-item assignment. Values that
cannot be resolved to Django model instances are passed through to Django's
relation manager, so normal Django validation or assignment errors surface.
Many-to-many assignment is attempted for both `build()` and `create()`. Because
Django requires a saved primary key for many-to-many `.set(...)`, `build()` with
explicit or generated many-to-many values is unsupported and may raise Django's
unsaved-instance relation error.

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
return `None`. Related fields use the related model's GeneralManager factory
when one is registered, otherwise they select an existing related row when
possible. Nullable fields have a small chance to return `None`; nullable
foreign-key and one-to-one fields with no factory and no existing rows return
`None`, while non-nullable relation fields in the same situation raise
`MissingFactoryOrInstancesError`. Missing relation metadata raises
`MissingRelatedModelError`; non-model relation targets and non-relational scalar
fields passed to relation helpers raise `InvalidRelatedModelTypeError`.

`get_many_to_many_field_value(field)` accepts a Django `ManyToManyField` and
returns related model instances for assignment after object creation. It creates
new related rows through the related GeneralManager factory when available,
mixes in existing rows when present, and raises `MissingFactoryOrInstancesError`
when no related factory or existing rows are available. GeneralManager factory
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
