"""Django model mixins and helpers backing GeneralManager interfaces."""

from __future__ import annotations
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Callable, ClassVar, Protocol, TypeVar, cast

from django.db import models
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import FieldDoesNotExist
from simple_history.models import HistoricalRecords
from django.core.exceptions import ValidationError


if TYPE_CHECKING:
    from django.core.exceptions import ValidationErrorMessageArg

    from general_manager.manager.general_manager import GeneralManager

modelsModel = TypeVar("modelsModel", bound=models.Model)
ModelT = TypeVar("ModelT", bound=models.Model)
RuleMessage = str | Collection[str]
FullCleanMethod = Callable[
    [models.Model, Collection[str] | None, bool, bool],
    None,
]


class ModelValidationRule(Protocol):
    """Validation-rule methods consumed by the generated model cleaner."""

    def evaluate(self, obj: models.Model) -> bool | None:
        """Return whether the model instance satisfies this rule."""
        ...

    def get_error_message(self) -> Mapping[str, RuleMessage]:
        """Return field-keyed validation messages for a failed rule."""
        ...


class DjangoFullClean(Protocol):
    """Subset of Django's model validation API called by the generated cleaner."""

    def full_clean(
        self,
        exclude: Collection[str] | None = None,
        validate_unique: bool = True,
        validate_constraints: bool = True,
    ) -> None:
        """Validate a model instance and raise `ValidationError` on failure."""
        ...


def _normalize_rule_message(message: RuleMessage) -> list[str]:
    """Return a Django `ValidationError`-compatible list of message strings."""
    if isinstance(message, str):
        return [message]
    return list(message)


def _merge_error_messages(
    errors: dict[str, list[str]],
    new_errors: Mapping[str, RuleMessage],
) -> None:
    """Append field-keyed validation messages without dropping earlier errors."""
    for field_name, message in new_errors.items():
        errors.setdefault(field_name, []).extend(_normalize_rule_message(message))


def model_has_field(instance: models.Model, field_name: str) -> bool:
    """
    Return whether a Django model instance exposes a concrete field.

    Parameters:
        instance (models.Model): Model instance whose `_meta` field registry is
            inspected.
        field_name (str): Name of the field to look up.

    Returns:
        bool: `True` when `_meta.get_field(field_name)` succeeds, otherwise
            `False`.

    Raises:
        Exception: Exceptions other than Django's `FieldDoesNotExist` propagate
            unchanged from `_meta.get_field()`.
    """
    try:
        instance._meta.get_field(field_name)
    except FieldDoesNotExist:
        return False
    return True


def get_full_clean_methode(model: type[models.Model]) -> FullCleanMethod:
    """
    Create a custom `full_clean` method for a Django model that runs Django's standard validation and evaluates additional rule-based checks.

    The generated method calls the model's superclass `full_clean`, collects
    any `ValidationError.message_dict` entries, then iterates rules from
    `self._meta.rules` and merges messages from failing rules. Rules returning
    `True` or `None` are treated as passing; only `False` adds rule messages. If
    any errors are collected, the method raises a `ValidationError` containing
    the aggregated error mapping.

    Parameters:
        model (type[models.Model]): The Django model class for which to
            construct the `full_clean` method.

    Returns:
        FullCleanMethod: A `full_clean(self, exclude=None, validate_unique=True,
            validate_constraints=True)` function suitable for assignment to the
            model class.

    Raises:
        ValidationError: From Django validation and from failing rules, merged
            into one field-keyed error mapping.
    """

    def full_clean(
        self: models.Model,
        exclude: Collection[str] | None = None,
        validate_unique: bool = True,
        validate_constraints: bool = True,
    ) -> None:
        """
        Performs full validation on the model instance, including both standard Django validation and custom rule-based checks.

        Parameters:
            self (models.Model): Model instance being validated.
            exclude (Collection[str] | None): Field names Django should skip.
            validate_unique (bool): Whether Django should run unique checks.
            validate_constraints (bool): Whether Django should run constraint
                checks.

        Raises:
            ValidationError: When Django validation fails or when a rule returns
                `False`.
        """
        errors: dict[str, list[str]] = {}
        try:
            parent_model = cast(DjangoFullClean, super(model, self))
            parent_model.full_clean(
                exclude=exclude,
                validate_unique=validate_unique,
                validate_constraints=validate_constraints,
            )
        except ValidationError as e:
            errors.update(e.message_dict)

        rules = cast(
            list[ModelValidationRule],
            getattr(self._meta, "rules", []),
        )
        for rule in rules:
            if rule.evaluate(self) is False:
                error_message = rule.get_error_message()
                if error_message:
                    _merge_error_messages(errors, error_message)

        if errors:
            raise ValidationError(cast("ValidationErrorMessageArg", errors))

    return full_clean


class ActiveManager(models.Manager[ModelT]):
    """Default manager for soft-delete models that returns active rows only."""

    def get_queryset(self) -> models.QuerySet[ModelT]:
        """
        Retrieve a queryset filtered to objects where `is_active` is True.

        Returns:
            QuerySet[ModelT]: A queryset containing only active objects
                (`is_active=True`).
        """
        return super().get_queryset().filter(is_active=True)


class SoftDeleteMixin(models.Model):
    """
    Abstract Django model mixin that adds soft-delete manager support.

    `objects` filters to rows where `is_active=True`; `all_objects` exposes the
    unfiltered manager for code paths that need inactive rows.
    """

    is_active = models.BooleanField(default=True)
    objects = ActiveManager["SoftDeleteMixin"]()
    all_objects = models.Manager["SoftDeleteMixin"]()

    class Meta:
        abstract = True


class GeneralManagerBasisModel(models.Model):
    """
    Abstract base model providing shared metadata for GeneralManager storage.

    Subclasses receive simple-history tracking and a `_general_manager_class`
    class attribute pointing back to the owning manager class.
    """

    _general_manager_class: ClassVar[type[GeneralManager]]
    history = HistoricalRecords(inherit=True)

    class Meta:
        abstract = True


class SoftDeleteGeneralManagerModel(SoftDeleteMixin, GeneralManagerBasisModel):
    """Abstract base model combining GeneralManager metadata with soft delete."""

    class Meta:
        abstract = True


class GeneralManagerModel(GeneralManagerBasisModel):
    """Abstract model adding change-tracking metadata for writeable managers."""

    @property
    def _history_user(self) -> AbstractBaseUser | None:
        """
        Return the user recorded for the next simple-history entry.

        Returns:
            AbstractBaseUser | None: The transient `_gm_history_user` value when
            set, otherwise the model's `changed_by` value when that concrete
            field exists, otherwise `None`.
        """
        history_user = getattr(self, "_gm_history_user", None)
        if history_user is not None:
            return cast(AbstractBaseUser, history_user)
        if not model_has_field(self, "changed_by"):
            return None
        return cast(AbstractBaseUser | None, getattr(self, "changed_by", None))

    @_history_user.setter
    def _history_user(self, value: AbstractBaseUser | None) -> None:
        """
        Assign the given user as the author of the most recent change recorded for this model instance.

        Parameters:
            value (AbstractBaseUser | None): The user to associate with the latest modification, or `None` to clear the recorded user.

        Side effects:
            Stores the value on `_gm_history_user` for simple-history. When the
            model has a concrete `changed_by` field, mirrors the same value to
            that field; models without `changed_by` are left without a transient
            `changed_by` attribute.
        """
        self._gm_history_user = value
        if model_has_field(self, "changed_by"):
            self.changed_by = value

    class Meta:
        abstract = True
