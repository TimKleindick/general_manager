"""Convenience helpers for defining factory_boy lazy attributes."""

from typing import Any, Optional

from factory.declarations import LazyAttribute, LazyAttributeSequence, LazyFunction
from datetime import date, datetime, timedelta
from decimal import Decimal
from faker import Faker
from general_manager.measurement.measurement import Measurement
from random import SystemRandom
import uuid

fake = Faker()
_RNG = SystemRandom()

_AVG_DELTA_DAYS_ERROR = "avg_delta_days must be >= 0"
_EMPTY_OPTIONS_ERROR = "options must be a non-empty list"


def lazy_measurement(
    min_value: int | float, max_value: int | float, unit: str
) -> LazyFunction:
    """
    Create a lazy factory that produces Measurement values with a numeric magnitude sampled between the given bounds and the specified unit.

    Parameters:
        min_value (int | float): Lower bound (inclusive) for the sampled magnitude.
        max_value (int | float): Upper bound (inclusive) for the sampled magnitude.
        unit (str): Unit string to attach to the Measurement.

    Returns:
        LazyFunction: A factory that yields a Measurement whose numeric value is drawn uniformly between min_value and max_value (formatted to six decimal places) and uses the provided unit.
    """
    return LazyFunction(
        lambda: Measurement(f"{_RNG.uniform(min_value, max_value):.6f}", unit)
    )


def lazy_delta_date(avg_delta_days: int, base_attribute: str) -> LazyAttribute:
    """
    Compute a date by offsetting an instance's base date attribute by a randomized number of days.

    Parameters:
        avg_delta_days (int): Average number of days for the offset; the actual offset is randomly chosen
            between floor(avg_delta_days / 2) and floor(avg_delta_days * 3 / 2), inclusive.
        base_attribute (str): Name of the attribute on the instance that provides the base date. If that
            attribute is missing or evaluates to false, today's date is used as the base.

    Returns:
        date: The base date shifted by the randomly chosen number of days.

    Raises:
        ValueError: If avg_delta_days is negative.
    """
    if avg_delta_days < 0:
        raise ValueError(_AVG_DELTA_DAYS_ERROR)
    return LazyAttribute(
        lambda instance: (getattr(instance, base_attribute) or date.today())
        + timedelta(days=_RNG.randint(avg_delta_days // 2, avg_delta_days * 3 // 2))
    )


def lazy_project_name() -> LazyFunction:
    """Return a lazy factory producing a pseudo-random project-style name."""
    return LazyFunction(
        lambda: (
            f"{fake.word().capitalize()} "
            f"{fake.word().capitalize()} "
            f"{fake.random_element(elements=('X', 'Z', 'G'))}"
            f"-{fake.random_int(min=1, max=1000)}"
        )
    )


def lazy_date_today() -> LazyFunction:
    """Return a lazy factory that yields today's date."""
    return LazyFunction(lambda: date.today())


def lazy_date_between(start_date: date, end_date: date) -> LazyAttribute:
    """
    Produce a lazy attribute that yields a date between two given dates (inclusive).

    Parameters:
        start_date (date): The start of the date range. If later than end_date, the range will be corrected.
        end_date (date): The end of the date range. If earlier than start_date, the range will be corrected.

    Returns:
        date: A date between start_date and end_date, inclusive.
    """
    delta = (end_date - start_date).days
    if delta < 0:
        start_date, end_date = end_date, start_date
        delta = -delta
    return LazyAttribute(lambda _: start_date + timedelta(days=_RNG.randint(0, delta)))


def lazy_date_time_between(start: datetime, end: datetime) -> LazyAttribute:
    """
    Produce a lazy attribute that yields a datetime within the inclusive range defined by `start` and `end`.

    If `start` is after `end`, the two endpoints are swapped before selecting a value.

    Parameters:
        start (datetime): The start of the datetime range.
        end (datetime): The end of the datetime range.

    Returns:
        LazyAttribute: A lazy attribute that produces a `datetime` between `start` and `end` (inclusive).
    """
    span = (end - start).total_seconds()
    if span < 0:
        start, end = end, start
        span = -span
    return LazyAttribute(
        lambda _: start + timedelta(seconds=_RNG.randint(0, int(span)))
    )


def lazy_integer(min_value: int, max_value: int) -> LazyFunction:
    """
    Return a lazy factory that produces an integer within the provided bounds.

    Parameters:
        min_value (int): Lower bound (inclusive) for generated integers.
        max_value (int): Upper bound (inclusive) for generated integers.

    Returns:
        int: A random integer greater than or equal to min_value and less than or equal to max_value.
    """
    return LazyFunction(lambda: _RNG.randint(min_value, max_value))


def lazy_decimal(
    min_value: float, max_value: float, precision: int = 2
) -> LazyFunction:
    """
    Create a lazy factory that produces Decimal values between min_value and max_value, rounded to the specified precision.

    Parameters:
        min_value (float): Lower bound of the generated value.
        max_value (float): Upper bound of the generated value.
        precision (int): Number of decimal places to round the generated value to.

    Returns:
        Decimal: A Decimal value between min_value and max_value (inclusive), rounded to `precision` decimal places.
    """
    fmt = f"{{:.{precision}f}}"
    return LazyFunction(lambda: Decimal(fmt.format(_RNG.uniform(min_value, max_value))))


def lazy_choice(options: list[Any]) -> LazyFunction:
    """
    Create a lazy factory that selects a random element from the provided options.

    Parameters:
        options (list[Any]): Candidate values to choose from.

    Returns:
        Any: One element randomly chosen from `options`.
    """
    if not options:
        raise ValueError(_EMPTY_OPTIONS_ERROR)
    return LazyFunction(lambda: _RNG.choice(options))


def lazy_sequence(start: int = 0, step: int = 1) -> LazyAttributeSequence:
    """
    Produce a sequence attribute that yields successive integer values.

    Each produced value equals start + index * step where index is the zero-based position in the sequence.

    Parameters:
        start (int): Initial value of the sequence.
        step (int): Increment between successive values.

    Returns:
        LazyAttributeSequence: An attribute sequence that yields integers as described.
    """
    return LazyAttributeSequence(lambda _instance, index: start + index * step)


def lazy_boolean(trues_ratio: float = 0.5) -> LazyFunction:
    """
    Return booleans where each value is True with the specified probability.

    Parameters:
        trues_ratio (float): Probability that the generated value is True; expected between 0 and 1.

    Returns:
        bool: `True` with probability `trues_ratio`, `False` otherwise.
    """
    return LazyFunction(lambda: _RNG.random() < trues_ratio)


def lazy_uuid() -> LazyFunction:
    """
    Create a lazy factory that yields RFC 4122 version 4 UUID strings.

    Returns:
        uuid_str (str): A UUID4 string in standard 36-character representation.
    """
    return LazyFunction(lambda: str(uuid.uuid4()))


def lazy_faker_name() -> LazyFunction:
    """Return a lazy factory producing names using Faker."""
    return LazyFunction(lambda: fake.name())


def lazy_faker_email(
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


def lazy_faker_sentence(number_of_words: int = 6) -> LazyFunction:
    """Return a lazy factory producing fake sentences."""
    return LazyFunction(lambda: fake.sentence(nb_words=number_of_words))


def lazy_faker_address() -> LazyFunction:
    """Return a lazy factory producing fake postal addresses."""
    return LazyFunction(lambda: fake.address())


def lazy_faker_url() -> LazyFunction:
    """Return a lazy factory producing fake URLs."""
    return LazyFunction(lambda: fake.url())
