from django.test import SimpleTestCase
from datetime import datetime, date, time
import json
from unittest.mock import patch

# Ensure the custom encoder module path is correct
from general_manager.auxiliary import jsonEncoder


class FakeGeneralManager:
    def __init__(self, identification):
        self.identification = identification


class CustomJSONEncoderTests(SimpleTestCase):
    def setUp(self):
        self.encoder_cls = jsonEncoder.CustomJSONEncoder
        self.FakeGeneralManager = FakeGeneralManager

    def test_serialize_datetime_date_time(self):
        dt = datetime(2021, 12, 31, 23, 59, 59)
        d = date(2020, 1, 1)
        t = time(8, 30)

        self.assertEqual(json.dumps(dt, cls=self.encoder_cls), f'"{dt.isoformat()}"')
        self.assertEqual(json.dumps(d, cls=self.encoder_cls), f'"{d.isoformat()}"')
        self.assertEqual(json.dumps(t, cls=self.encoder_cls), f'"{t.isoformat()}"')

    def test_serialize_nested_datetime_in_dict(self):
        dt = datetime(2022, 5, 10, 14, 0)
        data = {"timestamp": dt}
        result = json.dumps(data, cls=self.encoder_cls)
        self.assertIn('"timestamp": "2022-05-10T14:00:00"', result)

    def test_serialize_general_manager(self):
        with patch.object(jsonEncoder, "GeneralManager", FakeGeneralManager):
            gm = self.FakeGeneralManager({"id": 123, "name": "Test"})
            dumped = json.dumps(gm, cls=self.encoder_cls)
            expected = f'"{gm.__class__.__name__}(**{gm.identification})"'
            self.assertEqual(dumped, expected)

    def test_fallback_to_str_on_unserializable(self):
        class Unserializable:
            def __str__(self):
                return "custom_str"

        obj = Unserializable()
        dumped = json.dumps(obj, cls=self.encoder_cls)
        self.assertEqual(dumped, '"custom_str"')
