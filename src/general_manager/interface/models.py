"""Django model mixins and helpers backing GeneralManager interfaces."""

from __future__ import annotations
from typing import Type, ClassVar, Any, Callable, TYPE_CHECKING, TypeVar
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from simple_history.models import HistoricalRecords  # type: ignore
from django.core.exceptions import ValidationError


if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.rule.rule import Rule

modelsModel = TypeVar("modelsModel", bound=models.Model)


def get_full_clean_methode(model: Type[models.Model]) -> Callable[..., None]:
    """
    Create a custom `full_clean` method for a Django model that runs Django's standard validation and evaluates additional rule-based checks.

    The generated method calls the model's superclass `full_clean`, collects any ValidationError messages, then iterates rules from `self._meta.rules` and merges any rule error messages. If any errors are collected, the method raises a `ValidationError` containing the aggregated error mapping.

    Parameters:
        model (Type[models.Model]): The Django model class for which to construct the `full_clean` method.

    Returns:
        Callable[..., None]: A `full_clean(self, *args, **kwargs)` function suitable for assignment to the model class; it raises `ValidationError` when validation or rule checks fail.
    """

    def full_clean(self: models.Model, *args: Any, **kwargs: Any) -> None:
        """
        Performs full validation on the model instance, including both standard Django validation and custom rule-based checks.

        Aggregates errors from Django's built-in validation and any additional rules defined in the model's `_meta.rules` attribute. Raises a `ValidationError` containing all collected errors if any validation or rule check fails.
        """
        errors: dict[str, Any] = {}
        try:
            super(model, self).full_clean(*args, **kwargs)  # type: ignore
        except ValidationError as e:
            errors.update(e.message_dict)

        rules: list[Rule] = getattr(self._meta, "rules", [])
        for rule in rules:
            if rule.evaluate(self) is False:
                error_message = rule.get_error_message()
                if error_message:
                    errors.update(error_message)

        if errors:
            raise ValidationError(errors)

    return full_clean


class ActiveManager(models.Manager):
    """Manager returning only rows marked as active."""

    def get_queryset(self) -> models.QuerySet[Any]:
        """
        Retrieve a queryset filtered to objects where `is_active` is True.

        Returns:
            QuerySet[Any]: A queryset containing only active objects (is_active == True).
        """
        return super().get_queryset().filter(is_active=True)


class SoftDeleteMixin(models.Model):
    """Mixin providing soft-delete support via an `is_active` flag."""

    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]
    objects = ActiveManager()  # type: ignore[var-annotated]
    all_objects = models.Manager()  # type: ignore[var-annotated]

    class Meta:
        abstract = True


class GeneralManagerBasisModel(models.Model):
    """Abstract base model providing shared fields for GeneralManager storage."""

    _general_manager_class: ClassVar[Type[GeneralManager]]
    history = HistoricalRecords(inherit=True)

    class Meta:
        abstract = True


class SoftDeleteGeneralManagerModel(SoftDeleteMixin, GeneralManagerBasisModel):
    """Soft-delete capable variant of the base GeneralManager model."""

    class Meta:  # type: ignore
        abstract = True


class GeneralManagerModel(GeneralManagerBasisModel):
    """Abstract model adding change-tracking metadata for writeable managers."""

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )  # type: ignore[var-annotated]
    changed_by_id: int | None

    @property
    def _history_user(self) -> AbstractUser | None:
        """
        Returns the user who last modified this model instance, or None if no user is set.
        """
        return self.changed_by

    @_history_user.setter
    def _history_user(self, value: AbstractUser | None) -> None:
        """
        Assign the given user as the author of the most recent change recorded for this model instance.

        Parameters:
            value (AbstractUser | None): The user to associate with the latest modification, or `None` to clear the recorded user.
        """
        self.changed_by = value

    class Meta:  # type: ignore
        abstract = True
