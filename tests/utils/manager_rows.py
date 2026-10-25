"""Build real manager instances for in-memory native resolver fixtures."""

from general_manager.manager.general_manager import GeneralManager


def materialized_manager(manager_class, values):
    manager = manager_class(
        **{
            name: values[name]
            for name in manager_class.Interface.input_fields
            if name in values
        }
    )
    attributes = manager_class.Interface.get_attribute_types()
    for name, value in values.items():
        related = attributes.get(name, {}).get("type")
        if isinstance(related, type) and issubclass(related, GeneralManager):
            if value is not None and not isinstance(value, GeneralManager):
                value = materialized_manager(related, vars(value))
        setattr(manager, name, value)
    return manager
