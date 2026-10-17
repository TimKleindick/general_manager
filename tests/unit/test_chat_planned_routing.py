"""The old public complexity selector is intentionally removed."""

from importlib.util import find_spec


def test_complexity_router_api_is_removed():
    assert find_spec("general_manager.chat.planned.routing") is None
