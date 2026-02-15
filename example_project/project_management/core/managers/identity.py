from __future__ import annotations

from datetime import datetime
from typing import Optional

from factory import Sequence
from factory.declarations import LazyAttribute, LazyFunction
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password

from general_manager.factory import lazy_boolean, lazy_choice
from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager, graph_ql_property


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
        username = Sequence(lambda index: f"pm_user_{index + 1:04d}")
        email = LazyAttribute(lambda obj: f"{obj.username}@example.local")
        first_name = lazy_choice(
            ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Sam", "Riley"]
        )
        last_name = lazy_choice(
            ["Miller", "Nguyen", "Brown", "Schmidt", "Garcia", "Patel", "Kim"]
        )
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
