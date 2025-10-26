from django.test import SimpleTestCase
from general_manager.factory.factory_methods import (
    lazy_measurement,
    lazy_delta_date,
    lazy_project_name,
    lazy_date_today,
    lazy_date_between,
    lazy_date_time_between,
    lazy_integer,
    lazy_decimal,
    lazy_uuid,
    lazy_boolean,
    lazy_faker_name,
    lazy_faker_email,
    lazy_faker_sentence,
    lazy_faker_address,
    lazy_faker_url,
    lazy_choice,
    lazy_sequence,
)
from general_manager.measurement.measurement import Measurement
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace


class TestFactoryMethods(SimpleTestCase):
    def test__lazy_measurement(self):
        min_value = 10.0
        max_value = 30.5
        unit = "kilogram"
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                measurement = lazy_measurement(min_value, max_value, unit).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(measurement, Measurement)
                self.assertTrue(min_value <= float(measurement.magnitude) <= max_value)
                self.assertEqual(measurement.unit, unit)

    def test__lazy_delta_date(self):
        avg_delta_days = 5  # -> 2.5 to 7.5 days
        base_attribute = "start_date"
        obj = type("TestObject", (object,), {base_attribute: date(2023, 1, 1)})()
        for i in range(100):
            with self.subTest(run=i):
                delta_date = lazy_delta_date(avg_delta_days, base_attribute).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(delta_date, date)
                self.assertTrue(
                    date(2023, 1, 1) <= delta_date <= date(2023, 1, 8),
                    f"Run {i}: {delta_date} is not in the expected range.",
                )

    def test__lazy_project_name(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                project_name = lazy_project_name().evaluate(obj, 1, None)
                self.assertIsInstance(project_name, str)

    def test__lazy_date_today(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                date_today = lazy_date_today().evaluate(obj, 1, None)
                self.assertIsInstance(date_today, date)
                self.assertEqual(date_today, date.today())

    def test__lazy_date_between(self):
        start_date = date(2023, 1, 1)
        end_date = date(2023, 12, 31)
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                date_between = lazy_date_between(start_date, end_date).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(date_between, date)
                self.assertTrue(
                    start_date <= date_between <= end_date,
                    f"Run {i}: {date_between} is not in the expected range.",
                )

    def test__lazy_date_time_between(self):
        start = datetime(2023, 1, 1)
        end = datetime(2023, 12, 31)
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                datetime_between = lazy_date_time_between(start, end).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(datetime_between, datetime)
                self.assertTrue(
                    start <= datetime_between <= end,
                    f"Run {i}: {datetime_between} is not in the expected range.",
                )

    def test__lazy_integer(self):
        min_value = 1
        max_value = 100
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                integer_value = lazy_integer(min_value, max_value).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(integer_value, int)
                self.assertTrue(min_value <= integer_value <= max_value)

    def test__lazy_decimal(self):
        min_value = 1.0
        max_value = 100.0
        precision = 4
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                decimal_value = lazy_decimal(min_value, max_value, precision).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(decimal_value, Decimal)
                self.assertTrue(
                    Decimal(min_value) <= decimal_value <= Decimal(max_value)
                )
                decimal_str = format(decimal_value, "f")
                if "." in decimal_str:
                    self.assertEqual(len(decimal_str.split(".")[1]), precision)

    def test__lazy_uuid(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                uuid_value = lazy_uuid().evaluate(obj, 1, None)
                self.assertIsInstance(uuid_value, str)
                self.assertRegex(
                    uuid_value,
                    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                )

    def test__lazy_boolean(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                boolean_value = lazy_boolean().evaluate(obj, 1, None)
                self.assertIsInstance(boolean_value, bool)

    def test__lazy_faker_name(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                name_value = lazy_faker_name().evaluate(obj, 1, None)
                self.assertIsInstance(name_value, str)
                self.assertTrue(len(name_value) > 0)

    def test__lazy_faker_email(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                email_value = lazy_faker_email().evaluate(obj, 1, None)
                self.assertIsInstance(email_value, str)
                self.assertRegex(email_value, r"^[\w\.-]+@[\w\.-]+\.\w+$")

    def test__lazy_faker_email_with_presets(self):
        obj = type("TestObject", (object,), {})()
        names = [
            "John Doe",
            "Jane Smith",
            "Alice Johnson",
            "Bob Brown",
            "Charlie Davis Jr.",
            None,
            None,
        ]
        domains = ["example.com", None]
        for i, name in enumerate(names):
            domain = domains[i % len(domains)]
            with self.subTest(run=i):
                email_value = lazy_faker_email(name, domain).evaluate(obj, 1, None)
                self.assertIsInstance(email_value, str)
                self.assertRegex(email_value, r"^[\w\.-]+@[\w\.-]+\.\w+$")

    def test__lazy_faker_sentence(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                sentence_value = lazy_faker_sentence().evaluate(obj, 1, None)
                self.assertIsInstance(sentence_value, str)
                self.assertTrue(len(sentence_value) > 0)

    def test__lazy_faker_address(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                address_value = lazy_faker_address().evaluate(obj, 1, None)
                self.assertIsInstance(address_value, str)
                self.assertTrue(len(address_value) > 0)

    def test__lazy_faker_url(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                url_value = lazy_faker_url().evaluate(obj, 1, None)
                self.assertIsInstance(url_value, str)
                self.assertRegex(url_value, r"^https?://[^\s/$.?#].[^\s]*$")
                self.assertTrue(len(url_value) > 0)

    def test__lazy_choice(self):
        options = ["option1", "option2", "option3"]
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                choice_value = lazy_choice(options).evaluate(obj, 1, None)
                self.assertIn(choice_value, options)
                self.assertIsInstance(choice_value, str)
                self.assertTrue(len(choice_value) > 0)

    def test__lazy_sequence(self):
        start = 0
        step = 2
        obj = type("TestObject", (object,), {})()

        for i in range(100):
            with self.subTest(run=i):
                context = SimpleNamespace(sequence=i)
                sequence_value = lazy_sequence(start, step).evaluate(obj, context, None)
                self.assertEqual(sequence_value, start + i * step)
                self.assertIsInstance(sequence_value, int)
