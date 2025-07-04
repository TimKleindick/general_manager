from __future__ import annotations
from typing import Type, ClassVar, Any, Callable, TYPE_CHECKING, TypeVar, Generic
from django.db import models
from django.conf import settings
from simple_history.models import HistoricalRecords  # type: ignore
from django.core.exceptions import ValidationError


if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from django.contrib.auth.models import AbstractUser
    from general_manager.rule.rule import Rule

modelsModel = TypeVar("modelsModel", bound=models.Model)


def getFullCleanMethode(model: Type[models.Model]) -> Callable[..., None]:
    """
    Generates a custom `full_clean` method for a Django model that combines standard validation with additional rule-based checks.

    The returned method first performs Django's built-in model validation, then evaluates any custom rules defined in the model's `_meta.rules` attribute. If any validation or rule fails, a `ValidationError` is raised containing all collected errors.
    """

    def full_clean(self: models.Model, *args: Any, **kwargs: Any):
        errors: dict[str, Any] = {}
        try:
            super(model, self).full_clean(*args, **kwargs)  # type: ignore
        except ValidationError as e:
            errors.update(e.message_dict)

        rules: list[Rule] = getattr(self._meta, "rules")
        for rule in rules:
            if not rule.evaluate(self):
                error_message = rule.getErrorMessage()
                if error_message:
                    errors.update(error_message)

        if errors:
            raise ValidationError(errors)

    return full_clean


class GeneralManagerBasisModel(models.Model):
    _general_manager_class: ClassVar[Type[GeneralManager]]
    is_active = models.BooleanField(default=True)
    history = HistoricalRecords(inherit=True)

    class Meta:
        abstract = True


class GeneralManagerModel(GeneralManagerBasisModel):
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    changed_by_id: int | None

    @property
    def _history_user(self) -> AbstractUser | None:
        """
        Gets the user who last modified this model instance, or None if not set.

        Returns:
            AbstractUser | None: The user who last changed the instance, or None if unavailable.
        """
        return self.changed_by

    @_history_user.setter
    def _history_user(self, value: AbstractUser) -> None:
        """
        Sets the user responsible for the latest change to the model instance.

        Args:
            value: The user to associate with the change.
        """
        self.changed_by = value

    class Meta:  # type: ignore
        abstract = True
