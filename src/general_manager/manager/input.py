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
        super().__init__(f"{domain_name} does not provide eager iteration.")


class InvalidNumericRangeError(ValueError):
    """Raised when a numeric range domain is configured with invalid bounds."""

    def __init__(self, reason: str) -> None:
        messages = {
            "step": "NumericRangeDomain step must be greater than zero.",
            "bounds": "NumericRangeDomain min_value must be <= max_value.",
        }
        super().__init__(messages[reason])


class InvalidDateRangeError(ValueError):
    """Raised when a date range domain is configured with invalid bounds."""

    def __init__(self, reason: str, *, frequency: str | None = None) -> None:
        messages = {
            "step": "DateRangeDomain step must be greater than zero.",
            "bounds": "DateRangeDomain start must be <= end.",
        }
        if reason == "frequency":
            super().__init__(f"Unsupported date frequency: {frequency}.")
            return
        super().__init__(messages[reason])


def _invoke_callable(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Invoke a callback with only the arguments its signature accepts."""

    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
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
                remaining_kwargs.pop(parameter.name, None)
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

    return func(*bound_args, **bound_kwargs)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _year_start(value: date) -> date:
    return value.replace(month=1, day=1)


def _year_end(value: date) -> date:
    return value.replace(month=12, day=31)


def _week_end(value: date) -> date:
    days_until_sunday = (6 - value.weekday()) % 7
    return value.fromordinal(value.toordinal() + days_until_sunday)


def _quarter_end(value: date) -> date:
    quarter_end_month = ((value.month - 1) // 3 + 1) * 3
    last_day = calendar.monthrange(value.year, quarter_end_month)[1]
    return value.replace(month=quarter_end_month, day=last_day)


def _add_months(value: date, months: int) -> date:
    absolute_month = (value.year * 12 + (value.month - 1)) + months
    year = absolute_month // 12
    month = absolute_month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def _add_numeric_step(
    current: int | float | Decimal,
    step: int | float | Decimal,
) -> int | float | Decimal:
    if isinstance(current, Decimal) or isinstance(step, Decimal):
        return Decimal(str(current)) + Decimal(str(step))
    return current + step


def _float_tolerance(step: int | float | Decimal) -> float:
    return max(1e-12, abs(float(step)) * 1e-9)


def _decimal_tolerance(step: int | float | Decimal) -> Decimal:
    return max(Decimal("1e-12"), abs(Decimal(str(step))) * Decimal("1e-9"))


@dataclass(frozen=True)
class InputDomain(Generic[VALUE_TYPE]):
    """Structured description of an input domain."""

    kind: str

    def contains(self, value: VALUE_TYPE) -> bool:
        try:
            return value in self
        except TypeError:
            return False

    def normalize(self, value: VALUE_TYPE) -> VALUE_TYPE:
        return value

    def metadata(self) -> dict[str, Any]:
        return {"kind": self.kind}

    def __iter__(self) -> Iterator[VALUE_TYPE]:
        raise DomainIterationError(self.__class__.__name__)

    def __contains__(self, value: object) -> bool:
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
        if step <= 0:
            raise InvalidNumericRangeError("step")
        if min_value > max_value:
            raise InvalidNumericRangeError("bounds")
        object.__setattr__(self, "kind", "numeric_range")
        object.__setattr__(self, "min_value", min_value)
        object.__setattr__(self, "max_value", max_value)
        object.__setattr__(self, "step", step)

    def contains(self, value: int | float | Decimal) -> bool:
        if any(
            isinstance(candidate, Decimal)
            for candidate in (self.min_value, self.max_value, self.step, value)
        ):
            decimal_value = Decimal(str(value))
            decimal_min = Decimal(str(self.min_value))
            decimal_max = Decimal(str(self.max_value))
            decimal_step = Decimal(str(self.step))
            decimal_tolerance = _decimal_tolerance(decimal_step)
            if decimal_value < decimal_min - decimal_tolerance:
                return False
            if decimal_value > decimal_max + decimal_tolerance:
                return False
            decimal_current = decimal_min
            while decimal_current <= decimal_max + decimal_tolerance:
                if abs(decimal_current - decimal_value) <= decimal_tolerance:
                    return True
                decimal_current = cast(
                    Decimal,
                    _add_numeric_step(decimal_current, decimal_step),
                )
            return False

        if any(
            isinstance(candidate, float)
            for candidate in (self.min_value, self.max_value, self.step, value)
        ):
            float_value = float(value)
            float_min = float(self.min_value)
            float_max = float(self.max_value)
            float_step = float(self.step)
            float_tolerance = _float_tolerance(float_step)
            if float_value < float_min - float_tolerance:
                return False
            if float_value > float_max + float_tolerance:
                return False
            float_current = float_min
            while float_current <= float_max + float_tolerance:
                if abs(float_current - float_value) <= float_tolerance:
                    return True
                float_current = float(
                    cast(float, _add_numeric_step(float_current, float_step))
                )
            return False

        current = cast(int, self.min_value)
        max_value = cast(int, self.max_value)
        step = cast(int, self.step)
        integer_value = cast(int, value)
        while current <= max_value:
            if current == integer_value:
                return True
            current = cast(int, _add_numeric_step(current, step))
        return False

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "step": self.step,
        }

    def __iter__(self) -> Iterator[int | float | Decimal]:
        current = self.min_value
        while current <= self.max_value:
            yield current
            current = _add_numeric_step(current, self.step)

    def __contains__(self, value: object) -> bool:
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
        normalized = self.normalize(value)
        if normalized > self.end:
            return False
        return normalized in self

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "frequency": self.frequency,
            "step": self.step,
        }

    def __iter__(self) -> Iterator[date]:
        current = self.normalize(self.start)
        last = self.end
        while current <= last:
            yield current
            current = self._advance(current)

    def __contains__(self, value: object) -> bool:
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
        Create an Input specification with type information, constraints, and dependency metadata.

        Parameters:
            type (INPUT_TYPE): Expected Python type for the input value.
            possible_values: Allowed values as a domain, iterable, bucket, or callable returning one.
            depends_on (list[str] | None): Names of other inputs required for dynamic constraints.
            required (bool): Whether callers must provide a value for this input.
            min_value: Inclusive lower bound for scalar values.
            max_value: Inclusive upper bound for scalar values.
            validator: Extra validation callback returning ``True``/``False`` or ``None``.
            normalizer: Optional callback used to canonicalize values after casting.
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
        """Create a date input backed by a structured date range domain."""

        def build_domain(**dependency_values: Any) -> DateRangeDomain:
            resolved_start = cls._resolve_bound(start, dependency_values)
            resolved_end = cls._resolve_bound(end, dependency_values)
            return DateRangeDomain(
                resolved_start,
                resolved_end,
                frequency=frequency,
                step=step,
            )

        resolved_depends_on = (
            depends_on
            if depends_on is not None
            else cls._infer_dependencies(start, end)
        )
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
        """Create a date input constrained to canonical monthly dates."""

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
        """Create a date input constrained to canonical yearly dates."""

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
        """Create a manager input whose possible values come from a manager query."""

        def build_values(**dependency_values: Any) -> Any:
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

        resolved_depends_on = (
            depends_on if depends_on is not None else cls._infer_dependencies(query)
        )
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
        if callable(value):
            return cast(date, _invoke_callable(value, **dependency_values))
        return value

    def resolve_possible_values(
        self,
        identification: dict[str, Any] | None = None,
    ) -> Any:
        """Resolve the configured possible values for the current dependency context."""

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
        """Canonicalize a cast value using domain or explicit normalization rules."""

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
        """Return whether a value satisfies configured scalar bounds."""

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
        """Return whether a value satisfies the configured validator callback."""

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
        dependency_values: dict[str, Any] = {}
        for dependency_name in self.depends_on:
            if identification is None or dependency_name not in identification:
                raise KeyError(dependency_name)
            dependency_values[dependency_name] = identification[dependency_name]
        return dependency_values

    def cast(self, value: Any, identification: dict[str, Any] | None = None) -> Any:
        """
        Convert a raw value to the configured input type.

        Parameters:
            value (Any): Raw value supplied by the caller.
            identification (dict[str, Any] | None): Dependency values used by normalizers.

        Returns:
            Any: Value converted to the target type.

        Raises:
            ValueError: If the value cannot be converted to the target type.
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
