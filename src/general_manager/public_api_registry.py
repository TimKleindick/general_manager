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
    "GeneralManager": ("general_manager.manager.generalManager", "GeneralManager"),
    "Input": ("general_manager.manager.input", "Input"),
    "CalculationInterface": (
        "general_manager.interface.calculationInterface",
        "CalculationInterface",
    ),
    "DatabaseInterface": (
        "general_manager.interface.databaseInterface",
        "DatabaseInterface",
    ),
    "ReadOnlyInterface": (
        "general_manager.interface.readOnlyInterface",
        "ReadOnlyInterface",
    ),
    "ManagerBasedPermission": (
        "general_manager.permission.managerBasedPermission",
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
    "AutoFactory": ("general_manager.factory.autoFactory", "AutoFactory"),
    "LazyMeasurement": ("general_manager.factory.factoryMethods", "LazyMeasurement"),
    "LazyDeltaDate": ("general_manager.factory.factoryMethods", "LazyDeltaDate"),
    "LazyProjectName": ("general_manager.factory.factoryMethods", "LazyProjectName"),
    "LazyDateToday": ("general_manager.factory.factoryMethods", "LazyDateToday"),
    "LazyDateBetween": ("general_manager.factory.factoryMethods", "LazyDateBetween"),
    "LazyDateTimeBetween": (
        "general_manager.factory.factoryMethods",
        "LazyDateTimeBetween",
    ),
    "LazyInteger": ("general_manager.factory.factoryMethods", "LazyInteger"),
    "LazyDecimal": ("general_manager.factory.factoryMethods", "LazyDecimal"),
    "LazyChoice": ("general_manager.factory.factoryMethods", "LazyChoice"),
    "LazySequence": ("general_manager.factory.factoryMethods", "LazySequence"),
    "LazyBoolean": ("general_manager.factory.factoryMethods", "LazyBoolean"),
    "LazyUUID": ("general_manager.factory.factoryMethods", "LazyUUID"),
    "LazyFakerName": ("general_manager.factory.factoryMethods", "LazyFakerName"),
    "LazyFakerEmail": ("general_manager.factory.factoryMethods", "LazyFakerEmail"),
    "LazyFakerSentence": (
        "general_manager.factory.factoryMethods",
        "LazyFakerSentence",
    ),
    "LazyFakerAddress": ("general_manager.factory.factoryMethods", "LazyFakerAddress"),
    "LazyFakerUrl": ("general_manager.factory.factoryMethods", "LazyFakerUrl"),
}


MEASUREMENT_EXPORTS: LazyExportMap = {
    "Measurement": ("general_manager.measurement.measurement", "Measurement"),
    "ureg": ("general_manager.measurement.measurement", "ureg"),
    "currency_units": ("general_manager.measurement.measurement", "currency_units"),
    "MeasurementField": (
        "general_manager.measurement.measurementField",
        "MeasurementField",
    ),
}


UTILS_EXPORTS: LazyExportMap = {
    "noneToZero": ("general_manager.utils.noneToZero", "noneToZero"),
    "args_to_kwargs": ("general_manager.utils.argsToKwargs", "args_to_kwargs"),
    "make_cache_key": ("general_manager.utils.makeCacheKey", "make_cache_key"),
    "parse_filters": ("general_manager.utils.filterParser", "parse_filters"),
    "create_filter_function": (
        "general_manager.utils.filterParser",
        "create_filter_function",
    ),
    "snake_to_pascal": ("general_manager.utils.formatString", "snake_to_pascal"),
    "snake_to_camel": ("general_manager.utils.formatString", "snake_to_camel"),
    "pascal_to_snake": ("general_manager.utils.formatString", "pascal_to_snake"),
    "camel_to_snake": ("general_manager.utils.formatString", "camel_to_snake"),
    "CustomJSONEncoder": ("general_manager.utils.jsonEncoder", "CustomJSONEncoder"),
    "PathMap": ("general_manager.utils.pathMapping", "PathMap"),
}


PERMISSION_EXPORTS: LazyExportMap = {
    "BasePermission": ("general_manager.permission.basePermission", "BasePermission"),
    "ManagerBasedPermission": (
        "general_manager.permission.managerBasedPermission",
        "ManagerBasedPermission",
    ),
    "MutationPermission": (
        "general_manager.permission.mutationPermission",
        "MutationPermission",
    ),
}


INTERFACE_EXPORTS: LazyExportMap = {
    "InterfaceBase": "general_manager.interface.baseInterface",
    "DBBasedInterface": "general_manager.interface.databaseBasedInterface",
    "DatabaseInterface": "general_manager.interface.databaseInterface",
    "ReadOnlyInterface": "general_manager.interface.readOnlyInterface",
    "CalculationInterface": "general_manager.interface.calculationInterface",
}


CACHE_EXPORTS: LazyExportMap = {
    "cached": ("general_manager.cache.cacheDecorator", "cached"),
    "CacheBackend": ("general_manager.cache.cacheDecorator", "CacheBackend"),
    "DependencyTracker": ("general_manager.cache.cacheTracker", "DependencyTracker"),
    "record_dependencies": (
        "general_manager.cache.dependencyIndex",
        "record_dependencies",
    ),
    "remove_cache_key_from_index": (
        "general_manager.cache.dependencyIndex",
        "remove_cache_key_from_index",
    ),
    "invalidate_cache_key": (
        "general_manager.cache.dependencyIndex",
        "invalidate_cache_key",
    ),
}


BUCKET_EXPORTS: LazyExportMap = {
    "Bucket": ("general_manager.bucket.baseBucket", "Bucket"),
    "DatabaseBucket": ("general_manager.bucket.databaseBucket", "DatabaseBucket"),
    "CalculationBucket": (
        "general_manager.bucket.calculationBucket",
        "CalculationBucket",
    ),
    "GroupBucket": ("general_manager.bucket.groupBucket", "GroupBucket"),
}


MANAGER_EXPORTS: LazyExportMap = {
    "GeneralManager": ("general_manager.manager.generalManager", "GeneralManager"),
    "GeneralManagerMeta": ("general_manager.manager.meta", "GeneralManagerMeta"),
    "Input": ("general_manager.manager.input", "Input"),
    "GroupManager": ("general_manager.manager.groupManager", "GroupManager"),
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
