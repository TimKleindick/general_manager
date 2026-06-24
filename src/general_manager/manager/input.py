"""Input field metadata used by GeneralManager interfaces."""

from __future__ import annotations

import calendar
import builtins
import inspect
from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, cast

from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement

if TYPE_CHECKING:
    from general_manager.bucket.base_bucket import Bucket


INPUT_TYPE = TypeVar("INPUT_TYPE", bound=type[object])
VALUE_TYPE = TypeVar("VALUE_TYPE")

type PossibleValues = (
    "InputDomain[object]"
    | "Iterable[object]"
    | "Bucket[GeneralManager]"
    | "Callable[..., object]"
)
type PossibleValuesCacheContext = tuple[type[object], str]
"""Run-cache context for callable possible values: ``(owner_class, field_name)``."""
type ScalarConstraint = date | datetime | int | float | Decimal
type Validator = Callable[..., bool | None]
type Normalizer = Callable[..., object]


class _Comparable(Protocol):
    def __lt__(self, other: object) -> bool: ...

    def __gt__(self, other: object) -> bool: ...


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


def _invoke_callable(
    func: Callable[..., object],
    /,
    *args: object,
    **kwargs: object,
) -> object:
    """Invoke a callback with only the arguments its signature accepts."""

    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
    positional_args = list(args)
    bound_args: list[object] = []
    bound_kwargs: dict[str, object] = {}
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


def _materialize_cached_possible_values(possible_values: object) -> object:
    """Store one-shot iterators as reusable values in the run cache."""

    if isinstance(possible_values, Iterator):
        return list(possible_values)
    return possible_values


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
    """Structured description of an input domain.

    The base domain stores a ``kind`` string, returns identity values from
    :meth:`normalize`, reports ``{"kind": kind}`` from :meth:`metadata`, and
    implements ``contains(value)`` as a safe membership check. Direct ``in``
    checks call :meth:`__contains__`, which iterates over :meth:`__iter__`. On
    the base class that raises ``DomainIterationError`` because subclasses are
    responsible for finite iteration; call :meth:`contains` when callers need a
    false result instead of that eager-iteration error.
    """

    kind: str

    def contains(self, value: VALUE_TYPE) -> bool:
        try:
            return value in self
        except TypeError:
            return False

    def normalize(self, value: VALUE_TYPE) -> VALUE_TYPE:
        return value

    def metadata(self) -> dict[str, object]:
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
    """Inclusive finite numeric range with optional stepping.

    Values are valid only when they fall on the generated step sequence. If any
    bound, step, or candidate is a ``Decimal``, membership and iteration use
    ``Decimal`` arithmetic and iteration yields ``Decimal`` values. Otherwise,
    if any bound, step, or candidate is a ``float``, they use ``float``
    arithmetic and iteration yields ``float`` values. Pure integer ranges yield
    ``int`` values. ``bool`` is not a documented numeric input type even though
    Python may treat it as an integer at runtime. Floating-point checks use
    ``max(1e-12, abs(step) * 1e-9)`` tolerance and decimal checks use the
    equivalent ``Decimal`` tolerance so endpoint and step comparisons remain
    stable for common decimal fractions.
    """

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

    def metadata(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "step": self.step,
        }

    def __iter__(self) -> Iterator[int | float | Decimal]:
        if any(
            isinstance(candidate, Decimal)
            for candidate in (self.min_value, self.max_value, self.step)
        ):
            decimal_min = Decimal(str(self.min_value))
            decimal_max = Decimal(str(self.max_value))
            decimal_step = Decimal(str(self.step))
            decimal_tolerance = _decimal_tolerance(decimal_step)
            decimal_current = decimal_min
            while decimal_current <= decimal_max + decimal_tolerance:
                if abs(decimal_current - decimal_max) <= decimal_tolerance:
                    yield decimal_max
                    break
                yield decimal_current
                decimal_current = cast(
                    Decimal,
                    _add_numeric_step(decimal_current, decimal_step),
                )
            return

        if any(
            isinstance(candidate, float)
            for candidate in (self.min_value, self.max_value, self.step)
        ):
            float_min = float(self.min_value)
            float_max = float(self.max_value)
            float_step = float(self.step)
            float_tolerance = _float_tolerance(float_step)
            float_current = float_min
            while float_current <= float_max + float_tolerance:
                if abs(float_current - float_max) <= float_tolerance:
                    yield float_max
                    break
                yield float_current
                float_current = float(
                    cast(float, _add_numeric_step(float_current, float_step))
                )
            return

        current = cast(int, self.min_value)
        max_value = cast(int, self.max_value)
        step = cast(int, self.step)
        while current <= max_value:
            yield current
            current = cast(int, _add_numeric_step(current, step))

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, (int, float, Decimal)):
            return False
        return self.contains(value)


@dataclass(frozen=True)
class DateRangeDomain(InputDomain[date]):
    """Inclusive finite date domain backed by a frequency and step.

    Non-daily frequencies normalize candidate dates to the configured anchor
    before membership checks. ``week_end`` anchors to Sunday. Month and year
    steps advance by calendar months or years from the current anchored date;
    quarter steps advance by three-month intervals. Iteration starts at
    ``normalize(start)`` and stops once the next generated date would be greater
    than the raw ``end`` value, so unaligned starts may anchor before or after
    the supplied start date and unaligned ends are upper bounds rather than
    additional anchors. ``datetime`` values are accepted by membership checks and
    converted to their date component first; :meth:`normalize` itself is typed
    for ``date`` inputs.
    """

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
        date_value = value.date() if isinstance(value, datetime) else value
        normalized = self.normalize(date_value)
        if normalized > self.end:
            return False
        return normalized in self

    def metadata(self) -> dict[str, object]:
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
    """Describe an interface input's type, constraints, and dependency metadata.

    ``Input`` instances are attached to ``Interface`` classes and consumed by
    GeneralManager interfaces when casting raw values, enumerating calculation
    combinations, or exposing GraphQL input metadata. The descriptor stores the
    expected Python class, optional allowed values, scalar bounds, dependency
    names, and optional validation/normalization callbacks. It does not validate
    that static ``possible_values`` match ``type`` during construction.
    """

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
            type (INPUT_TYPE): Expected Python class for the input value.
            possible_values: Allowed values as a domain, iterable, bucket, or callable returning one.
            depends_on (list[str] | None): Names of other inputs required for dynamic constraints.
            required (bool): Whether callers must provide a value for this input.
            min_value: Inclusive lower bound for scalar values.
            max_value: Inclusive upper bound for scalar values.
            validator: Extra validation callback returning ``True``/``False`` or ``None``.
            normalizer: Optional callback used to canonicalize values after casting.

        Raises:
            TypeError: If ``type`` is not a class accepted by ``issubclass``.
        """
        self.type: builtins.type[object] = cast(builtins.type[object], type)
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
            self.depends_on = self._infer_dependencies(possible_values)
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
        """Create a date input backed by a structured date range domain.

        ``start`` and ``end`` may be dates or callbacks that accept declared
        dependency values. Dependency names are inferred from callback parameter
        names unless ``depends_on`` is supplied. The generated input normalizes
        non-daily frequencies to their canonical date before returning values
        from :meth:`cast`.

        Raises:
            InvalidDateRangeError: If bounds, frequency, or step are invalid
                after dependency callbacks have been resolved.
            TypeError: If a dependency callback cannot be invoked with the
                available dependency values.
            KeyError: If a dependent domain is resolved without a required
                dependency value.
        """

        def build_domain(**dependency_values: object) -> DateRangeDomain:
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
                cast(INPUT_TYPE, date),
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
        """Create a date input constrained to canonical monthly dates.

        ``anchor`` accepts ``"start"``/``"month_start"`` and
        ``"end"``/``"month_end"``. Other values are forwarded as date-range
        frequencies and invalid values raise ``InvalidDateRangeError`` when the
        domain is built.
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
        """Create a date input constrained to canonical yearly dates.

        ``anchor`` accepts ``"start"``/``"year_start"`` and
        ``"end"``/``"year_end"``. Other values are forwarded as date-range
        frequencies and invalid values raise ``InvalidDateRangeError`` when the
        domain is built.
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
        query: dict[str, object] | Callable[..., object] | None = None,
        depends_on: list[str] | None = None,
        required: bool = True,
    ) -> "Input[INPUT_TYPE]":
        """Create a manager input whose possible values come from a manager query.

        With no query, possible values come from ``manager_type.all()``. Mapping
        queries are passed to ``manager_type.filter(**query)``; callable queries
        receive dependency values and may return either a mapping or an already
        resolved iterable/bucket/domain.

        Raises:
            AttributeError: If ``manager_type`` does not provide the required
                ``all`` or ``filter`` class method for the selected query form.
            TypeError: If a query callback cannot be invoked with the available
                dependency values.
            KeyError: If dependency values are missing when possible values are
                resolved.
        """

        def build_values(**dependency_values: object) -> object:
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
            possible_values = cast(PossibleValues, build_values())
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
        dependency_values: dict[str, object],
    ) -> date:
        if callable(value):
            return cast(date, _invoke_callable(value, **dependency_values))
        return value

    def _possible_values_run_cache_key(
        self,
        cache_context: PossibleValuesCacheContext,
        dependency_values: dict[str, object],
    ) -> tuple[Hashable, ...]:
        from general_manager.bucket.indexing import freeze_bucket_index_value

        return cast(
            tuple[Hashable, ...],
            (
                "input_possible_values",
                cache_context[0],
                cache_context[1],
                tuple(
                    (
                        dependency_name,
                        freeze_bucket_index_value(dependency_values[dependency_name]),
                    )
                    for dependency_name in self.depends_on
                ),
            ),
        )

    def resolve_possible_values(
        self,
        identification: dict[str, object] | None = None,
        *,
        cache_context: PossibleValuesCacheContext | None = None,
    ) -> object:
        """Resolve possible values for the current dependency context.

        Static values are returned unchanged. Callable providers receive only
        declared dependency values, raising ``KeyError`` when a dependency is
        missing. When called inside a calculation run with ``cache_context``,
        provider results are cached by owner class, field name, and declared
        dependency values; one-shot iterators are materialized before caching.
        Unhashable dependency values skip the cache and call the provider.
        """

        if self.possible_values is None:
            return None
        if callable(self.possible_values):
            possible_values_provider = self.possible_values
            dependency_values = self._build_dependency_values(identification)

            def invoke_possible_values() -> object:
                return _invoke_callable(
                    possible_values_provider,
                    *dependency_values.values(),
                    **dependency_values,
                )

            if cache_context is None:
                return invoke_possible_values()

            from general_manager.cache.run_context import (
                current_calculation_run_context,
            )

            context = current_calculation_run_context()
            if context is None:
                return invoke_possible_values()

            try:
                cache_key = self._possible_values_run_cache_key(
                    cache_context,
                    dependency_values,
                )
            except TypeError:
                return invoke_possible_values()
            return context.get_or_set(
                cache_key,
                lambda: _materialize_cached_possible_values(invoke_possible_values()),
            )
        return self.possible_values

    def normalize(
        self,
        value: object,
        identification: dict[str, object] | None = None,
        *,
        cache_context: PossibleValuesCacheContext | None = None,
    ) -> object:
        """Canonicalize a cast value using domain or explicit normalization rules.

        If ``possible_values`` is a static ``InputDomain``, domain normalization
        runs even when no custom normalizer is configured. Dynamic possible
        values are resolved only when a custom normalizer exists; if that
        resolved value is an ``InputDomain``, dynamic-domain normalization runs
        before the custom normalizer. Custom normalizers receive the converted
        value first, then positional dependency values, a ``domain`` keyword
        containing the resolved possible-values object or ``None``, and named
        dependency values. Unsupported callback signatures raise the normal
        Python ``TypeError`` from invocation.
        """

        if value is None:
            return None
        possible_values: object | None = (
            self.possible_values
            if isinstance(self.possible_values, InputDomain)
            else None
        )
        if isinstance(possible_values, InputDomain):
            value = possible_values.normalize(value)
        if self.normalizer is not None:
            if possible_values is None:
                possible_values = self.resolve_possible_values(
                    identification,
                    cache_context=cache_context,
                )
                if isinstance(possible_values, InputDomain):
                    value = possible_values.normalize(value)
            dependency_values = self._build_dependency_values(identification)
            return _invoke_callable(
                self.normalizer,
                value,
                *dependency_values.values(),
                domain=possible_values,
                **dependency_values,
            )
        return value

    def validate_bounds(self, value: object) -> bool:
        """Return whether a value satisfies configured scalar bounds.

        ``None`` is valid only for non-required inputs. For non-``None`` values,
        Python comparison errors from incompatible value/bound types propagate.
        """

        if value is None:
            return not self.required
        bounded_value = cast(_Comparable, value)
        if self.min_value is not None and bounded_value < self.min_value:
            return False
        if self.max_value is not None and bounded_value > self.max_value:
            return False
        return True

    def validate_with_callable(
        self,
        value: object,
        identification: dict[str, object] | None = None,
    ) -> bool:
        """Return whether a value satisfies the configured validator callback.

        ``None`` values bypass custom validators. Validator callbacks receive the
        candidate value first, followed by declared dependency values selected
        from ``identification``. A ``None`` return is treated as a successful
        validation and callback exceptions propagate unchanged.
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
        identification: dict[str, object] | None,
    ) -> dict[str, object]:
        dependency_values: dict[str, object] = {}
        for dependency_name in self.depends_on:
            if identification is None or dependency_name not in identification:
                raise KeyError(dependency_name)
            dependency_values[dependency_name] = identification[dependency_name]
        return dependency_values

    def cast(
        self,
        value: object,
        identification: dict[str, object] | None = None,
        *,
        cache_context: PossibleValuesCacheContext | None = None,
    ) -> object:
        """
        Convert a raw value to the configured input type and normalize it.

        ``cast`` does not apply scalar bounds, possible-value membership checks,
        or the validator callback. Interface validation calls
        :meth:`validate_bounds`, possible-value membership, and
        :meth:`validate_with_callable` as separate steps.

        Parameters:
            value: Raw value supplied by the caller.
            identification: Mapping of dependency input names to their current
                values, used by dynamic possible values, normalizers, and
                validators.

        Returns:
            Value converted to the target type, or ``None`` when ``value`` is
            ``None``.

        Raises:
            ValueError: If date, datetime, measurement, or fallback constructor
                conversion rejects the value.
            TypeError: If the configured type constructor or a callback cannot
                accept the supplied value/dependencies.
            KeyError: If normalization needs a declared dependency that is
                missing from ``identification``.
        """
        if value is None:
            return None
        if self.type == date:
            if isinstance(value, datetime) and type(value) is not date:
                cast_value = value.date()
            elif isinstance(value, date):
                cast_value = value
            else:
                cast_value = date.fromisoformat(cast(str, value))
            return self.normalize(
                cast_value,
                identification,
                cache_context=cache_context,
            )
        if self.type == datetime:
            if isinstance(value, datetime):
                cast_value = value
            elif isinstance(value, date):
                cast_value = datetime.combine(value, datetime.min.time())
            else:
                cast_value = datetime.fromisoformat(cast(str, value))
            return self.normalize(
                cast_value,
                identification,
                cache_context=cache_context,
            )
        if isinstance(value, self.type):
            return self.normalize(value, identification, cache_context=cache_context)
        if issubclass(self.type, GeneralManager):
            manager_type = self.type
            if isinstance(value, dict):
                return self.normalize(
                    manager_type(**value),
                    identification,
                    cache_context=cache_context,
                )
            return self.normalize(
                manager_type(id=value),
                identification,
                cache_context=cache_context,
            )
        if self.type == Measurement and isinstance(value, str):
            return self.normalize(
                Measurement.from_string(value),
                identification,
                cache_context=cache_context,
            )
        value_type = cast(Callable[[object], object], self.type)
        return self.normalize(
            value_type(value),
            identification,
            cache_context=cache_context,
        )
