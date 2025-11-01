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
    "graph_ql_property": ("general_manager.api.property", "graph_ql_property"),
    "graph_ql_mutation": ("general_manager.api.mutation", "graph_ql_mutation"),
    "GeneralManager": ("general_manager.manager.general_manager", "GeneralManager"),
    "Input": ("general_manager.manager.input", "Input"),
    "get_logger": ("general_manager.logging", "get_logger"),
    "CalculationInterface": (
        "general_manager.interface.calculation_interface",
        "CalculationInterface",
    ),
    "DatabaseInterface": (
        "general_manager.interface.database_interface",
        "DatabaseInterface",
    ),
    "ExistingModelInterface": (
        "general_manager.interface.existing_model_interface",
        "ExistingModelInterface",
    ),
    "ReadOnlyInterface": (
        "general_manager.interface.read_only_interface",
        "ReadOnlyInterface",
    ),
    "ManagerBasedPermission": (
        "general_manager.permission.manager_based_permission",
        "ManagerBasedPermission",
    ),
    "register_permission": (
        "general_manager.permission.permission_checks",
        "register_permission",
    ),
    "permission_functions": (
        "general_manager.permission.permission_checks",
        "permission_functions",
    ),
    "Rule": ("general_manager.rule.rule", "Rule"),
}


API_EXPORTS: LazyExportMap = {
    "GraphQL": ("general_manager.api.graphql", "GraphQL"),
    "MeasurementType": ("general_manager.api.graphql", "MeasurementType"),
    "MeasurementScalar": ("general_manager.api.graphql", "MeasurementScalar"),
    "graph_ql_property": ("general_manager.api.property", "graph_ql_property"),
    "graph_ql_mutation": ("general_manager.api.mutation", "graph_ql_mutation"),
}


FACTORY_EXPORTS: LazyExportMap = {
    "AutoFactory": ("general_manager.factory.auto_factory", "AutoFactory"),
    "lazy_measurement": ("general_manager.factory.factory_methods", "lazy_measurement"),
    "lazy_delta_date": ("general_manager.factory.factory_methods", "lazy_delta_date"),
    "lazy_project_name": (
        "general_manager.factory.factory_methods",
        "lazy_project_name",
    ),
    "lazy_date_today": ("general_manager.factory.factory_methods", "lazy_date_today"),
    "lazy_date_between": (
        "general_manager.factory.factory_methods",
        "lazy_date_between",
    ),
    "lazy_date_time_between": (
        "general_manager.factory.factory_methods",
        "lazy_date_time_between",
    ),
    "lazy_integer": ("general_manager.factory.factory_methods", "lazy_integer"),
    "lazy_decimal": ("general_manager.factory.factory_methods", "lazy_decimal"),
    "lazy_choice": ("general_manager.factory.factory_methods", "lazy_choice"),
    "lazy_sequence": ("general_manager.factory.factory_methods", "lazy_sequence"),
    "lazy_boolean": ("general_manager.factory.factory_methods", "lazy_boolean"),
    "lazy_uuid": ("general_manager.factory.factory_methods", "lazy_uuid"),
    "lazy_faker_name": ("general_manager.factory.factory_methods", "lazy_faker_name"),
    "lazy_faker_email": ("general_manager.factory.factory_methods", "lazy_faker_email"),
    "lazy_faker_sentence": (
        "general_manager.factory.factory_methods",
        "lazy_faker_sentence",
    ),
    "lazy_faker_address": (
        "general_manager.factory.factory_methods",
        "lazy_faker_address",
    ),
    "lazy_faker_url": ("general_manager.factory.factory_methods", "lazy_faker_url"),
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
    "register_permission": (
        "general_manager.permission.permission_checks",
        "register_permission",
    ),
    "permission_functions": (
        "general_manager.permission.permission_checks",
        "permission_functions",
    ),
    "configure_audit_logger": (
        "general_manager.permission.audit",
        "configure_audit_logger",
    ),
    "configure_audit_logger_from_settings": (
        "general_manager.permission.audit",
        "configure_audit_logger_from_settings",
    ),
    "FileAuditLogger": (
        "general_manager.permission.audit",
        "FileAuditLogger",
    ),
    "DatabaseAuditLogger": (
        "general_manager.permission.audit",
        "DatabaseAuditLogger",
    ),
    "PermissionAuditEvent": (
        "general_manager.permission.audit",
        "PermissionAuditEvent",
    ),
    "AuditLogger": ("general_manager.permission.audit", "AuditLogger"),
}


INTERFACE_EXPORTS: LazyExportMap = {
    "InterfaceBase": "general_manager.interface.base_interface",
    "DBBasedInterface": "general_manager.interface.database_based_interface",
    "DatabaseInterface": "general_manager.interface.database_interface",
    "ExistingModelInterface": "general_manager.interface.existing_model_interface",
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
    "graph_ql_property": ("general_manager.api.property", "graph_ql_property"),
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
