from typing import Any, Optional
from factory.declarations import LazyFunction, LazyAttribute, LazyAttributeSequence
import random
from general_manager.measurement.measurement import Measurement
from datetime import timedelta, date, datetime
from faker import Faker
import uuid
from decimal import Decimal

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


def LazyDateToday() -> LazyFunction:
    return LazyFunction(lambda: date.today())


def LazyDateBetween(start_date: date, end_date: date) -> LazyAttribute:
    delta = (end_date - start_date).days
    return LazyAttribute(
        lambda obj: start_date + timedelta(days=random.randint(0, delta))
    )


def LazyDateTimeBetween(start: datetime, end: datetime) -> LazyAttribute:
    span = (end - start).total_seconds()
    return LazyAttribute(
        lambda obj: start + timedelta(seconds=random.randint(0, int(span)))
    )


def LazyInteger(min_value: int, max_value: int) -> LazyFunction:
    return LazyFunction(lambda: random.randint(min_value, max_value))


def LazyDecimal(min_value: float, max_value: float, precision: int = 2) -> LazyFunction:
    fmt = f"{{:.{precision}f}}"
    return LazyFunction(
        lambda: Decimal(fmt.format(random.uniform(min_value, max_value)))
    )


def LazyChoice(options: list[Any]) -> LazyFunction:
    return LazyFunction(lambda: random.choice(options))


def LazySequence(start: int = 0, step: int = 1) -> LazyAttributeSequence:
    return LazyAttributeSequence(lambda obj, n: start + n * step)


def LazyBoolean(trues_ratio: float = 0.5) -> LazyFunction:
    return LazyFunction(lambda: random.random() < trues_ratio)


def LazyUUID() -> LazyFunction:
    return LazyFunction(lambda: str(uuid.uuid4()))


def LazyFakerName() -> LazyFunction:
    return LazyFunction(lambda: fake.name())


def LazyFakerEmail(
    name: Optional[str] = None, domain: Optional[str] = None
) -> LazyFunction:
    if not name and not domain:
        return LazyFunction(lambda: fake.email(domain=domain))
    if not name:
        name = fake.name()
    if not domain:
        domain = fake.domain_name()
    return LazyFunction(lambda: name.replace(" ", "_") + "@" + domain)


def LazyFakerSentence(number_of_words: int = 6) -> LazyFunction:
    return LazyFunction(lambda: fake.sentence(nb_words=number_of_words))


def LazyFakerAddress() -> LazyFunction:
    return LazyFunction(lambda: fake.address())


def LazyFakerUrl() -> LazyFunction:
    return LazyFunction(lambda: fake.url())
