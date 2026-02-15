from __future__ import annotations

from datetime import datetime
from typing import Optional

from factory import Sequence
from factory.declarations import LazyAttribute, LazyFunction
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password

from general_manager.factory import lazy_boolean
from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager, graph_ql_property


_USER_FIRST_NAMES = (
    "Alex",
    "Jordan",
    "Taylor",
    "Morgan",
    "Casey",
    "Sam",
    "Riley",
    "Avery",
    "Jamie",
    "Drew",
)
_USER_LAST_NAMES = (
    "Miller",
    "Nguyen",
    "Brown",
    "Schmidt",
    "Garcia",
    "Patel",
    "Kim",
    "Lopez",
    "Kowalski",
    "Rossi",
)


def _user_name_parts(index: int) -> tuple[str, str]:
    first = _USER_FIRST_NAMES[index % len(_USER_FIRST_NAMES)]
    last = _USER_LAST_NAMES[(index // len(_USER_FIRST_NAMES)) % len(_USER_LAST_NAMES)]
    return first, last


def _user_username(index: int) -> str:
    first, last = _user_name_parts(index)
    return f"{first.lower()}.{last.lower()}.{index + 1:04d}"


class User(GeneralManager):
    username: str
    last_login: Optional[datetime]
    first_name: Optional[str]
    last_name: Optional[str]
    email: str
    is_active: bool

    class Interface(ExistingModelInterface):
        model = get_user_model()

        class Meta:
            skip_history_registration = True

    class Factory:
        username = Sequence(_user_username)
        email = LazyAttribute(lambda obj: f"{obj.username}@example.local")
        first_name = Sequence(lambda index: _user_name_parts(index)[0])
        last_name = Sequence(lambda index: _user_name_parts(index)[1])
        is_active = lazy_boolean(0.92)
        password = LazyFunction(lambda: make_password("test-pass-123"))

    @graph_ql_property(sortable=True, filterable=True)
    def full_name(self) -> str:
        first = (self.first_name or "").strip()
        last = (self.last_name or "").strip()
        full_name = f"{first} {last}".strip() or (self.username or "").strip()
        if not full_name:
            return "Unknown User"
        if self.is_active:
            return full_name
        return f"{full_name} (inactive)"
