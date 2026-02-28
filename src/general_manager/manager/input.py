"""Input field metadata used by GeneralManager interfaces."""

from __future__ import annotations

import calendar
import builtins
import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from general_manager.bucket.base_bucket import Bucket


INPUT_TYPE = TypeVar("INPUT_TYPE", bound=type)
VALUE_TYPE = TypeVar("VALUE_TYPE")

type PossibleValues = (
    "InputDomain[Any]"
    | "Iterable[Any]"
    | "Bucket[Any]"
    | "Callable[..., InputDomain[Any] | Iterable[Any] | Bucket[Any]]"
)
type ScalarConstraint = date | datetime | int | float | Decimal
type Validator = Callable[..., bool | None]
type Normalizer = Callable[..., Any]


class DomainIterationError(TypeError):
    """Raised when code attempts to eagerly iterate a non-iterable domain."""

    def __init__(self, domain_name: str) -> None:
        """
        Initialize the DomainIterationError for an attempted eager iteration on a non-iterable domain.
        
        Parameters:
            domain_name (str): Name or identifier of the domain that does not support eager iteration; included in the exception message.
        """
        super().__init__(f"{domain_name} does not provide eager iteration.")


class InvalidNumericRangeError(ValueError):
    """Raised when a numeric range domain is configured with invalid bounds."""

    def __init__(self, reason: str) -> None:
        """
        Initialize the exception with a message corresponding to the numeric range configuration error.
        
        Parameters:
            reason (str): One of `"step"` or `"bounds"`. `"step"` indicates a non-positive step value; `"bounds"` indicates min_value > max_value. The chosen reason determines the exception message.
        """
        messages = {
            "step": "NumericRangeDomain step must be greater than zero.",
            "bounds": "NumericRangeDomain min_value must be <= max_value.",
        }
        super().__init__(messages[reason])


class InvalidDateRangeError(ValueError):
    """Raised when a date range domain is configured with invalid bounds."""

    def __init__(self, reason: str, *, frequency: str | None = None) -> None:
        """
        Initialize the InvalidDateRangeError with a clear message for the given validation failure reason.
        
        Parameters:
        	reason (str): One of "step", "bounds", or "frequency" indicating the validation failure.
        	frequency (str | None): Frequency value included in the error message when `reason` is "frequency"; ignored otherwise.
        """
        messages = {
            "step": "DateRangeDomain step must be greater than zero.",
            "bounds": "DateRangeDomain start must be <= end.",
        }
        if reason == "frequency":
            super().__init__(f"Unsupported date frequency: {frequency}.")
            return
        super().__init__(messages[reason])


def _invoke_callable(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """
    Call `func` with only the positional and keyword arguments its signature accepts.
    
    Inspects `func`'s signature and forwards matching values from `args` and `kwargs`; extra positional or keyword arguments are discarded unless `func` accepts `*args` or `**kwargs`.
    
    Parameters:
        func (Callable[..., Any]): The callable to invoke.
        *args (Any): Positional arguments to consider for forwarding to `func`.
        **kwargs (Any): Keyword arguments to consider for forwarding to `func`.
    
    Returns:
        Any: The value returned by `func` when invoked with the filtered arguments.
    """

    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
    accepts_var_positional = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters
    )
    accepts_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )

    positional_args = list(args)
    bound_args: list[Any] = []
    bound_kwargs: dict[str, Any] = {}
    remaining_kwargs = dict(kwargs)

    for parameter in parameters:
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            if positional_args:
                bound_args.append(positional_args.pop(0))
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD:
            if positional_args:
                bound_args.append(positional_args.pop(0))
                continue
            if parameter.name in remaining_kwargs:
                bound_kwargs[parameter.name] = remaining_kwargs.pop(parameter.name)
            continue
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            if parameter.name in remaining_kwargs:
                bound_kwargs[parameter.name] = remaining_kwargs.pop(parameter.name)
            continue
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            bound_args.extend(positional_args)
            positional_args.clear()
            continue
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            bound_kwargs.update(remaining_kwargs)
            remaining_kwargs.clear()

    if accepts_var_positional and positional_args:
        bound_args.extend(positional_args)
    if accepts_var_keyword and remaining_kwargs:
        bound_kwargs.update(remaining_kwargs)

    return func(*bound_args, **bound_kwargs)


def _month_start(value: date) -> date:
    """
    Return the first day of the month for the given date.
    
    Parameters:
        value (date): The input date.
    
    Returns:
        date: A date with the same year and month as `value` and day set to 1.
    """
    return value.replace(day=1)


def _month_end(value: date) -> date:
    """
    Return the last calendar day of the month for the given date.
    
    Parameters:
        value (date): A date within the target month.
    
    Returns:
        date: A date with the same year and month as `value` and the day set to that month's final day.
    """
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _year_start(value: date) -> date:
    """
    Return the first day of the year for the given date.
    
    Parameters:
        value (date): Input date.
    
    Returns:
        date: A date representing January 1st of the same year as `value`.
    """
    return value.replace(month=1, day=1)


def _year_end(value: date) -> date:
    """
    Compute the last calendar day of the year for a given date.
    
    Returns:
        A `date` representing December 31 of the same year as `value`.
    """
    return value.replace(month=12, day=31)


def _week_end(value: date) -> date:
    """
    Return the date corresponding to the Sunday on or after the given date.
    
    Parameters:
        value (date): The input date.
    
    Returns:
        date: The Sunday that is the same day or the next Sunday after `value`.
    """
    days_until_sunday = (6 - value.weekday()) % 7
    return value.fromordinal(value.toordinal() + days_until_sunday)


def _quarter_end(value: date) -> date:
    """
    Return the last calendar date of the quarter containing the given date.
    
    Parameters:
        value (date): A date within the quarter to evaluate.
    
    Returns:
        date: A date set to the final day of the quarter that contains `value`.
    """
    quarter_end_month = ((value.month - 1) // 3 + 1) * 3
    last_day = calendar.monthrange(value.year, quarter_end_month)[1]
    return value.replace(month=quarter_end_month, day=last_day)


def _add_months(value: date, months: int) -> date:
    """
    Add a number of months to a date, adjusting the day to the last valid day of the resulting month when necessary.
    
    Parameters:
        value (date): The starting date.
        months (int): Number of months to add (may be negative).
    
    Returns:
        date: A new date moved by `months` months with the day clamped to the last day of the target month if the original day does not exist there.
    """
    absolute_month = (value.year * 12 + (value.month - 1)) + months
    year = absolute_month // 12
    month = absolute_month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def _add_numeric_step(
    current: int | float | Decimal,
    step: int | float | Decimal,
) -> int | float | Decimal:
    """
    Add a numeric step to a current value, preserving Decimal arithmetic when either operand is a Decimal.
    
    Parameters:
        current (int | float | Decimal): The current numeric value.
        step (int | float | Decimal): The step to add to the current value.
    
    Returns:
        int | float | Decimal: The sum of `current` and `step`. If either argument is a `Decimal`, the result is a `Decimal`; otherwise the result follows normal Python numeric addition.
    """
    if isinstance(current, Decimal) or isinstance(step, Decimal):
        return Decimal(str(current)) + Decimal(str(step))
    return current + step


@dataclass(frozen=True)
class InputDomain(Generic[VALUE_TYPE]):
    """Structured description of an input domain."""

    kind: str

    def contains(self, value: VALUE_TYPE) -> bool:
        """
        Determine whether the given value is a member of this domain.
        
        If a membership test raises a TypeError (for example, due to incompatible types), the value is treated as not contained.
        
        Returns:
            `true` if the value is contained in the domain, `false` otherwise.
        """
        try:
            return value in self
        except TypeError:
            return False

    def normalize(self, value: VALUE_TYPE) -> VALUE_TYPE:
        """
        Return a canonicalized representation of the given value.
        
        Parameters:
            value (VALUE_TYPE): The input value to normalize.
        
        Returns:
            VALUE_TYPE: The normalized value (by default the input value unchanged).
        """
        return value

    def metadata(self) -> dict[str, Any]:
        """
        Provide metadata describing the domain.
        
        Returns:
            dict[str, Any]: A mapping containing at least the "kind" key that identifies the domain type.
        """
        return {"kind": self.kind}

    def __iter__(self) -> Iterator[VALUE_TYPE]:
        """
        Indicate that this domain does not support eager iteration.
        
        Raises:
            DomainIterationError: Always raised to prevent iterating over the domain.
        """
        raise DomainIterationError(self.__class__.__name__)

    def __contains__(self, value: object) -> bool:
        """
        Determine whether the domain contains a value by comparing it for equality against the domain's members.
        
        @returns:
            `True` if an equal member exists in the domain, `False` otherwise.
        """
        for candidate in self:
            if candidate == value:
                return True
        return False


@dataclass(frozen=True)
class NumericRangeDomain(InputDomain[int | float | Decimal]):
    """Finite numeric range with optional stepping."""

    min_value: int | float | Decimal
    max_value: int | float | Decimal
    step: int | float | Decimal = 1

    def __init__(
        self,
        min_value: int | float | Decimal,
        max_value: int | float | Decimal,
        step: int | float | Decimal = 1,
    ) -> None:
        """
        Initialize a finite numeric range domain with inclusive bounds and a stepping interval.
        
        Parameters:
            min_value (int | float | Decimal): Lower inclusive bound of the range.
            max_value (int | float | Decimal): Upper inclusive bound of the range.
            step (int | float | Decimal): Positive increment between consecutive values (defaults to 1).
        
        Raises:
            InvalidNumericRangeError: If `step` is not greater than zero (reason "step") or if `min_value` is greater than `max_value` (reason "bounds").
        """
        if step <= 0:
            raise InvalidNumericRangeError("step")
        if min_value > max_value:
            raise InvalidNumericRangeError("bounds")
        object.__setattr__(self, "kind", "numeric_range")
        object.__setattr__(self, "min_value", min_value)
        object.__setattr__(self, "max_value", max_value)
        object.__setattr__(self, "step", step)

    def contains(self, value: int | float | Decimal) -> bool:
        """
        Determine whether a numeric value lies within the inclusive numeric range and falls exactly on the configured stepping sequence.
        
        Returns:
            `true` if `value` is between `min_value` and `max_value` (inclusive) and equals one of the discrete steps starting at `min_value` with increment `step`, `false` otherwise.
        """
        if value < self.min_value or value > self.max_value:
            return False
        current = self.min_value
        while current <= self.max_value:
            if current == value:
                return True
            current = _add_numeric_step(current, self.step)
        return False

    def metadata(self) -> dict[str, Any]:
        """
        Provide a dictionary describing this numeric range domain.
        
        The returned mapping includes:
        - "kind": domain identifier string.
        - "min_value": lower bound of the range.
        - "max_value": upper bound of the range.
        - "step": increment between successive values in the range.
        
        Returns:
            dict[str, Any]: Metadata with keys "kind", "min_value", "max_value", and "step".
        """
        return {
            "kind": self.kind,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "step": self.step,
        }

    def __iter__(self) -> Iterator[int | float | Decimal]:
        """
        Iterate over the domain's numeric range, yielding each value from the lower bound to the upper bound according to the configured step.
        
        Returns:
        	Iterator[int | float | Decimal]: Yields values starting at `min_value` up to and including `max_value`, advanced by `step`.
        """
        current = self.min_value
        while current <= self.max_value:
            yield current
            current = _add_numeric_step(current, self.step)

    def __contains__(self, value: object) -> bool:
        """
        Check whether the given value belongs to the numeric range domain.
        
        Parameters:
            value (object): Value to test; only integers, floats, and Decimals are considered.
        
        Returns:
            `true` if the value is a numeric type within the domain and aligned to the domain's step, `false` otherwise.
        """
        if not isinstance(value, (int, float, Decimal)):
            return False
        return self.contains(value)


@dataclass(frozen=True)
class DateRangeDomain(InputDomain[date]):
    """Finite date domain backed by an inclusive range and frequency."""

    start: date
    end: date
    frequency: str = "day"
    step: int = 1

    def __init__(
        self,
        start: date,
        end: date,
        *,
        frequency: str = "day",
        step: int = 1,
    ) -> None:
        """
        Construct a date range domain with an inclusive start and end and a stepping frequency.
        
        Parameters:
            start (date): The inclusive lower bound of the range.
            end (date): The inclusive upper bound of the range.
            frequency (str): Granularity used for normalization/advancement. One of:
                - "day": daily steps
                - "week_end": step aligns to week end (Sunday)
                - "month_start": step aligns to the first day of a month
                - "month_end": step aligns to the last day of a month
                - "quarter_end": step aligns to the last day of the quarter
                - "year_start": step aligns to the first day of a year
                - "year_end": step aligns to the last day of a year
                Defaults to "day".
            step (int): Positive integer number of `frequency` units to advance between values. Defaults to 1.
        
        Raises:
            InvalidDateRangeError: If `step` is less than or equal to zero, if `start` is after `end`, or if `frequency` is not one of the supported values.
        """
        if step <= 0:
            raise InvalidDateRangeError("step")
        if start > end:
            raise InvalidDateRangeError("bounds")
        if frequency not in {
            "day",
            "week_end",
            "month_start",
            "month_end",
            "quarter_end",
            "year_start",
            "year_end",
        }:
            raise InvalidDateRangeError("frequency", frequency=frequency)
        object.__setattr__(self, "kind", "date_range")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "frequency", frequency)
        object.__setattr__(self, "step", step)

    def normalize(self, value: date) -> date:
        """
        Normalize a date to this domain's configured frequency anchor.
        
        Returns:
            date: The date adjusted to the canonical boundary or anchor for the domain's `frequency`
            (e.g., unchanged for "day", week-ending date for "week_end", month start/end for
            "month_start"/"month_end", quarter end for "quarter_end", and year start/end for
            "year_start"/"year_end").
        """
        if self.frequency == "day":
            return value
        if self.frequency == "week_end":
            return _week_end(value)
        if self.frequency == "month_start":
            return _month_start(value)
        if self.frequency == "month_end":
            return _month_end(value)
        if self.frequency == "quarter_end":
            return _quarter_end(value)
        if self.frequency == "year_start":
            return _year_start(value)
        return _year_end(value)

    def contains(self, value: date) -> bool:
        """
        Determine whether a given date belongs to this date-range domain after applying the domain's normalization.
        
        Parameters:
            value (date): The date to test; it will be normalized according to the domain's frequency.
        
        Returns:
            True if the normalized date is within the domain's start/end bounds and aligns with the domain's stepping, False otherwise.
        """
        normalized = self.normalize(value)
        if normalized > self.end:
            return False
        return normalized in self

    def metadata(self) -> dict[str, Any]:
        """
        Metadata describing the date range domain.
        
        Returns:
            dict[str, Any]: Dictionary with keys:
                - "kind": domain kind identifier string.
                - "start": start date of the range.
                - "end": end date of the range.
                - "frequency": frequency identifier used for normalization and stepping.
                - "step": integer step size between consecutive values.
        """
        return {
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "frequency": self.frequency,
            "step": self.step,
        }

    def __iter__(self) -> Iterator[date]:
        """
        Yield dates from the domain's start to end, advancing by the domain's frequency and step.
        
        Iteration begins at the domain-normalized start and produces each subsequent date using the domain's `_advance` strategy, including the end date when reached.
        
        Returns:
            Iterator[date]: An iterator of date values from start through end.
        """
        current = self.normalize(self.start)
        last = self.end
        while current <= last:
            yield current
            current = self._advance(current)

    def __contains__(self, value: object) -> bool:
        """
        Determine whether a date or datetime lies within the domain's normalized date range.
        
        If a `datetime` is provided it is converted to its `date`. The value is normalized to the domain's frequency before comparison.
        
        Returns:
            `true` if the normalized date is within the domain's start and end bounds, `false` otherwise.
        """
        if not isinstance(value, date):
            return False
        date_value = value.date() if isinstance(value, datetime) else value
        normalized = self.normalize(date_value)
        end = self.end
        if normalized > end:
            return False
        current = self.normalize(self.start)
        last = end
        while current <= last:
            if current == normalized:
                return True
            current = self._advance(current)
        return False

    def _advance(self, value: date) -> date:
        """
        Advance a date by this domain's configured frequency and step.
        
        Advances the provided date forward by one increment of the domain's frequency multiplied by its step. Supported frequencies: "day", "week_end", "month_start", "month_end", "quarter_end", "year_start", and "year_end".
        
        Returns:
            advanced_date (date): The resulting date after advancing by the frequency * step.
        """
        if self.frequency == "day":
            return value.fromordinal(value.toordinal() + self.step)
        if self.frequency == "week_end":
            return value.fromordinal(value.toordinal() + self.step * 7)
        if self.frequency == "month_start":
            return _month_start(_add_months(value, self.step))
        if self.frequency == "month_end":
            return _month_end(_add_months(value, self.step))
        if self.frequency == "quarter_end":
            return _quarter_end(_add_months(value, self.step * 3))
        if self.frequency == "year_start":
            return value.replace(year=value.year + self.step, month=1, day=1)
        return value.replace(year=value.year + self.step, month=12, day=31)


class Input(Generic[INPUT_TYPE]):
    """Descriptor describing the expected type and constraints for an interface input."""

    def __init__(
        self,
        type: INPUT_TYPE,
        possible_values: PossibleValues | None = None,
        depends_on: list[str] | None = None,
        *,
        required: bool = True,
        min_value: ScalarConstraint | None = None,
        max_value: ScalarConstraint | None = None,
        validator: Validator | None = None,
        normalizer: Normalizer | None = None,
    ) -> None:
        """
        Describe an expected input including its target type, allowed values, validation, normalization, and dependency metadata.
        
        Parameters:
            type: Target Python type or class used when casting input values.
            possible_values: Domain, iterable, bucket, or a callable that returns one; callables may accept dependency values.
            depends_on: Explicit list of dependency names required when evaluating callable `possible_values`, `validator`, or `normalizer`. If omitted and `possible_values` is callable, dependencies are inferred from the callable's parameters (excluding *args/**kwargs).
            required: Whether a value must be provided for the input.
            min_value: Inclusive lower scalar bound used by validate_bounds.
            max_value: Inclusive upper scalar bound used by validate_bounds.
            validator: Optional callable invoked with (value, **dependency_values) that returns `True`/`False` or `None` (treated as `True`) to indicate validity.
            normalizer: Optional callable invoked after casting with signature (value, **dependency_values) or accepting `domain` to canonicalize the value.
        """
        self.type: builtins.type[Any] = cast(Any, type)
        self.possible_values = possible_values
        self.required = required
        self.min_value = min_value
        self.max_value = max_value
        self.validator = validator
        self.normalizer = normalizer
        self.is_manager = issubclass(type, GeneralManager)

        if depends_on is not None:
            self.depends_on = depends_on
        elif callable(possible_values):
            signature = inspect.signature(possible_values)
            self.depends_on = [
                name
                for name, parameter in signature.parameters.items()
                if parameter.kind
                not in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }
            ]
        else:
            self.depends_on = []

    @classmethod
    def date_range(
        cls,
        *,
        start: date | Callable[..., date],
        end: date | Callable[..., date],
        frequency: str = "day",
        step: int = 1,
        depends_on: list[str] | None = None,
        required: bool = True,
    ) -> "Input[type[date]]":
        """
        Create an Input configured with a DateRangeDomain describing allowed date values.
        
        Parameters:
            start (date | Callable[..., date]): Anchor start date or a callable that receives dependency values and returns a date.
            end (date | Callable[..., date]): Anchor end date or a callable that receives dependency values and returns a date.
            frequency (str): Periodicity used to normalize and iterate dates (e.g., "day", "month_start", "month_end", "week_end", "quarter_end", "year_start", "year_end").
            step (int): Number of frequency units between successive values; must be greater than zero.
            depends_on (list[str] | None): Explicit dependency names whose values are provided to callable bounds; when omitted, dependencies are inferred from `start`/`end` callables' parameter names.
            required (bool): Whether the input value is required.
        
        Returns:
            Input[type[date]]: An Input whose possible_values is either a DateRangeDomain (for static bounds) or a builder callable that returns a DateRangeDomain when given dependency values. When `frequency` is not "day", the Input's normalizer will normalize values via the domain.
        """

        def build_domain(**dependency_values: Any) -> DateRangeDomain:
            """
            Builds a DateRangeDomain by resolving start and end bounds from provided dependency values.
            
            Parameters:
                dependency_values (dict): Mapping of dependency names to their current values used to resolve callable bounds.
            
            Returns:
                DateRangeDomain: A domain spanning the resolved start and end dates with the configured frequency and step.
            """
            resolved_start = cls._resolve_bound(start, dependency_values)
            resolved_end = cls._resolve_bound(end, dependency_values)
            return DateRangeDomain(
                resolved_start,
                resolved_end,
                frequency=frequency,
                step=step,
            )

        resolved_depends_on = depends_on or cls._infer_dependencies(start, end)
        domain_or_builder: PossibleValues | DateRangeDomain
        if resolved_depends_on:
            domain_or_builder = build_domain
        else:
            domain_or_builder = build_domain()
        return cast(
            "Input[type[date]]",
            cls(
                cast(Any, date),
                possible_values=domain_or_builder,
                depends_on=resolved_depends_on,
                required=required,
                normalizer=(
                    None
                    if frequency == "day"
                    else lambda value, domain: domain.normalize(value)
                ),
            ),
        )

    @classmethod
    def monthly_date(
        cls,
        *,
        start: date | Callable[..., date],
        end: date | Callable[..., date],
        anchor: str = "month_end",
        step: int = 1,
        depends_on: list[str] | None = None,
        required: bool = True,
    ) -> "Input[type[date]]":
        """
        Create an Input describing allowed dates aligned to canonical monthly boundaries.
        
        Parameters:
            start (date | Callable[..., date]): Lower bound for the range or a callable that returns it when given dependency values.
            end (date | Callable[..., date]): Upper bound for the range or a callable that returns it when given dependency values.
            anchor (str): Which monthly anchor to use; "start" maps to month-start, "end" maps to month-end, or pass a frequency name directly.
            step (int): Number of months between successive allowed dates.
            depends_on (list[str] | None): Names of inputs whose values are provided to `start`/`end` if those are callables.
            required (bool): Whether a value for this input is required.
        
        Returns:
            Input[type[date]]: An Input whose possible values are monthly-aligned dates within [start, end] at the given step.
        """

        frequency = {"start": "month_start", "end": "month_end"}.get(anchor, anchor)
        return cls.date_range(
            start=start,
            end=end,
            frequency=frequency,
            step=step,
            depends_on=depends_on,
            required=required,
        )

    @classmethod
    def yearly_date(
        cls,
        *,
        start: date | Callable[..., date],
        end: date | Callable[..., date],
        anchor: str = "year_end",
        step: int = 1,
        depends_on: list[str] | None = None,
        required: bool = True,
    ) -> "Input[type[date]]":
        """
        Create an Input constrained to canonical yearly dates.
        
        Parameters:
            start (date | Callable[..., date]): Lower bound of the date range or a callable that returns it when given dependency values.
            end (date | Callable[..., date]): Upper bound of the date range or a callable that returns it when given dependency values.
            anchor (str): Which yearly anchor to use; accepted values are "start" (maps to "year_start"), "end" (maps to "year_end"), or a specific frequency like "year_start"/"year_end".
            step (int): Number of yearly periods between successive allowed values (must be greater than 0).
            depends_on (list[str] | None): Names of other inputs whose values will be passed to any callable bounds.
            required (bool): Whether the input is required.
        
        Returns:
            Input[type[date]]: An Input configured with a DateRangeDomain that yields dates aligned to the chosen yearly anchor and step.
        """

        frequency = {"start": "year_start", "end": "year_end"}.get(anchor, anchor)
        return cls.date_range(
            start=start,
            end=end,
            frequency=frequency,
            step=step,
            depends_on=depends_on,
            required=required,
        )

    @classmethod
    def from_manager_query(
        cls,
        manager_type: INPUT_TYPE,
        *,
        query: dict[str, Any] | Callable[..., dict[str, Any] | Any] | None = None,
        depends_on: list[str] | None = None,
        required: bool = True,
    ) -> "Input[INPUT_TYPE]":
        """
        Create an Input whose possible values are produced by querying the given manager type.
        
        Parameters:
            manager_type: A manager class providing `all()` and `filter(**kwargs)` query methods; used to resolve possible values.
            query: If `None`, `manager_type.all()` is used. If a `dict`, `manager_type.filter(**dict)` is used. If a callable, it is invoked with dependency values and its result is used; the callable may return a dict (applied to `filter`) or any iterable of values.
            depends_on: Explicit list of dependency names for `query` arguments; if omitted and `query` is callable, dependencies are inferred from the callable's parameter names.
            required: Whether the resulting Input is required.
        
        Returns:
            An Input configured to supply possible values from the resolved manager query, with `depends_on` set to the resolved dependency list.
        """

        def build_values(**dependency_values: Any) -> Any:
            """
            Resolve the configured query using provided dependency values and return the resulting manager query or resolved value.
            
            Parameters:
                dependency_values (Any): Mapping of dependency names to values used when invoking a callable `query`.
            
            Returns:
                The resolved possible-values result: if `query` is None, the manager's `all()` result; if `query` is a callable, its invocation result; if the resolved result is a dict, the manager's `filter(**dict)` result; otherwise the resolved result as-is.
            """
            if query is None:
                return manager_type.all()  # type: ignore[attr-defined]
            resolved_query = (
                _invoke_callable(query, **dependency_values)
                if callable(query)
                else query
            )
            if isinstance(resolved_query, dict):
                return manager_type.filter(**resolved_query)  # type: ignore[attr-defined]
            return resolved_query

        resolved_depends_on = depends_on or cls._infer_dependencies(query)
        possible_values: PossibleValues
        if resolved_depends_on:
            possible_values = build_values
        else:
            possible_values = build_values()
        return cls(
            manager_type,
            possible_values=possible_values,
            depends_on=resolved_depends_on,
            required=required,
        )

    @staticmethod
    def _infer_dependencies(*values: object) -> list[str]:
        """
        Collect ordered, unique parameter names from callable inputs to infer dependencies.
        
        Parameters:
            values (object): One or more values; callables among these are inspected for their
                parameter names. Non-callable values are ignored.
        
        Returns:
            dependencies (list[str]): Ordered list of unique parameter names from the inspected
            callables, excluding var-positional (`*args`) and var-keyword (`**kwargs`) parameters.
        """
        dependencies: list[str] = []
        for value in values:
            if not callable(value):
                continue
            for parameter_name, parameter in inspect.signature(
                value
            ).parameters.items():
                if parameter.kind in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }:
                    continue
                if parameter_name not in dependencies:
                    dependencies.append(parameter_name)
        return dependencies

    @staticmethod
    def _resolve_bound(
        value: date | Callable[..., date],
        dependency_values: dict[str, Any],
    ) -> date:
        """
        Resolve a date bound that may be a concrete date or a callable producing a date using dependency values.
        
        Parameters:
            value (date | Callable[..., date]): Either a date or a callable that returns a date when invoked with dependency keyword arguments.
            dependency_values (dict[str, Any]): Mapping of dependency names to values to pass as keyword arguments if `value` is callable.
        
        Returns:
            date: The resolved date.
        """
        if callable(value):
            return cast(date, _invoke_callable(value, **dependency_values))
        return value

    def resolve_possible_values(
        self,
        identification: dict[str, Any] | None = None,
    ) -> Any:
        """
        Resolve the Input's configured possible values using an optional dependency context.
        
        If `possible_values` is a callable, it is invoked with dependency values built from `identification`; otherwise the stored `possible_values` is returned. If no possible values were configured, `None` is returned.
        
        Parameters:
            identification (dict[str, Any] | None): Mapping of dependency names to values used when resolving a callable `possible_values`; pass `None` when there are no dependencies.
        
        Returns:
            The resolved possible values (e.g., an InputDomain, iterable, manager query result, or `None`).
        """

        if self.possible_values is None:
            return None
        if callable(self.possible_values):
            dependency_values = self._build_dependency_values(identification)
            return cast(
                Any,
                _invoke_callable(
                    self.possible_values,
                    *dependency_values.values(),
                    **dependency_values,
                ),
            )
        return cast(Any, self.possible_values)

    def normalize(
        self, value: Any, identification: dict[str, Any] | None = None
    ) -> Any:
        """
        Canonicalize an input value according to the configured possible-values domain and/or a custom normalizer.
        
        If the configured possible values resolve to an InputDomain, the domain's normalization is applied. If a custom normalizer is configured, it is invoked with the value, the resolved dependency values (positional and keyword), and the resolved domain under the `domain` keyword; its result is returned. If neither applies, the (possibly domain-normalized) value is returned unchanged.
        
        Parameters:
            value: The value to normalize; may be any type accepted by the input.
            identification (dict[str, Any] | None): Optional mapping of dependency names to their current values used to resolve possible values and to pass to the custom normalizer.
        
        Returns:
            The canonicalized value after domain-based and/or custom normalization.
        """

        if value is None:
            return None
        possible_values = self.resolve_possible_values(identification)
        if isinstance(possible_values, InputDomain):
            value = possible_values.normalize(value)
        if self.normalizer is not None:
            dependency_values = self._build_dependency_values(identification)
            return _invoke_callable(
                self.normalizer,
                value,
                *dependency_values.values(),
                domain=possible_values,
                **dependency_values,
            )
        return value

    def validate_bounds(self, value: Any) -> bool:
        """
        Check whether the given value falls within the Input's configured min and max bounds.
        
        None is considered valid only when the input is not required.
        
        Parameters:
            value (Any): The value to validate against configured bounds.
        
        Returns:
            `True` if the value is within the configured bounds (or is `None` and the input is not required), `False` otherwise.
        """

        if value is None:
            return not self.required
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True

    def validate_with_callable(
        self,
        value: Any,
        identification: dict[str, Any] | None = None,
    ) -> bool:
        """
        Check a value against the configured validator callback.
        
        If no validator is configured or the value is None, validation succeeds. When a validator exists, dependency values are built from `identification` and the validator is invoked with the value followed by those dependency values (both as positional and keyword arguments). If the validator returns `None`, the value is considered valid; otherwise the boolean value of the validator's result is returned.
        
        Parameters:
            value (Any): The value to validate.
            identification (dict[str, Any] | None): Mapping of dependency names to values used to build validator arguments.
        
        Returns:
            bool: `True` if the value is valid, `False` otherwise.
        """

        if self.validator is None or value is None:
            return True
        dependency_values = self._build_dependency_values(identification)
        result = _invoke_callable(
            self.validator,
            value,
            *dependency_values.values(),
            **dependency_values,
        )
        if result is None:
            return True
        return bool(result)

    def _build_dependency_values(
        self,
        identification: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Build a mapping of this Input's declared dependencies to their values from an identification dictionary.
        
        Parameters:
        	identification (dict[str, Any] | None): Mapping of input names to supplied values (typically an identification/context).
        
        Returns:
        	dict[str, Any]: A dictionary containing each dependency name from `self.depends_on` mapped to its value from `identification`.
        
        Raises:
        	KeyError: If `identification` is None or a required dependency name is missing.
        """
        dependency_values: dict[str, Any] = {}
        for dependency_name in self.depends_on:
            if identification is None or dependency_name not in identification:
                raise KeyError(dependency_name)
            dependency_values[dependency_name] = identification[dependency_name]
        return dependency_values

    def cast(self, value: Any, identification: dict[str, Any] | None = None) -> Any:
        """
        Cast a raw input value to the Input's configured type and apply any configured normalization.
        
        Parameters:
            value (Any): Raw value to convert. If `None`, `None` is returned.
            identification (dict[str, Any] | None): Mapping of dependency names to values passed to normalizers/validators.
        
        Returns:
            Any: The converted value (of the configured target type) after normalization.
        """
        if value is None:
            return None
        if self.type == date:
            if isinstance(value, datetime) and type(value) is not date:
                cast_value = value.date()
            elif isinstance(value, date):
                cast_value = value
            else:
                cast_value = date.fromisoformat(value)
            return self.normalize(cast_value, identification)
        if self.type == datetime:
            if isinstance(value, datetime):
                cast_value = value
            elif isinstance(value, date):
                cast_value = datetime.combine(value, datetime.min.time())
            else:
                cast_value = datetime.fromisoformat(value)
            return self.normalize(cast_value, identification)
        if isinstance(value, self.type):
            return self.normalize(value, identification)
        if issubclass(self.type, GeneralManager):
            if isinstance(value, dict):
                return self.normalize(self.type(**value), identification)  # type: ignore[misc]
            return self.normalize(self.type(id=value), identification)  # type: ignore[misc]
        if self.type == Measurement and isinstance(value, str):
            return self.normalize(Measurement.from_string(value), identification)
        return self.normalize(self.type(value), identification)
