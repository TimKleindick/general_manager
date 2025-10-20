from __future__ import annotations

import json
from pathlib import Path
from importlib import import_module

import pytest

from general_manager.public_api_registry import EXPORT_REGISTRY
from general_manager.utils.public_api import _normalize_target

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "public_api_exports.json"
)


def _load_snapshot() -> dict[str, dict[str, tuple[str, str]]]:
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
    module = import_module(module_path)
    assert not hasattr(module, "does_not_exist")
