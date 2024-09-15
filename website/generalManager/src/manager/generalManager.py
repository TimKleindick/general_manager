from typing import Type, ClassVar
from django.db import models
from manager.meta import GeneralManagerMeta


class GeneralManager(metaclass=GeneralManagerMeta):
    _model: ClassVar[Type[models.Model]]

    def __init__(self, id, *args, **kwargs):
        self._instance = self._model.objects.get(pk=id)

    def __getattr__(self, item):
        if hasattr(self._instance, item):
            return getattr(self._instance, item)
        else:
            raise AttributeError(
                f"'{self.__class__.__name__}' Objekt hat kein Attribut '{item}'"
            )
