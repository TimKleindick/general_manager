"""Generate type-only helper modules for lazy public APIs.

The script reads ``EXPORT_REGISTRY`` so the runtime modules can keep their
exports in a single place while the generated helpers provide explicit imports
for static type checkers. Re-run the script whenever the registry changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from general_manager.public_api_registry import EXPORT_REGISTRY


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TYPES_PACKAGE = PROJECT_ROOT / "src" / "general_manager" / "_types"


def _normalize_target(name: str, target: str | tuple[str, str]) -> tuple[str, str]:
    if isinstance(target, tuple):
        return target
    return target, name


def _write_module(module_name: str, names: Iterable[str], imports: list[str]) -> None:
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
            lines.append(f"    \"{name}\",")
        lines.append("]")
    else:
        lines.append("__all__: list[str] = []")
    lines.append("")
    lines.extend(imports)
    lines.append("")

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    TYPES_PACKAGE.mkdir(parents=True, exist_ok=True)
    (TYPES_PACKAGE / "__init__.py").write_text(
        "from __future__ import annotations\n\n__all__: list[str] = []\n",
        encoding="utf-8",
    )

    for module_name, exports in EXPORT_REGISTRY.items():
        ordered_names = list(exports.keys())
        import_lines: list[str] = []
        for public_name in ordered_names:
            module_path, attr_name = _normalize_target(public_name, exports[public_name])
            if attr_name == public_name:
                import_lines.append(f"from {module_path} import {attr_name}")
            else:
                import_lines.append(
                    f"from {module_path} import {attr_name} as {public_name}"
                )
        _write_module(module_name, ordered_names, import_lines)


if __name__ == "__main__":
    main()
