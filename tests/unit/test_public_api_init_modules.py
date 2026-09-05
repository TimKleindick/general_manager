from __future__ import annotations

import json
import os
from pathlib import Path
from importlib import import_module
import subprocess
import sys
import textwrap

import pytest

from general_manager.public_api_registry import EXPORT_REGISTRY
from general_manager.utils.public_api import _normalize_target

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "public_api_exports.json"
)


def _load_snapshot() -> dict[str, dict[str, tuple[str, str]]]:
    """
    Load the stored public API snapshot and convert each export target to a tuple.

    Reads the JSON file at SNAPSHOT_PATH and returns a mapping from module path to a mapping
    of export name to a `(target_module, target_attr)` tuple as recorded in the snapshot.

    Returns:
        snapshot (dict[str, dict[str, tuple[str, str]]]): Mapping of module path -> export name -> (target_module, target_attr).
    """
    snapshot_raw = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return {
        module_path: {
            export_name: tuple(target) for export_name, target in module_exports.items()
        }
        for module_path, module_exports in snapshot_raw.items()
    }


SNAPSHOT_EXPORTS: dict[str, dict[str, tuple[str, str]]] = _load_snapshot()

CURRENT_EXPORTS: dict[str, dict[str, tuple[str, str]]] = {
    module_path: {
        public_name: _normalize_target(public_name, target)
        for public_name, target in exports.items()
    }
    for module_path, exports in EXPORT_REGISTRY.items()
}

MODULE_EXPORTS = SNAPSHOT_EXPORTS


def test_permission_filter_decision_is_an_expected_permission_export() -> None:
    assert "PermissionFilterDecision" in MODULE_EXPORTS["general_manager.permission"]


def _build_export_parameters() -> list[tuple[str, str, str, str]]:
    parameters: list[tuple[str, str, str, str]] = []
    for module_path, exports in MODULE_EXPORTS.items():
        for export_name, (target_module, target_attr) in exports.items():
            parameters.append((module_path, export_name, target_module, target_attr))
    return parameters


def test_public_api_snapshot_matches_registry() -> None:
    assert CURRENT_EXPORTS == SNAPSHOT_EXPORTS


@pytest.mark.parametrize("module_path", sorted(MODULE_EXPORTS.keys()))
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
    """
    Verifies that accessing a non-existent attribute raises AttributeError.

    Parameters:
        module_path (str): Dotted import path of the module to inspect (e.g., "package.submodule").
    """
    module = import_module(module_path)

    with pytest.raises(AttributeError):
        module.does_not_exist  # noqa: B018


@pytest.mark.parametrize("module_path", ["general_manager", "general_manager.search"])
def test_search_invalidation_contract_is_exported(module_path: str) -> None:
    module = import_module(module_path)

    assert module.SearchChange.__module__ == "general_manager.search.config"
    assert module.SearchInvalidationRule.__module__ == "general_manager.search.config"


def test_invalid_error_template_error_is_exported_from_rule() -> None:
    public_module = import_module("general_manager.rule")
    implementation_module = import_module("general_manager.rule.rule")

    assert (
        public_module.InvalidErrorTemplateError
        is implementation_module.InvalidErrorTemplateError
    )
    assert "InvalidErrorTemplateError" in public_module.__all__


def test_graphql_type_is_a_package_root_export() -> None:
    public_module = import_module("general_manager")
    implementation_module = import_module("general_manager.api.graphql_type")

    assert "GraphQLType" in public_module.__all__
    assert public_module.GraphQLType is implementation_module.GraphQLType


@pytest.mark.parametrize("implementations_first", [False, True])
def test_utility_function_exports_survive_import_order(
    implementations_first: bool,
) -> None:
    """Utility facade functions stay callable after implementation imports."""
    code = textwrap.dedent(
        f"""
        from importlib import import_module

        from general_manager.public_api_registry import UTILS_EXPORTS

        package = import_module("general_manager.utils")
        names = ("none_to_zero", "args_to_kwargs", "make_cache_key")
        for name in names:
            legacy_path = "general_manager.utils." + name
            try:
                import_module(legacy_path)
            except ModuleNotFoundError as error:
                assert error.name == legacy_path, (legacy_path, error.name)
            else:
                raise AssertionError("Legacy utility module exists: " + legacy_path)
        implementation_modules = tuple(
            UTILS_EXPORTS[name][0] for name in names
        )

        def load_implementations():
            return tuple(import_module(path) for path in implementation_modules)

        if {implementations_first!r}:
            loaded_modules = load_implementations()
        from general_manager.utils import (
            args_to_kwargs as before_args_to_kwargs,
            make_cache_key as before_make_cache_key,
            none_to_zero as before_none_to_zero,
        )
        before = {{
            "none_to_zero": before_none_to_zero,
            "args_to_kwargs": before_args_to_kwargs,
            "make_cache_key": before_make_cache_key,
        }}
        if not {implementations_first!r}:
            loaded_modules = load_implementations()
        from general_manager.utils import (
            args_to_kwargs as after_args_to_kwargs,
            make_cache_key as after_make_cache_key,
            none_to_zero as after_none_to_zero,
        )
        after = {{
            "none_to_zero": after_none_to_zero,
            "args_to_kwargs": after_args_to_kwargs,
            "make_cache_key": after_make_cache_key,
        }}

        for name, implementation in zip(names, loaded_modules, strict=True):
            expected = getattr(implementation, name)
            assert callable(before[name]), (name, before[name])
            assert callable(after[name]), (name, after[name])
            assert before[name] is expected, (name, before[name], expected)
            assert after[name] is expected, (name, after[name], expected)
        """
    )
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    pythonpath = [str(project_root / "src"), str(project_root)]
    if existing_pythonpath:
        pythonpath.append(existing_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    environment["DJANGO_SETTINGS_MODULE"] = "tests.test_settings"
    result = subprocess.run(  # noqa: S603 - executable and code are test constants
        [sys.executable, "-c", code],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
