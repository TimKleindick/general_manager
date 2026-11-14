"""Expression evaluation preserves live callbacks and fragment boundaries."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser

from general_manager.permission.permission_checks import permission_functions
from general_manager.permission.utils import validate_permission_string


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("rule", [[]]),
        ("rule:", [[""]]),
        ("rule::value", [["", "value"]]),
        ("rule&&rule", [[], [], []]),
    ],
)
def test_expression_preserves_empty_fragments_and_config_segments(expression, expected):
    seen = []

    def allowed(_subject, _user, config):
        seen.append(config)
        return True

    with patch.dict(
        permission_functions,
        {name: {"permission_method": allowed} for name in ("rule", "")},
    ):
        assert validate_permission_string(expression, object(), AnonymousUser())
    assert seen == expected


def test_later_fragments_use_the_current_registry_callback():
    seen = []

    def current(*_args):
        seen.append("current")
        return True

    def first(*_args):
        seen.append("first")
        permission_functions["next"]["permission_method"] = current
        return True

    def obsolete(*_args):
        raise AssertionError

    with patch.dict(
        permission_functions,
        {
            "first": {"permission_method": first},
            "next": {"permission_method": obsolete},
        },
    ):
        assert validate_permission_string("first&next", object(), AnonymousUser())
    assert seen == ["first", "current"]


def test_non_boolean_denial_is_normalized_once_and_stops_unknown_fragment():
    conversions = []

    class Denial:
        def __bool__(self):
            conversions.append(None)
            return False

    with patch.dict(
        permission_functions, {"deny": {"permission_method": lambda *_: Denial()}}
    ):
        assert not validate_permission_string(
            "deny&missingPermission", object(), AnonymousUser()
        )
    assert len(conversions) == 1


def test_callback_error_is_propagated_without_wrapping():
    failure = RuntimeError("callback failed")

    def fail(*_args):
        raise failure

    with patch.dict(permission_functions, {"failure": {"permission_method": fail}}):
        with pytest.raises(RuntimeError) as observed:
            validate_permission_string(
                "failure&missingPermission", object(), AnonymousUser()
            )
    assert observed.value is failure


@pytest.mark.parametrize("phase", ["callback", "truth-value"])
def test_fragment_stop_iteration_keeps_the_previous_generator_error(phase):
    failure = StopIteration("fragment exhausted")

    class Decision:
        def __bool__(self):
            raise failure

    def check(*_args):
        if phase == "callback":
            raise failure
        return Decision()

    with patch.dict(permission_functions, {"check": {"permission_method": check}}):
        with pytest.raises(
            RuntimeError, match="generator raised StopIteration"
        ) as error:
            validate_permission_string("check", object(), AnonymousUser())
    assert error.value.__cause__ is failure


def test_expression_source_stop_iteration_is_not_changed():
    failure = StopIteration("expression exhausted")

    class Expression(str):
        def split(self, _separator):
            raise failure

    with pytest.raises(StopIteration) as error:
        validate_permission_string(Expression("check"), object(), AnonymousUser())
    assert error.value is failure
