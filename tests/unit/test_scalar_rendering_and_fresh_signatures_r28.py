"""Scalar wire budgets and fresh execution proof for the CI hot paths."""

import json
from types import FunctionType
from types import SimpleNamespace

import pytest

from general_manager.chat.planned.schema_projection import _bounded, _dump
from general_manager.chat.graphql_contract import _argument_info
from general_manager.utils.executable_contract import (
    executable_matches,
    executable_signature,
)


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        0,
        -7,
        10**100,
        "",
        "normal schema field",
        '"quoted"\\path\n\t',
        "".join(chr(code) for code in range(128)),
        "Größe 日本語 😀 \u2028 \u2029",
        "\ud800",
        -0.0,
        1.25,
        ["text", None, True, 17],
        {"field": "nested", "nullable": None},
    ],
)
def test_scalar_and_container_rendering_preserves_native_json_bytes(value):
    expected = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    assert _dump(value) == expected


@pytest.mark.parametrize(
    "value", [None, False, True, 0, -7, 10**100, "", 'a\n\\"', "😀"]
)
def test_scalar_character_guard_uses_exact_native_rendered_length(value):
    length = len(
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
    _bounded(value, max_chars=length)
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        _bounded(value, max_chars=length - 1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_scalar_rendering_still_rejects(value):
    with pytest.raises(ValueError):
        _dump(value)


def test_plain_function_proof_revalidates_code_and_added_defaults():
    def original(value):
        return value

    def replacement(value):
        return str(value)

    current = FunctionType(original.__code__, globals())
    pin = executable_signature(current)
    assert executable_matches(current, pin)
    current.__code__ = replacement.__code__
    assert not executable_matches(current, pin)
    current.__code__ = original.__code__
    assert executable_matches(current, pin)
    current.__defaults__ = (None,)
    assert not executable_matches(current, pin)
    current.__defaults__ = None
    current.__kwdefaults__ = {"extra": None}
    assert not executable_matches(current, pin)


def test_mutable_defaults_and_closure_changes_revoke_execution_proof():
    def defaulted(value=None, *, options=None):
        return value, options

    defaulted.__defaults__ = (["initial"],)
    defaulted.__kwdefaults__ = {"options": {"limit": 1}}
    pin = executable_signature(defaulted)
    assert executable_matches(defaulted, pin)
    defaulted.__defaults__[0].append("changed")
    assert not executable_matches(defaulted, pin)
    defaulted.__defaults__[0].pop()
    assert executable_matches(defaulted, pin)
    defaulted.__kwdefaults__["options"]["limit"] = 2
    assert not executable_matches(defaulted, pin)

    captured = 1

    def closure():
        return captured

    closure_pin = executable_signature(closure)
    assert executable_matches(closure, closure_pin)
    closure.__closure__[0].cell_contents = 2
    assert not executable_matches(closure, closure_pin)


def test_optional_none_default_proof_revalidates_replacement_and_restoration():
    def optional(value=None):
        return value

    pin = executable_signature(optional)
    assert executable_matches(optional, pin)
    optional.__defaults__ = (0,)
    assert not executable_matches(optional, pin)
    optional.__defaults__ = None
    assert not executable_matches(optional, pin)
    optional.__defaults__ = (None,)
    assert executable_matches(optional, pin)


def test_frozen_scalar_and_predicate_metadata_returns_detached_public_mappings():
    frozen = {
        "type": "Int",
        "description": "Allowed quantity",
        "filter_contract": {"source": "interface_attribute", "operator": "gte"},
    }
    argument = SimpleNamespace(_contract_argument_info=frozen)
    observed = _argument_info(argument)
    assert observed == frozen
    observed["description"] = "changed"
    observed["filter_contract"]["operator"] = "lt"
    assert frozen["description"] == "Allowed quantity"
    assert frozen["filter_contract"]["operator"] == "gte"
    assert _argument_info(argument) == frozen


def test_structured_default_metadata_preserves_isolation_and_internal_aliases():
    shared = ["original"]
    frozen = {"type": "StringInput", "default": {"left": shared, "right": shared}}
    observed = _argument_info(SimpleNamespace(_contract_argument_info=frozen))
    assert observed == frozen
    assert observed["default"]["left"] is observed["default"]["right"]
    assert observed["default"]["left"] is not shared
    observed["default"]["left"].append("changed")
    assert shared == ["original"]
