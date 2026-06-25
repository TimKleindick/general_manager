"""Convenience helpers for defining factory_boy lazy attributes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from random import SystemRandom
from typing import TypeVar, cast
import uuid

from factory.declarations import LazyAttribute, LazyAttributeSequence, LazyFunction
from faker import Faker

from general_manager.measurement.measurement import Measurement

fake = Faker()
_RNG = SystemRandom()
_ChoiceT = TypeVar("_ChoiceT")

_LazyFunctionConstructor = cast(
    Callable[[Callable[[], object]], LazyFunction],
    LazyFunction,
)
_LazyAttributeConstructor = cast(
    Callable[[Callable[[object], object]], LazyAttribute],
    LazyAttribute,
)
_LazyAttributeSequenceConstructor = cast(
    Callable[[Callable[[object, int], object]], LazyAttributeSequence],
    LazyAttributeSequence,
)

_AVG_DELTA_DAYS_ERROR = "avg_delta_days must be >= 0"
_EMPTY_OPTIONS_ERROR = "options must be a non-empty sequence"
_NEGATIVE_PRECISION_ERROR = "precision must be >= 0"
_NUMERIC_RANGE_ERROR = "min_value must be <= max_value"
_TRUES_RATIO_ERROR = "trues_ratio must be between 0 and 1"


def _lazy_function(callback: Callable[[], object]) -> LazyFunction:
    """Create a typed LazyFunction declaration from an untyped factory_boy API."""
    return _LazyFunctionConstructor(callback)


def _lazy_attribute(callback: Callable[[object], object]) -> LazyAttribute:
    """Create a typed LazyAttribute declaration from an untyped factory_boy API."""
    return _LazyAttributeConstructor(callback)


def _lazy_sequence(
    callback: Callable[[object, int], object],
) -> LazyAttributeSequence:
    """Create a typed LazyAttributeSequence declaration from an untyped API."""
    return _LazyAttributeSequenceConstructor(callback)


def _ensure_numeric_range(min_value: int | float, max_value: int | float) -> None:
    """Validate inclusive numeric bounds for public helper inputs."""
    if min_value > max_value:
        raise ValueError(_NUMERIC_RANGE_ERROR)


def lazy_measurement(
    min_value: int | float, max_value: int | float, unit: str
) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a `Measurement`.

    The evaluated value is a `general_manager.measurement.measurement.Measurement`
    with a magnitude sampled uniformly between the supplied inclusive bounds. The
    sampled number is formatted as a six-decimal string before it is passed to
    `Measurement`, so the resulting `Measurement.magnitude` follows
    `Measurement`'s normal Decimal conversion rules.

    Parameters:
        min_value: Lower bound for the sampled magnitude.
        max_value: Upper bound for the sampled magnitude.
        unit: Unit string passed directly to `Measurement`.

    Returns:
        A `factory.declarations.LazyFunction` declaration.

    Raises:
        ValueError: If `min_value > max_value`.
        Exception: Errors from `Measurement` can be raised during evaluation
            when the sampled magnitude or unit is not accepted by the
            measurement layer.
    """
    _ensure_numeric_range(min_value, max_value)
    return _lazy_function(
        lambda: Measurement(f"{_RNG.uniform(min_value, max_value):.6f}", unit)
    )


def lazy_delta_date(avg_delta_days: int, base_attribute: str) -> LazyAttribute:
    """
    Return a lazy declaration that offsets another generated date-like value.

    During evaluation, the declaration reads `base_attribute` from the generated
    object. If the attribute is missing or falsey, `date.today()` is evaluated
    and used as the base. Truthy base values must support adding a
    `datetime.timedelta`, such as `date` or `datetime`; invalid truthy values
    raise their normal `TypeError` during evaluation.

    Parameters:
        avg_delta_days: Average number of days for the offset. The actual offset
            is an integer chosen uniformly between `avg_delta_days // 2` and
            `avg_delta_days * 3 // 2`, inclusive.
        base_attribute: Name of the generated object's base date/datetime
            attribute.

    Returns:
        A `factory.declarations.LazyAttribute` declaration. It evaluates to the
        base value plus the random day offset.

    Raises:
        ValueError: If `avg_delta_days` is negative.
    """
    if avg_delta_days < 0:
        raise ValueError(_AVG_DELTA_DAYS_ERROR)
    return _lazy_attribute(
        lambda instance: (
            (getattr(instance, base_attribute) or date.today())
            + timedelta(days=_RNG.randint(avg_delta_days // 2, avg_delta_days * 3 // 2))
        )
    )


def lazy_project_name() -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a pseudo-random project name.

    The value uses the module-level default-locale Faker instance plus a random
    suffix. No deterministic seed is set by this helper.
    """
    return _lazy_function(
        lambda: (
            f"{fake.word().capitalize()} "
            f"{fake.word().capitalize()} "
            f"{fake.random_element(elements=('X', 'Z', 'G'))}"
            f"-{fake.random_int(min=1, max=1000)}"
        )
    )


def lazy_date_today() -> LazyFunction:
    """
    Return a lazy declaration that evaluates to `date.today()`.

    The date is read from Python's local system date at declaration evaluation
    time, not when the helper is called.
    """
    return _lazy_function(lambda: date.today())


def lazy_date_between(start_date: date, end_date: date) -> LazyAttribute:
    """
    Return a lazy declaration that evaluates to a random date in a range.

    The date is chosen uniformly at evaluation time from the inclusive day
    range. If `start_date` is after `end_date`, the endpoints are swapped before
    choosing the value.

    Parameters:
        start_date: Start of the inclusive date range.
        end_date: End of the inclusive date range.

    Returns:
        A `factory.declarations.LazyAttribute` declaration that evaluates to a
        `date` between the normalized endpoints, inclusive.
    """
    delta = (end_date - start_date).days
    if delta < 0:
        start_date, end_date = end_date, start_date
        delta = -delta
    return _lazy_attribute(
        lambda _: start_date + timedelta(days=_RNG.randint(0, delta))
    )


def lazy_date_time_between(start: datetime, end: datetime) -> LazyAttribute:
    """
    Return a lazy declaration that evaluates to a random datetime in a range.

    The datetime is chosen uniformly at evaluation time with whole-second
    granularity. If `start` is after `end`, the endpoints are swapped before
    choosing the value. Python's normal datetime subtraction rules apply, so
    mixed naive/aware inputs raise `TypeError`.

    Parameters:
        start: Start of the inclusive datetime range.
        end: End of the inclusive datetime range.

    Returns:
        A `factory.declarations.LazyAttribute` declaration that evaluates to a
        `datetime` between the normalized endpoints, inclusive.
    """
    span = (end - start).total_seconds()
    if span < 0:
        start, end = end, start
        span = -span
    return _lazy_attribute(
        lambda _: start + timedelta(seconds=_RNG.randint(0, int(span)))
    )


def lazy_integer(min_value: int, max_value: int) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a random integer.

    Parameters:
        min_value: Inclusive lower bound.
        max_value: Inclusive upper bound.

    Returns:
        A `factory.declarations.LazyFunction` declaration that evaluates to an
        integer selected uniformly between `min_value` and `max_value`,
        inclusive.

    Raises:
        ValueError: If `min_value > max_value`.
    """
    _ensure_numeric_range(min_value, max_value)
    return _lazy_function(lambda: _RNG.randint(min_value, max_value))


def lazy_decimal(
    min_value: float, max_value: float, precision: int = 2
) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a random `Decimal`.

    The sampled float is drawn uniformly between the supplied inclusive bounds,
    formatted with exactly `precision` decimal places, and then converted
    through `Decimal(str_value)`.

    Parameters:
        min_value: Lower bound of the generated value.
        max_value: Upper bound of the generated value.
        precision: Number of decimal places in the formatted value.

    Returns:
        A `factory.declarations.LazyFunction` declaration that evaluates to a
        `Decimal`.

    Raises:
        ValueError: If `min_value > max_value`.
        ValueError: If `precision` is negative.
    """
    _ensure_numeric_range(min_value, max_value)
    if precision < 0:
        raise ValueError(_NEGATIVE_PRECISION_ERROR)
    fmt = f"{{:.{precision}f}}"
    return _lazy_function(
        lambda: Decimal(fmt.format(_RNG.uniform(min_value, max_value)))
    )


def lazy_choice(options: Sequence[_ChoiceT]) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to one random option.

    The options are snapshotted as a tuple when this helper is called. Later
    mutations to the caller's sequence do not affect generated values.

    Parameters:
        options: Non-empty candidate values to choose from.

    Returns:
        A `factory.declarations.LazyFunction` declaration that evaluates to one
        element selected uniformly from the declaration-time options snapshot.

    Raises:
        ValueError: If `options` is empty.
    """
    if not options:
        raise ValueError(_EMPTY_OPTIONS_ERROR)
    choices = tuple(options)
    return _lazy_function(lambda: _RNG.choice(choices))


def lazy_sequence(start: int = 0, step: int = 1) -> LazyAttributeSequence:
    """
    Return a sequence declaration that evaluates to successive integers.

    Each evaluated value equals `start + index * step`, where `index` is the
    zero-based factory_boy sequence counter for that attribute.

    Parameters:
        start: Initial value of the sequence.
        step: Increment between successive values.

    Returns:
        A `factory.declarations.LazyAttributeSequence` declaration.
    """
    return _lazy_sequence(lambda _instance, index: start + index * step)


def lazy_boolean(trues_ratio: float = 0.5) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a random boolean.

    Parameters:
        trues_ratio: Probability that the generated value is `True`. Must be in
            the inclusive `[0, 1]` interval.

    Returns:
        A `factory.declarations.LazyFunction` declaration that evaluates to a
        boolean by comparing `_RNG.random() < trues_ratio`.

    Raises:
        ValueError: If `trues_ratio` is outside the inclusive `[0, 1]` interval.
    """
    if not 0 <= trues_ratio <= 1:
        raise ValueError(_TRUES_RATIO_ERROR)
    return _lazy_function(lambda: _RNG.random() < trues_ratio)


def lazy_uuid() -> LazyFunction:
    """
    Return a lazy declaration that evaluates to an RFC 4122 version 4 UUID string.

    Returns:
        A `factory.declarations.LazyFunction` declaration that evaluates to a
        UUID4 string in standard 36-character representation.
    """
    return _lazy_function(lambda: str(uuid.uuid4()))


def lazy_faker_name() -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a Faker-generated name.

    Uses the module-level default-locale Faker instance. No deterministic seed is
    set by this helper.
    """
    return _lazy_function(lambda: fake.name())


def lazy_faker_email(
    name: str | None = None, domain: str | None = None
) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to an email address.

    When neither override is supplied, Faker generates the full email address.
    When `name` or `domain` is supplied, missing pieces are generated once when
    this helper is called. The name part is converted by replacing spaces with
    underscores, and `domain` should be a hostname without a leading `@`.
    """
    if not name and not domain:
        return _lazy_function(lambda: fake.email(domain=domain))
    if not name:
        name = fake.name()
    if not domain:
        domain = fake.domain_name()
    return _lazy_function(lambda: name.replace(" ", "_") + "@" + domain)


def lazy_faker_sentence(number_of_words: int = 6) -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a Faker-generated sentence.

    Uses the module-level default-locale Faker instance and requests
    `number_of_words` words from Faker at evaluation time.
    """
    return _lazy_function(lambda: fake.sentence(nb_words=number_of_words))


def lazy_faker_address() -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a Faker-generated postal address.

    Uses the module-level default-locale Faker instance.
    """
    return _lazy_function(lambda: fake.address())


def lazy_faker_url() -> LazyFunction:
    """
    Return a lazy declaration that evaluates to a Faker-generated URL.

    Uses the module-level default-locale Faker instance.
    """
    return _lazy_function(lambda: fake.url())
