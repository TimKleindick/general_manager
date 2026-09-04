"""Excel interface primitives."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any, Callable, ClassVar, Generic, TypeVar, cast

T = TypeVar("T")


class ExcelError(Exception):
    """Base error for Excel-backed interfaces."""


class ExcelStructureError(ExcelError):
    """Raised when workbook layout or row identity is invalid."""

    @classmethod
    def duplicate_key(cls, key: Any) -> "ExcelStructureError":
        """Build an error for a duplicate workbook key."""
        return cls(f"Duplicate Excel key {key!r}.")

    @classmethod
    def missing_column(cls, field_name: str) -> "ExcelStructureError":
        """Build an error for a missing declared column."""
        return cls(f"Missing Excel column for field {field_name!r}.")

    @classmethod
    def blank_key(cls) -> "ExcelStructureError":
        """Build an error for a blank workbook key."""
        return cls("Excel key value is blank.")


class ExcelWriteConflictError(ExcelError):
    """Raised when Excel changed before a GeneralManager write could be saved."""

    @classmethod
    def workbook_changed(cls, operation: str) -> ExcelWriteConflictError:
        """Build an error for an intervening workbook change."""
        return cls(
            f"Workbook changed during {operation}; retry after refreshing Excel."
        )

    @classmethod
    def existing_key(cls, key: Any) -> "ExcelWriteConflictError":
        """Build an error for a key that already exists."""
        return cls(
            "GeneralManager create for Excel key "
            f"{key!r} could not be saved because the key already exists in Excel. "
            "Excel has been refreshed and remains authoritative."
        )

    @classmethod
    def missing_row(cls, key: Any) -> "ExcelWriteConflictError":
        """Build an error for a row that no longer exists."""
        return cls(
            "GeneralManager write for Excel key "
            f"{key!r} could not be saved because the row is missing in Excel. "
            "Excel has been refreshed and remains authoritative."
        )

    @classmethod
    def changed_row(cls, key: Any) -> "ExcelWriteConflictError":
        """Build an error for a row changed since it was observed."""
        return cls(
            "GeneralManager write for Excel key "
            f"{key!r} could not be saved because the row changed in Excel. "
            "Excel has been refreshed and remains authoritative."
        )


class ExcelMutationPayloadError(ExcelError):
    """Raised when an Excel mutation payload is not supported."""

    @classmethod
    def unknown_field(cls, field_name: str) -> "ExcelMutationPayloadError":
        """Build an error for a field absent from the Excel schema."""
        return cls(f"Unknown Excel field {field_name!r}.")

    @classmethod
    def cannot_update_key(cls, field_name: str) -> "ExcelMutationPayloadError":
        """Build an error for an attempted key-field update."""
        return cls(f"Excel key field {field_name!r} cannot be updated.")


class ExcelValidationError(ExcelError):
    """Raised when a cell value cannot be parsed or validated."""

    preserve_attribute_evaluation_error: ClassVar[bool] = True

    @classmethod
    def required_blank(cls) -> "ExcelValidationError":
        """Build an error for a blank required field."""
        return cls("Required Excel value is blank.")

    @classmethod
    def cannot_parse(cls, value: Any, python_type_name: str) -> "ExcelValidationError":
        """Build an error for a value that cannot be parsed."""
        return cls(f"Cannot parse {value!r} as {python_type_name}.")

    @classmethod
    def exceeds_max_length(cls, value: str, max_length: int) -> "ExcelValidationError":
        """Build an error for text exceeding its maximum length."""
        return cls(f"Excel value {value!r} exceeds max_length={max_length}.")

    @classmethod
    def cannot_parse_decimal(cls, value: Any) -> "ExcelValidationError":
        """Build an error for an invalid decimal value."""
        return cls(f"Cannot parse {value!r} as Decimal.")

    @classmethod
    def exceeds_max_digits(
        cls, value: Decimal, max_digits: int
    ) -> "ExcelValidationError":
        """Build an error for a decimal exceeding its digit limit."""
        return cls(f"Excel value {value!r} exceeds max_digits={max_digits}.")


class ExcelLockError(ExcelError):
    """Raised when a workbook lock cannot be acquired."""


class ExcelConfigurationError(ValueError):
    """Raised when Excel interface metadata is invalid."""

    @classmethod
    def missing_meta(cls) -> "ExcelConfigurationError":
        """Build an error for a missing nested Excel ``Meta`` class."""
        return cls("ExcelInterface requires a nested Meta class.")

    @classmethod
    def missing_workbook(cls) -> "ExcelConfigurationError":
        """Build an error for a missing workbook setting."""
        return cls("ExcelInterface.Meta.workbook is required.")

    @classmethod
    def missing_sheet(cls) -> "ExcelConfigurationError":
        """Build an error for a missing worksheet setting."""
        return cls("ExcelInterface.Meta.sheet is required.")

    @classmethod
    def missing_key(cls) -> "ExcelConfigurationError":
        """Build an error for a missing key-field setting."""
        return cls("ExcelInterface.Meta.key is required.")

    @classmethod
    def ambiguous_location(cls) -> "ExcelConfigurationError":
        """Build an error for conflicting table and header-row settings."""
        return cls("Configure exactly one of Meta.table or Meta.header_row.")

    @classmethod
    def invalid_header_row(cls) -> "ExcelConfigurationError":
        """Build an error for a non-positive header-row setting."""
        return cls("ExcelInterface.Meta.header_row must be >= 1.")

    @classmethod
    def key_not_excel_field(cls) -> "ExcelConfigurationError":
        """Build an error when the key is not a declared Excel field."""
        return cls("ExcelInterface.Meta.key must name an ExcelField.")


@dataclass(frozen=True, slots=True)
class ExcelMeta:
    workbook: str
    sheet: str
    key: str
    table: str | None = None
    header_row: int | None = None
    cache_alias: str = "default"
    cache_version: str = "1"

    def __post_init__(self) -> None:
        if not self.workbook:
            raise ExcelConfigurationError.missing_workbook()
        if not self.sheet:
            raise ExcelConfigurationError.missing_sheet()
        if not self.key:
            raise ExcelConfigurationError.missing_key()
        if (self.table is None) == (self.header_row is None):
            raise ExcelConfigurationError.ambiguous_location()
        if self.header_row is not None and self.header_row < 1:
            raise ExcelConfigurationError.invalid_header_row()


@dataclass(frozen=True, slots=True)
class ExcelRowSnapshot:
    key: Any
    values: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ExcelSyncDelta:
    created: tuple[ExcelRowSnapshot, ...]
    updated: tuple[tuple[ExcelRowSnapshot, ExcelRowSnapshot], ...]
    deleted: tuple[ExcelRowSnapshot, ...]


def build_excel_meta(meta: type | None) -> ExcelMeta:
    """Validate and normalize an Excel interface's ``Meta`` configuration."""
    if meta is None:
        raise ExcelConfigurationError.missing_meta()
    return ExcelMeta(
        workbook=getattr(meta, "workbook", ""),
        sheet=getattr(meta, "sheet", ""),
        key=getattr(meta, "key", ""),
        table=getattr(meta, "table", None),
        header_row=getattr(meta, "header_row", None),
        cache_alias=getattr(meta, "cache_alias", "default"),
        cache_version=str(getattr(meta, "cache_version", "1")),
    )


@dataclass(frozen=True, slots=True, eq=False)
class ExcelField(Generic[T]):
    python_type: type[T]
    required: bool = True
    default: T | None = None
    header: str | None = None
    aliases: tuple[str, ...] = ()
    unique: bool = False
    editable: bool = True
    parser: Callable[[Any], T] | None = None
    dumper: Callable[[T | None], Any] | None = None

    def parse(self, value: Any) -> T | None:
        """Convert a workbook value to the field's declared Python type."""
        if value is None or value == "":
            if self.required and self.default is None:
                raise ExcelValidationError.required_blank()
            return self.default
        if self.parser is not None:
            return self.parser(value)
        try:
            converter = cast(Callable[[Any], T], self.python_type)
            return converter(value)
        except (TypeError, ValueError) as error:
            raise ExcelValidationError.cannot_parse(
                value, self.python_type.__name__
            ) from error

    def dump(self, value: T | None) -> Any:
        """Convert a Python value to its workbook representation."""
        if self.dumper is not None:
            return self.dumper(value)
        return value

    def header_candidates(self, name: str) -> tuple[str, ...]:
        """Return accepted workbook headers for this field."""
        return (self.header or name, *self.aliases)


class ExcelCharField(ExcelField[str]):
    max_length: int | None

    def __init__(
        self,
        *,
        max_length: int | None = None,
        required: bool = True,
        default: str | None = None,
        header: str | None = None,
        aliases: tuple[str, ...] = (),
        unique: bool = False,
        editable: bool = True,
    ) -> None:
        object.__setattr__(self, "max_length", max_length)
        super().__init__(
            str,
            required=required,
            default=default,
            header=header,
            aliases=aliases,
            unique=unique,
            editable=editable,
        )

    def parse(self, value: Any) -> str | None:
        """Parse and validate a workbook value as text."""
        parsed = super().parse(value)
        if (
            parsed is not None
            and self.max_length is not None
            and len(parsed) > self.max_length
        ):
            raise ExcelValidationError.exceeds_max_length(parsed, self.max_length)
        return parsed


class ExcelIntegerField(ExcelField[int]):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(int, **kwargs)


class ExcelDecimalField(ExcelField[Decimal]):
    max_digits: int | None
    decimal_places: int | None

    def __init__(
        self,
        *,
        max_digits: int | None = None,
        decimal_places: int | None = None,
        **kwargs: Any,
    ) -> None:
        object.__setattr__(self, "max_digits", max_digits)
        object.__setattr__(self, "decimal_places", decimal_places)
        super().__init__(Decimal, **kwargs)

    def parse(self, value: Any) -> Decimal | None:
        """Parse and validate a finite decimal workbook value."""
        if value is None or value == "":
            return super().parse(value)
        try:
            parsed = Decimal(str(value))
        except (DecimalException, ValueError) as error:
            raise ExcelValidationError.cannot_parse_decimal(value) from error
        if not parsed.is_finite():
            raise ExcelValidationError.cannot_parse_decimal(value)
        if self.decimal_places is not None:
            try:
                quantizer = Decimal("1").scaleb(-self.decimal_places)
                parsed = parsed.quantize(quantizer)
            except DecimalException as error:
                raise ExcelValidationError.cannot_parse_decimal(value) from error
        if (
            self.max_digits is not None
            and _decimal_digit_count(parsed) > self.max_digits
        ):
            raise ExcelValidationError.exceeds_max_digits(parsed, self.max_digits)
        return parsed


def _decimal_digit_count(value: Decimal) -> int:
    digits = value.as_tuple().digits
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        return len(digits)
    if exponent >= 0:
        if digits == (0,):
            return 1
        return len(digits) + exponent
    if abs(exponent) > len(digits):
        return abs(exponent)
    return len(digits)
