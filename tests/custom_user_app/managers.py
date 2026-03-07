from __future__ import annotations

from factory import LazyFunction, Sequence
from django.conf import settings
from django.db import models
from django.utils.crypto import get_random_string

from general_manager.interface import DatabaseInterface, ExistingModelInterface
from general_manager.manager.general_manager import GeneralManager


class User(GeneralManager):
    id: int
    username: str
    email: str
    is_active: bool

    class Interface(ExistingModelInterface):
        model = settings.AUTH_USER_MODEL

    class Factory:
        username = Sequence(lambda n: f"wrapped-user-{n}")
        email = Sequence(lambda n: f"wrapped-{n}@example.com")
        password = LazyFunction(lambda: get_random_string(16))


class Ticket(GeneralManager):
    id: int
    title: str
    owner: User

    class Interface(DatabaseInterface):
        title = models.CharField(max_length=100)
        owner = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            related_name="managed_tickets",
        )


__all__ = ["Ticket", "User"]
