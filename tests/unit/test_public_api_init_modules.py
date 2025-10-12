from __future__ import annotations

from importlib import import_module

import pytest

MODULE_EXPORTS: dict[str, dict[str, tuple[str, str]]] = {
    "general_manager": {
        "GraphQL": ("general_manager.api.graphql", "GraphQL"),
        "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
        "graphQlMutation": ("general_manager.api.mutation", "graphQlMutation"),
        "GeneralManager": ("general_manager.manager.generalManager", "GeneralManager"),
        "GeneralManagerMeta": ("general_manager.manager.meta", "GeneralManagerMeta"),
        "Input": ("general_manager.manager.input", "Input"),
        "Bucket": ("general_manager.bucket.baseBucket", "Bucket"),
        "DatabaseBucket": (
            "general_manager.bucket.databaseBucket",
            "DatabaseBucket",
        ),
        "CalculationBucket": (
            "general_manager.bucket.calculationBucket",
            "CalculationBucket",
        ),
        "GroupBucket": ("general_manager.bucket.groupBucket", "GroupBucket"),
    },
    "general_manager.api": {
        "GraphQL": ("general_manager.api.graphql", "GraphQL"),
        "MeasurementType": ("general_manager.api.graphql", "MeasurementType"),
        "MeasurementScalar": ("general_manager.api.graphql", "MeasurementScalar"),
        "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
        "graphQlMutation": ("general_manager.api.mutation", "graphQlMutation"),
    },
    "general_manager.bucket": {
        "Bucket": ("general_manager.bucket.baseBucket", "Bucket"),
        "DatabaseBucket": (
            "general_manager.bucket.databaseBucket",
            "DatabaseBucket",
        ),
        "CalculationBucket": (
            "general_manager.bucket.calculationBucket",
            "CalculationBucket",
        ),
        "GroupBucket": ("general_manager.bucket.groupBucket", "GroupBucket"),
    },
    "general_manager.cache": {
        "cached": ("general_manager.cache.cacheDecorator", "cached"),
        "CacheBackend": ("general_manager.cache.cacheDecorator", "CacheBackend"),
        "DependencyTracker": (
            "general_manager.cache.cacheTracker",
            "DependencyTracker",
        ),
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
    },
    "general_manager.factory": {
        "AutoFactory": ("general_manager.factory.autoFactory", "AutoFactory"),
        "LazyMeasurement": (
            "general_manager.factory.factoryMethods",
            "LazyMeasurement",
        ),
        "LazyDeltaDate": (
            "general_manager.factory.factoryMethods",
            "LazyDeltaDate",
        ),
        "LazyProjectName": (
            "general_manager.factory.factoryMethods",
            "LazyProjectName",
        ),
    },
    "general_manager.interface": {
        "InterfaceBase": (
            "general_manager.interface.baseInterface",
            "InterfaceBase",
        ),
        "DBBasedInterface": (
            "general_manager.interface.databaseBasedInterface",
            "DBBasedInterface",
        ),
        "DatabaseInterface": (
            "general_manager.interface.databaseInterface",
            "DatabaseInterface",
        ),
        "ReadOnlyInterface": (
            "general_manager.interface.readOnlyInterface",
            "ReadOnlyInterface",
        ),
        "CalculationInterface": (
            "general_manager.interface.calculationInterface",
            "CalculationInterface",
        ),
    },
    "general_manager.manager": {
        "GeneralManager": (
            "general_manager.manager.generalManager",
            "GeneralManager",
        ),
        "GeneralManagerMeta": (
            "general_manager.manager.meta",
            "GeneralManagerMeta",
        ),
        "Input": ("general_manager.manager.input", "Input"),
        "GroupManager": (
            "general_manager.manager.groupManager",
            "GroupManager",
        ),
        "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
    },
    "general_manager.measurement": {
        "Measurement": (
            "general_manager.measurement.measurement",
            "Measurement",
        ),
        "MeasurementField": (
            "general_manager.measurement.measurementField",
            "MeasurementField",
        ),
        "ureg": ("general_manager.measurement.measurement", "ureg"),
        "currency_units": (
            "general_manager.measurement.measurement",
            "currency_units",
        ),
    },
    "general_manager.permission": {
        "BasePermission": (
            "general_manager.permission.basePermission",
            "BasePermission",
        ),
        "ManagerBasedPermission": (
            "general_manager.permission.managerBasedPermission",
            "ManagerBasedPermission",
        ),
        "MutationPermission": (
            "general_manager.permission.mutationPermission",
            "MutationPermission",
        ),
    },
    "general_manager.rule": {
        "Rule": ("general_manager.rule.rule", "Rule"),
        "BaseRuleHandler": (
            "general_manager.rule.handler",
            "BaseRuleHandler",
        ),
    },
    "general_manager.utils": {
        "noneToZero": ("general_manager.utils.noneToZero", "noneToZero"),
        "args_to_kwargs": ("general_manager.utils.argsToKwargs", "args_to_kwargs"),
        "make_cache_key": (
            "general_manager.utils.makeCacheKey",
            "make_cache_key",
        ),
        "parse_filters": (
            "general_manager.utils.filterParser",
            "parse_filters",
        ),
        "create_filter_function": (
            "general_manager.utils.filterParser",
            "create_filter_function",
        ),
        "snake_to_pascal": (
            "general_manager.utils.formatString",
            "snake_to_pascal",
        ),
        "snake_to_camel": (
            "general_manager.utils.formatString",
            "snake_to_camel",
        ),
        "pascal_to_snake": (
            "general_manager.utils.formatString",
            "pascal_to_snake",
        ),
        "camel_to_snake": (
            "general_manager.utils.formatString",
            "camel_to_snake",
        ),
    },
}


def _build_export_parameters() -> list[tuple[str, str, str, str]]:
    parameters: list[tuple[str, str, str, str]] = []
    for module_path, exports in MODULE_EXPORTS.items():
        for export_name, (target_module, target_attr) in exports.items():
            parameters.append((module_path, export_name, target_module, target_attr))
    return parameters


@pytest.mark.parametrize("module_path", MODULE_EXPORTS.keys())
def test_public_api_defines_expected_exports(module_path: str) -> None:
    module = import_module(module_path)
    expected_names = set(MODULE_EXPORTS[module_path])
    assert set(module.__all__) == expected_names


@pytest.mark.parametrize(
    ("module_path", "export_name", "target_module", "target_attr"),
    _build_export_parameters(),
)
def test_public_api_exports_correct_object(
    module_path: str,
    export_name: str,
    target_module: str,
    target_attr: str,
) -> None:
    module = import_module(module_path)
    module.__dict__.pop(export_name, None)
    exported_value = getattr(module, export_name)
    expected_module = import_module(target_module)
    expected_value = getattr(expected_module, target_attr)
    assert exported_value is expected_value
    assert module.__dict__[export_name] is expected_value


@pytest.mark.parametrize("module_path", MODULE_EXPORTS.keys())
def test_public_api_dir_includes_exports(module_path: str) -> None:
    module = import_module(module_path)
    directory_listing = module.__dir__()
    for name in MODULE_EXPORTS[module_path]:
        assert name in directory_listing


@pytest.mark.parametrize("module_path", MODULE_EXPORTS.keys())
def test_public_api_invalid_attribute_raises(module_path: str) -> None:
    module = import_module(module_path)
    with pytest.raises(AttributeError):
        getattr(module, "does_not_exist")
