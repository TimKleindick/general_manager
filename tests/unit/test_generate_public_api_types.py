"""Regression tests for public API type-module generation."""

from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from scripts import generate_public_api_types


def test_generated_type_module_preserves_module_docstring(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(generate_public_api_types, "TYPES_PACKAGE", tmp_path)

    generate_public_api_types._write_module(
        "general_manager.search",
        (),
        [],
    )

    generated = (tmp_path / "search.py").read_text(encoding="utf-8")
    assert generated.startswith(
        '"""Type-only imports for public API re-exports."""\n\n'
        "from __future__ import annotations\n"
    )


def test_generated_utility_types_target_private_implementation_modules(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Generated utility imports keep public function names collision-free."""
    types_package = tmp_path / "_types"
    monkeypatch.setattr(generate_public_api_types, "TYPES_PACKAGE", types_package)
    monkeypatch.setattr(
        generate_public_api_types,
        "SNAPSHOT_PATH",
        tmp_path / "public_api_exports.json",
    )

    generate_public_api_types.main()

    generated = (types_package / "utils.py").read_text(encoding="utf-8")
    assert (
        "from general_manager.utils._args_to_kwargs import args_to_kwargs" in generated
    )
    assert (
        "from general_manager.utils._make_cache_key import make_cache_key" in generated
    )
    assert "from general_manager.utils._none_to_zero import none_to_zero" in generated
    assert "general_manager.utils.args_to_kwargs" not in generated
    assert "general_manager.utils.make_cache_key" not in generated
    assert "general_manager.utils.none_to_zero" not in generated


def test_generated_type_module_wraps_long_imports_as_valid_python(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Long generated imports stay Ruff-compatible without becoming invalid."""
    monkeypatch.setattr(generate_public_api_types, "TYPES_PACKAGE", tmp_path)
    import_line = (
        "from general_manager.really_long_public_module_name_for_static_exports "
        "import StablePublicSymbol"
    )

    generate_public_api_types._write_module(
        "general_manager.long_exports",
        ("StablePublicSymbol",),
        [import_line],
    )

    generated = (tmp_path / "long_exports.py").read_text(encoding="utf-8")
    assert (
        "from general_manager.really_long_public_module_name_for_static_exports import (\n"
        "    StablePublicSymbol,\n"
        ")\n"
    ) in generated
    compile(generated, "long_exports.py", "exec")
