"""Convenience helpers for defining factory_boy lazy attributes."""

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
    """
    Return a lazy factory producing ``Measurement`` values in the given range.

    Parameters:
        min_value (int | float): Minimum magnitude.
        max_value (int | float): Maximum magnitude.
        unit (str): Measurement unit.
    """
    return LazyFunction(
        lambda: Measurement(f"{random.uniform(min_value, max_value):.6f}", unit)
    )


def LazyDeltaDate(avg_delta_days: int, base_attribute: str) -> LazyAttribute:
    """Return a lazy attribute that offsets a base date by a random delta.

    Parameters:
        avg_delta_days (int): Average number of days to offset.
        base_attribute (str): Name of the attribute providing the base date.
    """
    return LazyAttribute(
        lambda obj: (getattr(obj, base_attribute) or date.today())
        + timedelta(days=random.randint(avg_delta_days // 2, avg_delta_days * 3 // 2))
    )


def LazyProjectName() -> LazyFunction:
    """Return a lazy factory producing a pseudo-random project-style name."""
    return LazyFunction(
        lambda: (
            f"{fake.word().capitalize()} "
            f"{fake.word().capitalize()} "
            f"{fake.random_element(elements=('X', 'Z', 'G'))}"
            f"-{fake.random_int(min=1, max=1000)}"
        )
    )


def LazyDateToday() -> LazyFunction:
    """Return a lazy factory that yields today's date."""
    return LazyFunction(lambda: date.today())


def LazyDateBetween(start_date: date, end_date: date) -> LazyAttribute:
    """Return a lazy attribute producing dates within the supplied range."""
    delta = (end_date - start_date).days
    if delta < 0:
        start_date, end_date = end_date, start_date
        delta = -delta
    return LazyAttribute(
        lambda obj: start_date + timedelta(days=random.randint(0, delta))
    )


def LazyDateTimeBetween(start: datetime, end: datetime) -> LazyAttribute:
    """Return a lazy attribute producing datetimes within the supplied range."""
    span = (end - start).total_seconds()
    if span < 0:
        start, end = end, start
        span = -span
    return LazyAttribute(
        lambda obj: start + timedelta(seconds=random.randint(0, int(span)))
    )


def LazyInteger(min_value: int, max_value: int) -> LazyFunction:
    """Return a lazy factory yielding random integers within the bounds."""
    return LazyFunction(lambda: random.randint(min_value, max_value))


def LazyDecimal(min_value: float, max_value: float, precision: int = 2) -> LazyFunction:
    """Return a lazy factory yielding Decimal values within the bounds."""
    fmt = f"{{:.{precision}f}}"
    return LazyFunction(
        lambda: Decimal(fmt.format(random.uniform(min_value, max_value)))
    )


def LazyChoice(options: list[Any]) -> LazyFunction:
    """Return a lazy factory selecting a random element from the options."""
    return LazyFunction(lambda: random.choice(options))


def LazySequence(start: int = 0, step: int = 1) -> LazyAttributeSequence:
    """Return a lazy attribute sequence starting at ``start`` with ``step`` increments."""
    return LazyAttributeSequence(lambda obj, n: start + n * step)


def LazyBoolean(trues_ratio: float = 0.5) -> LazyFunction:
    """Return a lazy factory yielding booleans with the given true ratio."""
    return LazyFunction(lambda: random.random() < trues_ratio)


def LazyUUID() -> LazyFunction:
    """Return a lazy factory producing UUID4 strings."""
    return LazyFunction(lambda: str(uuid.uuid4()))


def LazyFakerName() -> LazyFunction:
    """Return a lazy factory producing names using Faker."""
    return LazyFunction(lambda: fake.name())


def LazyFakerEmail(
    name: Optional[str] = None, domain: Optional[str] = None
) -> LazyFunction:
    """Return a lazy factory producing email addresses with optional overrides."""
    if not name and not domain:
        return LazyFunction(lambda: fake.email(domain=domain))
    if not name:
        name = fake.name()
    if not domain:
        domain = fake.domain_name()
    return LazyFunction(lambda: name.replace(" ", "_") + "@" + domain)


def LazyFakerSentence(number_of_words: int = 6) -> LazyFunction:
    """Return a lazy factory producing fake sentences."""
    return LazyFunction(lambda: fake.sentence(nb_words=number_of_words))


def LazyFakerAddress() -> LazyFunction:
    """Return a lazy factory producing fake postal addresses."""
    return LazyFunction(lambda: fake.address())


def LazyFakerUrl() -> LazyFunction:
    """Return a lazy factory producing fake URLs."""
    return LazyFunction(lambda: fake.url())
