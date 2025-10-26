"""Central registry for lazy public API exports.

Each entry maps a public name to either the module path string or a
``(module_path, attribute_name)`` tuple. A plain string means that the public
name and the attribute name are identical.
"""

from __future__ import annotations

from typing import Mapping

LazyExportMap = Mapping[str, str | tuple[str, str]]


GENERAL_MANAGER_EXPORTS: LazyExportMap = {
    "GraphQL": ("general_manager.api.graphql", "GraphQL"),
    "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
    "graphQlMutation": ("general_manager.api.mutation", "graphQlMutation"),
    "GeneralManager": ("general_manager.manager.general_manager", "GeneralManager"),
    "Input": ("general_manager.manager.input", "Input"),
    "CalculationInterface": (
        "general_manager.interface.calculation_interface",
        "CalculationInterface",
    ),
    "DatabaseInterface": (
        "general_manager.interface.database_interface",
        "DatabaseInterface",
    ),
    "ReadOnlyInterface": (
        "general_manager.interface.read_only_interface",
        "ReadOnlyInterface",
    ),
    "ManagerBasedPermission": (
        "general_manager.permission.manager_based_permission",
        "ManagerBasedPermission",
    ),
    "Rule": ("general_manager.rule.rule", "Rule"),
}


API_EXPORTS: LazyExportMap = {
    "GraphQL": ("general_manager.api.graphql", "GraphQL"),
    "MeasurementType": ("general_manager.api.graphql", "MeasurementType"),
    "MeasurementScalar": ("general_manager.api.graphql", "MeasurementScalar"),
    "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
    "graphQlMutation": ("general_manager.api.mutation", "graphQlMutation"),
}


FACTORY_EXPORTS: LazyExportMap = {
    "AutoFactory": ("general_manager.factory.auto_factory", "AutoFactory"),
    "LazyMeasurement": ("general_manager.factory.factory_methods", "LazyMeasurement"),
    "LazyDeltaDate": ("general_manager.factory.factory_methods", "LazyDeltaDate"),
    "LazyProjectName": ("general_manager.factory.factory_methods", "LazyProjectName"),
    "LazyDateToday": ("general_manager.factory.factory_methods", "LazyDateToday"),
    "LazyDateBetween": ("general_manager.factory.factory_methods", "LazyDateBetween"),
    "LazyDateTimeBetween": (
        "general_manager.factory.factory_methods",
        "LazyDateTimeBetween",
    ),
    "LazyInteger": ("general_manager.factory.factory_methods", "LazyInteger"),
    "LazyDecimal": ("general_manager.factory.factory_methods", "LazyDecimal"),
    "LazyChoice": ("general_manager.factory.factory_methods", "LazyChoice"),
    "LazySequence": ("general_manager.factory.factory_methods", "LazySequence"),
    "LazyBoolean": ("general_manager.factory.factory_methods", "LazyBoolean"),
    "LazyUUID": ("general_manager.factory.factory_methods", "LazyUUID"),
    "LazyFakerName": ("general_manager.factory.factory_methods", "LazyFakerName"),
    "LazyFakerEmail": ("general_manager.factory.factory_methods", "LazyFakerEmail"),
    "LazyFakerSentence": (
        "general_manager.factory.factory_methods",
        "LazyFakerSentence",
    ),
    "LazyFakerAddress": ("general_manager.factory.factory_methods", "LazyFakerAddress"),
    "LazyFakerUrl": ("general_manager.factory.factory_methods", "LazyFakerUrl"),
}


MEASUREMENT_EXPORTS: LazyExportMap = {
    "Measurement": ("general_manager.measurement.measurement", "Measurement"),
    "ureg": ("general_manager.measurement.measurement", "ureg"),
    "currency_units": ("general_manager.measurement.measurement", "currency_units"),
    "MeasurementField": (
        "general_manager.measurement.measurement_field",
        "MeasurementField",
    ),
}


UTILS_EXPORTS: LazyExportMap = {
    "none_to_zero": ("general_manager.utils.none_to_zero", "none_to_zero"),
    "args_to_kwargs": ("general_manager.utils.args_to_kwargs", "args_to_kwargs"),
    "make_cache_key": ("general_manager.utils.make_cache_key", "make_cache_key"),
    "parse_filters": ("general_manager.utils.filter_parser", "parse_filters"),
    "create_filter_function": (
        "general_manager.utils.filter_parser",
        "create_filter_function",
    ),
    "snake_to_pascal": ("general_manager.utils.format_string", "snake_to_pascal"),
    "snake_to_camel": ("general_manager.utils.format_string", "snake_to_camel"),
    "pascal_to_snake": ("general_manager.utils.format_string", "pascal_to_snake"),
    "camel_to_snake": ("general_manager.utils.format_string", "camel_to_snake"),
    "CustomJSONEncoder": ("general_manager.utils.json_encoder", "CustomJSONEncoder"),
    "PathMap": ("general_manager.utils.path_mapping", "PathMap"),
}


PERMISSION_EXPORTS: LazyExportMap = {
    "BasePermission": ("general_manager.permission.base_permission", "BasePermission"),
    "ManagerBasedPermission": (
        "general_manager.permission.manager_based_permission",
        "ManagerBasedPermission",
    ),
    "MutationPermission": (
        "general_manager.permission.mutation_permission",
        "MutationPermission",
    ),
}


INTERFACE_EXPORTS: LazyExportMap = {
    "InterfaceBase": "general_manager.interface.base_interface",
    "DBBasedInterface": "general_manager.interface.database_based_interface",
    "DatabaseInterface": "general_manager.interface.database_interface",
    "ReadOnlyInterface": "general_manager.interface.read_only_interface",
    "CalculationInterface": "general_manager.interface.calculation_interface",
}


CACHE_EXPORTS: LazyExportMap = {
    "cached": ("general_manager.cache.cache_decorator", "cached"),
    "CacheBackend": ("general_manager.cache.cache_decorator", "CacheBackend"),
    "DependencyTracker": ("general_manager.cache.cache_tracker", "DependencyTracker"),
    "record_dependencies": (
        "general_manager.cache.dependency_index",
        "record_dependencies",
    ),
    "remove_cache_key_from_index": (
        "general_manager.cache.dependency_index",
        "remove_cache_key_from_index",
    ),
    "invalidate_cache_key": (
        "general_manager.cache.dependency_index",
        "invalidate_cache_key",
    ),
}


BUCKET_EXPORTS: LazyExportMap = {
    "Bucket": ("general_manager.bucket.base_bucket", "Bucket"),
    "DatabaseBucket": ("general_manager.bucket.database_bucket", "DatabaseBucket"),
    "CalculationBucket": (
        "general_manager.bucket.calculation_bucket",
        "CalculationBucket",
    ),
    "GroupBucket": ("general_manager.bucket.group_bucket", "GroupBucket"),
}


MANAGER_EXPORTS: LazyExportMap = {
    "GeneralManager": ("general_manager.manager.general_manager", "GeneralManager"),
    "GeneralManagerMeta": ("general_manager.manager.meta", "GeneralManagerMeta"),
    "Input": ("general_manager.manager.input", "Input"),
    "GroupManager": ("general_manager.manager.group_manager", "GroupManager"),
    "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
}


RULE_EXPORTS: LazyExportMap = {
    "Rule": ("general_manager.rule.rule", "Rule"),
    "BaseRuleHandler": ("general_manager.rule.handler", "BaseRuleHandler"),
}


EXPORT_REGISTRY: Mapping[str, LazyExportMap] = {
    "general_manager": GENERAL_MANAGER_EXPORTS,
    "general_manager.api": API_EXPORTS,
    "general_manager.factory": FACTORY_EXPORTS,
    "general_manager.measurement": MEASUREMENT_EXPORTS,
    "general_manager.utils": UTILS_EXPORTS,
    "general_manager.permission": PERMISSION_EXPORTS,
    "general_manager.interface": INTERFACE_EXPORTS,
    "general_manager.cache": CACHE_EXPORTS,
    "general_manager.bucket": BUCKET_EXPORTS,
    "general_manager.manager": MANAGER_EXPORTS,
    "general_manager.rule": RULE_EXPORTS,
}
