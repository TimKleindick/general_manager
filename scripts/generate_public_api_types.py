"""Generate type-only helper modules for lazy public APIs.

The script reads ``EXPORT_REGISTRY`` so the runtime modules can keep their
exports in a single place while the generated helpers provide explicit imports
for static type checkers. Re-run the script whenever the registry changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TYPES_PACKAGE = PROJECT_ROOT / "src" / "general_manager" / "_types"
SNAPSHOT_PATH = PROJECT_ROOT / "tests" / "snapshots" / "public_api_exports.json"


def _normalize_target(name: str, target: str | tuple[str, str]) -> tuple[str, str]:
    if isinstance(target, tuple):
        return target
    return target, name


def _write_module(module_name: str, names: Iterable[str], imports: list[str]) -> None:
    """
    Create a type-only module under the TYPES_PACKAGE containing a __all__ export list and the given import lines.
    
    The target file path is derived from the dotted module_name by dropping its leading package component and placing the resulting path inside TYPES_PACKAGE; parent directories are created as needed. The generated file starts with future annotations, a module docstring, a __all__ assignment (listing provided names or an empty list), followed by the provided import lines, and is written as UTF-8 text ending with a newline.
    
    Parameters:
        module_name (str): Dotted module name that determines the output file location inside TYPES_PACKAGE.
        names (Iterable[str]): Public names to include in the module's __all__ in the given order.
        imports (list[str]): Lines of import statements to append to the module file.
    """
    components = module_name.split(".")
    # Drop the leading package name to build the path inside _types.
    relative_parts = components[1:] or [components[0]]
    output = TYPES_PACKAGE.joinpath(*relative_parts).with_suffix(".py")
    output.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = ["from __future__ import annotations", ""]
    lines.append('"""Type-only imports for public API re-exports."""')
    lines.append("")
    if names:
        lines.append("__all__ = [")
        for name in names:
            lines.append(f'    "{name}",')
        lines.append("]")
    else:
        lines.append("__all__: list[str] = []")
    lines.append("")
    lines.extend(imports)
    lines.append("")

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """
    Generate type-only helper modules for the public API and write a snapshot of the export registry.
    
    Reads EXPORT_REGISTRY, emits a typed-only module under the TYPES_PACKAGE for each registry entry (populating each module's __all__ and type-only import lines), and writes a JSON snapshot of the resolved export targets to SNAPSHOT_PATH. The function also ensures the TYPES_PACKAGE directory and its __init__.py exist and creates parent directories for the snapshot file as needed.
    """
    TYPES_PACKAGE.mkdir(parents=True, exist_ok=True)
    (TYPES_PACKAGE / "__init__.py").write_text(
        "from __future__ import annotations\n\n__all__: list[str] = []\n",
        encoding="utf-8",
    )

    # Ensure local package imports work when running the script directly
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from general_manager.public_api_registry import EXPORT_REGISTRY

    snapshot: dict[str, dict[str, list[str]]] = {}

    for module_name, exports in EXPORT_REGISTRY.items():
        ordered_names = sorted(exports.keys())
        import_lines: list[str] = []
        snapshot[module_name] = {}
        for public_name in ordered_names:
            module_path, attr_name = _normalize_target(
                public_name, exports[public_name]
            )
            snapshot[module_name][public_name] = [module_path, attr_name]
            if attr_name == public_name:
                import_lines.append(f"from {module_path} import {attr_name}")
            else:
                import_lines.append(
                    f"from {module_path} import {attr_name} as {public_name}"
                )
        _write_module(module_name, ordered_names, import_lines)

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()