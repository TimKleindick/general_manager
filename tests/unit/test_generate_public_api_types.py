"""Regression tests for public API type-module generation."""

from __future__ import annotations

from scripts import generate_public_api_types


def test_generated_type_module_preserves_module_docstring(
    tmp_path,
    monkeypatch,
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
