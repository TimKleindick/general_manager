from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from factory import Sequence
from django.contrib.auth import get_user_model
from django.db.models import (
    BigIntegerField,
    BooleanField,
    CharField,
    ForeignKey,
    IntegerField,
    ManyToManyField,
    SET_NULL,
    constraints,
)

from general_manager.factory import lazy_boolean, lazy_choice, lazy_integer
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager

if TYPE_CHECKING:
    from .identity import User


_CUSTOMER_OEMS = (
    "Apex Motors",
    "Nordstern Automotive",
    "Helios Mobility",
    "Summit Vehicle Systems",
    "Vector Auto Group",
    "Orchid EV Technologies",
)
_CUSTOMER_REGIONS = ("EU", "NA", "APAC", "LATAM", "MEA")
_CUSTOMER_GROUPS = (
    "Powertrain Sourcing",
    "Body & Trim Procurement",
    "Electronics Platform Buying",
    "E-Mobility Commodity Purchasing",
    "Interior Module Purchasing",
)


def _customer_company_name(index: int) -> str:
    oem = _CUSTOMER_OEMS[index % len(_CUSTOMER_OEMS)]
    region = _CUSTOMER_REGIONS[(index // len(_CUSTOMER_OEMS)) % len(_CUSTOMER_REGIONS)]
    account_id = f"CUST-{index + 1:05d}"
    return f"{oem} ({region}) {account_id}"


def _customer_group_name(index: int) -> str:
    group = _CUSTOMER_GROUPS[index % len(_CUSTOMER_GROUPS)]
    region = _CUSTOMER_REGIONS[(index // len(_CUSTOMER_GROUPS)) % len(_CUSTOMER_REGIONS)]
    return f"{group} - {region}"


class AccountNumber(GeneralManager):
    number: str
    is_project_account: bool
    network_number: Optional[int]

    class Interface(DatabaseInterface):
        number = CharField(max_length=16, unique=True)
        is_project_account = BooleanField(default=True)
        network_number = BigIntegerField(null=True, blank=True)

        class Meta:
            app_label = "core"

    class Factory:
        number = Sequence(lambda index: f"AC{index + 100000:08d}")
        is_project_account = lazy_boolean(0.7)
        network_number = lazy_integer(100_000_000, 999_999_999)


class Customer(GeneralManager):
    company_name: str
    group_name: str
    key_account: Optional["User"]
    number: Optional[int]

    class Interface(DatabaseInterface):
        company_name = CharField(max_length=255)
        group_name = CharField(max_length=255)
        key_account = ForeignKey(
            get_user_model(),
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="key_account_for_customers",
        )
        sales_responsible = ManyToManyField(
            get_user_model(),
            blank=True,
            related_name="sales_responsible_for_customers",
        )
        number = IntegerField(null=True, blank=True)

        class Meta:
            app_label = "core"
            constraints = (
                constraints.UniqueConstraint(
                    fields=["company_name", "group_name"],
                    name="unique_customer_group_name",
                ),
            )

    class Factory:
        company_name = Sequence(_customer_company_name)
        group_name = Sequence(_customer_group_name)
        number = Sequence(lambda index: 10_000 + index)


class Plant(GeneralManager):
    name: str
    plant_officer: Optional["User"]
    plant_deputy_officer: Optional["User"]
    work_pattern_name: Optional[str]
    _plant_image_group_id: Optional[int]

    class Interface(DatabaseInterface):
        name = CharField(max_length=255, unique=True)
        plant_officer = ForeignKey(
            get_user_model(),
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="plant_officer_for",
        )
        plant_deputy_officer = ForeignKey(
            get_user_model(),
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="plant_deputy_officer_for",
        )
        work_pattern_name = CharField(max_length=255, null=True, blank=True)
        _plant_image_group_id = BigIntegerField(null=True, blank=True)

        class Meta:
            app_label = "core"

    class Factory:
        name = Sequence(lambda index: f"Plant-{index + 1:03d}")
        work_pattern_name = lazy_choice(["2-shift", "3-shift", "weekend support"])
        _plant_image_group_id = lazy_integer(1_000, 9_999_999)
