from typing import Any
from factory.declarations import LazyFunction, LazyAttribute, LazyAttributeSequence
import random
from general_manager.measurement.measurement import Measurement
from datetime import timedelta, date
from faker import Faker

fake = Faker()


def LazyMeasurement(
    min_value: int | float, max_value: int | float, unit: str
) -> LazyFunction:
    return LazyFunction(
        lambda: Measurement(str(random.uniform(min_value, max_value))[:10], unit)
    )


def LazyDeltaDate(avg_delta_days: int, base_attribute: str) -> LazyAttribute:
    return LazyAttribute(
        lambda obj: (getattr(obj, base_attribute) or date.today())
        + timedelta(days=random.randint(avg_delta_days // 2, avg_delta_days * 3 // 2))
    )


def LazyProjectName() -> LazyFunction:
    return LazyFunction(
        lambda: (
            f"{fake.word().capitalize()} "
            f"{fake.word().capitalize()} "
            f"{fake.random_element(elements=('X', 'Z', 'G'))}"
            f"-{fake.random_int(min=1, max=1000)}"
        )
    )


def LazySapNumber() -> LazyAttributeSequence:
    return LazyAttributeSequence(lambda obj, n: f"60{n:04d}")
