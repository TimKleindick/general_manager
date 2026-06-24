from __future__ import annotations

import json
from datetime import date, datetime, time
from unittest.mock import patch

import pytest

from general_manager.utils import json_encoder


class FakeGeneralManager:
    def __init__(self, identification: dict[str, object]) -> None:
        self.identification = identification


def test_serialize_datetime_date_time() -> None:
    dt = datetime(2021, 12, 31, 23, 59, 59)
    d = date(2020, 1, 1)
    t = time(8, 30)

    assert json.dumps(dt, cls=json_encoder.CustomJSONEncoder) == f'"{dt.isoformat()}"'
    assert json.dumps(d, cls=json_encoder.CustomJSONEncoder) == f'"{d.isoformat()}"'
    assert json.dumps(t, cls=json_encoder.CustomJSONEncoder) == f'"{t.isoformat()}"'


def test_serialize_nested_datetime_in_dict() -> None:
    dt = datetime(2022, 5, 10, 14, 0)
    data = {"timestamp": dt}
    result = json.dumps(data, cls=json_encoder.CustomJSONEncoder)

    assert '"timestamp": "2022-05-10T14:00:00"' in result


def test_serialize_general_manager() -> None:
    with patch.object(json_encoder, "GeneralManager", FakeGeneralManager):
        gm = FakeGeneralManager({"id": 123, "name": "Test"})
        dumped = json.dumps(gm, cls=json_encoder.CustomJSONEncoder)
        expected = f'"{gm.__class__.__name__}(**{gm.identification})"'
        assert dumped == expected


def test_standard_json_values_keep_standard_behavior() -> None:
    data = {"name": "Alpha", "count": 2, "active": True, "items": [1, None]}

    assert json.dumps(data, cls=json_encoder.CustomJSONEncoder) == json.dumps(data)


def test_fallback_to_str_on_unserializable() -> None:
    class Unserializable:
        def __str__(self) -> str:
            return "custom_str"

    dumped = json.dumps(Unserializable(), cls=json_encoder.CustomJSONEncoder)

    assert dumped == '"custom_str"'


def test_str_fallback_errors_propagate() -> None:
    class BrokenStringError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("broken string")

    class BrokenString:
        def __str__(self) -> str:
            raise BrokenStringError

    with pytest.raises(BrokenStringError, match="broken string"):
        json.dumps(BrokenString(), cls=json_encoder.CustomJSONEncoder)
