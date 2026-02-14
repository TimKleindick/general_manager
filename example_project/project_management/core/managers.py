from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from math import exp
from random import SystemRandom
from typing import Any, ClassVar, Optional

from factory import Sequence
from factory.declarations import LazyAttribute, LazyFunction
from django.contrib.auth.hashers import make_password
from django.db.models import (
    BigIntegerField,
    BooleanField,
    CharField,
    DateField,
    FloatField,
    ForeignKey,
    IntegerField,
    ManyToManyField,
    PROTECT,
    SET_NULL,
    TextField,
    constraints,
)
from django.db import transaction

from general_manager.interface import (
    CalculationInterface,
    DatabaseInterface,
    ExistingModelInterface,
    ReadOnlyInterface,
)
from general_manager.factory import (
    lazy_boolean,
    lazy_choice,
    lazy_date_between,
    lazy_delta_date,
    lazy_decimal,
    lazy_faker_sentence,
    lazy_integer,
)
from general_manager.manager import GeneralManager, Input, graph_ql_property
from general_manager.permission import ManagerBasedPermission, register_permission
from django.contrib.auth import get_user_model


LEGACY_CREATE_ALLOWED_IDENTIFIERS: set[str] = {
    "seed_user_1",
    "pm_admin",
}

LEGACY_MANAGEMENT_IDENTIFIERS: set[str] = {
    "seed_user_1",
    "pm_admin",
}

PHASE_IDS_WITH_FIXED_NOMINATION_PROBABILITY: set[int] = {3, 4, 5, 6, 7, 8}
_RNG = SystemRandom()


class ProjectCreationManagerAssignmentError(RuntimeError):
    pass


class ProjectCreationMissingCreatorError(ProjectCreationManagerAssignmentError):
    def __init__(self) -> None:
        super().__init__(
            "Project creation requires creator_id so program management can be assigned."
        )


class ProjectCreationCreatorNotFoundError(ProjectCreationManagerAssignmentError):
    def __init__(self, creator_id: int) -> None:
        super().__init__(f"Project creator user with id={creator_id} does not exist.")


class ProjectCreationRoleMissingError(ProjectCreationManagerAssignmentError):
    def __init__(self) -> None:
        super().__init__(
            "ProjectUserRole with id=1 (program management) does not exist."
        )


class ProjectCreationTeamEntryFailedError(ProjectCreationManagerAssignmentError):
    def __init__(self) -> None:
        super().__init__(
            "Failed to create project manager assignment for the new project."
        )


def _to_int(value: object | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_related_id(value: object | None) -> int | None:
    direct = _to_int(value)
    if direct is not None:
        return direct
    return _to_int(getattr(value, "id", None))


def _extract_identification_id(value: object | None) -> int | None:
    identification = getattr(value, "identification", None)
    if not isinstance(identification, dict):
        return None
    for key in ("id", "pk"):
        direct = _to_int(identification.get(key))
        if direct is not None:
            return direct
    for key, item in identification.items():
        if key.endswith("_id"):
            direct = _to_int(item)
            if direct is not None:
                return direct
    for item in identification.values():
        direct = _to_int(item)
        if direct is not None:
            return direct
    return None


def _request_user_id(user: object) -> int | None:
    return _to_int(getattr(user, "id", None))


def _resolve_project_id(instance: object) -> int | None:
    return (
        _extract_related_id(getattr(instance, "id", None))
        or _extract_identification_id(instance)
        or _extract_related_id(getattr(instance, "project_id", None))
        or _extract_related_id(getattr(instance, "project", None))
        or _extract_related_id(getattr(instance, "group_id", None))
    )


def _resolve_customer_id(instance: object) -> int | None:
    return (
        _extract_related_id(getattr(instance, "customer_id", None))
        or _extract_related_id(getattr(instance, "customer", None))
        or _extract_identification_id(getattr(instance, "customer", None))
    )


def _resolve_project_phase_type_id(instance: object) -> int | None:
    return _extract_related_id(
        getattr(instance, "project_phase_type_id", None)
    ) or _extract_related_id(getattr(instance, "project_phase_type", None))


def _resolve_user_identifier(instance: object) -> str | None:
    for field_name in ("microsoft_id", "username", "email"):
        value = getattr(instance, field_name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _generate_customer_volume_curve_points(
    *,
    customer_volume: object,
    datapoints: int | None = None,
    min_volume: int = 100,
    max_volume: int = 15000,
    total_volume: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    if min_volume > max_volume:
        min_volume, max_volume = max_volume, min_volume

    sop = getattr(customer_volume, "sop", None)
    eop = getattr(customer_volume, "eop", None)
    if not isinstance(sop, date):
        sop = date.today()
    if not isinstance(eop, date) or eop < sop:
        eop = sop

    total_days = max(0, (eop - sop).days)
    suggested_points = max(2, eop.year - sop.year + 1)
    requested_points = suggested_points if datapoints is None else int(datapoints)
    normalized_datapoints = max(2, min(requested_points, total_days + 1))

    generated_total_volume = total_volume
    if generated_total_volume is None:
        generated_total_volume = _RNG.randint(
            min_volume * normalized_datapoints,
            max_volume * normalized_datapoints,
        )

    center = (normalized_datapoints - 1) / 2
    spread = max(normalized_datapoints / 3, 1.0)
    raw_weights = [
        exp(-(((index - center) ** 2) / (2 * (spread**2))))
        for index in range(normalized_datapoints)
    ]
    weight_sum = sum(raw_weights) or 1.0

    volumes = [
        max(
            0,
            round((generated_total_volume * weight / weight_sum) * _RNG.uniform(0.9, 1.1)),
        )
        for weight in raw_weights
    ]
    delta = generated_total_volume - sum(volumes)
    peak_index = normalized_datapoints // 2
    volumes[peak_index] = max(0, volumes[peak_index] + delta)

    curve_points: list[dict[str, Any]] = []
    for index in range(normalized_datapoints):
        day_offset = round((index * total_days) / (normalized_datapoints - 1))
        curve_points.append(
            {
                **kwargs,
                "customer_volume": customer_volume,
                "volume_date": sop + timedelta(days=day_offset),
                "volume": volumes[index],
            }
        )
    return curve_points


@register_permission("isProjectRoleAny")
def _permission_is_project_role_any(instance, user, config: list[str]) -> bool:
    user_id = _request_user_id(user)
    if user_id is None:
        return False
    project_id = _resolve_project_id(instance)
    if project_id is None:
        return False
    role_ids = {_to_int(raw_id) for raw_id in config}
    role_ids.discard(None)
    if not role_ids:
        return False
    for team_entry in ProjectTeam.filter(
        project_id=project_id,
        responsible_user_id=user_id,
        active=True,
    ):
        if _to_int(getattr(team_entry, "project_user_role_id", None)) in role_ids:
            return True
    return False


@register_permission("isKeyAccountOfProjectCustomer")
def _permission_is_key_account_of_project_customer(
    instance, user, _config: list[str]
) -> bool:
    user_id = _request_user_id(user)
    if user_id is None:
        return False
    customer_id = _resolve_customer_id(instance)
    if customer_id is None:
        return False
    for customer in Customer.filter(id=customer_id):
        if _to_int(getattr(customer, "key_account_id", None)) == user_id:
            return True
    return False


@register_permission("isLegacyProjectCreateAllowed")
def _permission_is_legacy_project_create_allowed(
    _instance, user, _config: list[str]
) -> bool:
    user_id = _request_user_id(user)
    if user_id is None:
        return False

    user_manager = User.filter(id=user_id).first()
    identifier = _resolve_user_identifier(user_manager)
    if identifier in LEGACY_CREATE_ALLOWED_IDENTIFIERS:
        return True
    if identifier in LEGACY_MANAGEMENT_IDENTIFIERS:
        return True

    for customer in Customer.all():
        if _to_int(getattr(customer, "key_account_id", None)) == user_id:
            return True
    return False


@register_permission("canUpdateProbabilityOfNomination")
def _permission_can_update_probability_of_nomination(
    instance, user, _config: list[str]
) -> bool:
    phase_type_id = _resolve_project_phase_type_id(instance)
    if phase_type_id in PHASE_IDS_WITH_FIXED_NOMINATION_PROBABILITY:
        return False
    return _permission_is_project_role_any(instance, user, ["1"]) or (
        _permission_is_key_account_of_project_customer(instance, user, [])
    )


class ProjectUserRole(GeneralManager):
    id: int
    name: str

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "program management"},
        {"id": 2, "name": "sales"},
        {"id": 3, "name": "industrialization"},
        {"id": 4, "name": "product development"},
        {"id": 5, "name": "quality"},
        {"id": 6, "name": "procurement"},
        {"id": 7, "name": "logistics"},
        {"id": 8, "name": "welding supervisor"},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)


class ProjectPhaseType(GeneralManager):
    id: int
    name: str
    description: Optional[str]

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "request for information", "description": None},
        {"id": 2, "name": "request for quotation", "description": None},
        {"id": 3, "name": "nomination", "description": "acquired officially"},
        {"id": 4, "name": "production line release", "description": None},
        {"id": 5, "name": "tool release", "description": None},
        {"id": 6, "name": "series production", "description": None},
        {"id": 7, "name": "spare parts", "description": None},
        {"id": 8, "name": "end of production", "description": None},
        {"id": 9, "name": "internal", "description": None},
        {"id": 10, "name": "lost", "description": None},
        {"id": 11, "name": "prototypes", "description": None},
        {"id": 12, "name": "acquisition", "description": None},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=150, unique=True)
        description = TextField(null=True, blank=True)


class ProjectType(GeneralManager):
    id: int
    name: str

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "sustaining"},
        {"id": 2, "name": "growth"},
        {"id": 3, "name": "existing"},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)


class Currency(GeneralManager):
    id: int
    name: str
    abbreviation: str
    symbol: str

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "euro", "abbreviation": "eur", "symbol": "EUR"},
        {"id": 2, "name": "us-dollar", "abbreviation": "usd", "symbol": "USD"},
        {"id": 3, "name": "yuan", "abbreviation": "cny", "symbol": "CNY"},
        {"id": 4, "name": "swiss franc", "abbreviation": "chf", "symbol": "CHF"},
        {"id": 5, "name": "pound", "abbreviation": "gbp", "symbol": "GBP"},
        {"id": 6, "name": "australian dollar", "abbreviation": "aud", "symbol": "AUD"},
        {"id": 7, "name": "japanese yen", "abbreviation": "jpy", "symbol": "JPY"},
        {"id": 8, "name": "czech koruna", "abbreviation": "czk", "symbol": "CZK"},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)
        abbreviation = CharField(max_length=3, unique=True)
        symbol = CharField(max_length=8)


class DerivativeType(GeneralManager):
    id: int
    name: str
    abbreviation: Optional[str]

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "crash management system front", "abbreviation": "CMS Front"},
        {"id": 2, "name": "crash management system rear", "abbreviation": "CMS Rear"},
        {"id": 3, "name": "strut", "abbreviation": None},
        {"id": 4, "name": "side impact beam", "abbreviation": None},
        {"id": 5, "name": "body in white", "abbreviation": "BIW"},
        {"id": 6, "name": "sill", "abbreviation": None},
        {"id": 7, "name": "other", "abbreviation": None},
        {"id": 8, "name": "battery box", "abbreviation": None},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)
        abbreviation = CharField(max_length=32, null=True, blank=True)


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


class AccountNumber(GeneralManager):
    number: str
    is_project_account: bool
    network_number: Optional[int]

    class Interface(DatabaseInterface):
        number = CharField(max_length=16, unique=True)
        is_project_account = BooleanField(default=True)
        network_number = BigIntegerField(null=True, blank=True)

    class Factory:
        number = Sequence(lambda index: f"AC{index + 100000:08d}")
        is_project_account = lazy_boolean(0.7)
        network_number = lazy_integer(100_000_000, 999_999_999)


class Customer(GeneralManager):
    company_name: str
    group_name: str
    key_account: Optional[User]
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
            constraints = (
                constraints.UniqueConstraint(
                    fields=["company_name", "group_name"],
                    name="unique_customer_group_name",
                ),
            )

    class Factory:
        company_name = Sequence(lambda index: f"Customer Company {index + 1:04d}")
        group_name = Sequence(lambda index: f"Group {index + 1:04d}")
        number = Sequence(lambda index: 10_000 + index)


class Plant(GeneralManager):
    name: str
    plant_officer: Optional[User]
    plant_deputy_officer: Optional[User]
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

    class Factory:
        name = Sequence(lambda index: f"Plant-{index + 1:03d}")
        work_pattern_name = lazy_choice(["2-shift", "3-shift", "weekend support"])
        _plant_image_group_id = lazy_integer(1_000, 9_999_999)


class Project(GeneralManager):
    id: int
    name: str
    project_number: Optional[AccountNumber]
    project_phase_type: ProjectPhaseType
    project_type: Optional[ProjectType]
    currency: Currency
    project_image_group_id: Optional[int]
    customer: Customer
    probability_of_nomination: Optional[float]
    customer_volume_flex: Optional[float]

    class Interface(DatabaseInterface):
        name = CharField(max_length=255)
        project_number = ForeignKey(
            "AccountNumber",
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="project_number_for_projects",
        )
        invest_number = ManyToManyField(
            "AccountNumber",
            blank=True,
            related_name="invest_number_for_projects",
        )
        project_phase_type = ForeignKey("ProjectPhaseType", on_delete=PROTECT)
        project_type = ForeignKey(
            "ProjectType", on_delete=SET_NULL, null=True, blank=True
        )
        currency = ForeignKey("Currency", on_delete=PROTECT)
        project_image_group_id = BigIntegerField(null=True, blank=True)
        customer = ForeignKey("Customer", on_delete=PROTECT)
        probability_of_nomination = FloatField(null=True, blank=True)
        customer_volume_flex = FloatField(null=True, blank=True)

    class Factory:
        name = Sequence(lambda index: f"Project {index + 1:04d}")
        probability_of_nomination = lazy_decimal(0.01, 0.99, 4)
        customer_volume_flex = lazy_decimal(0.0, 0.4, 4)

    @graph_ql_property(sortable=True, filterable=True)
    def earliest_sop(self) -> Optional[date]:
        earliest = None
        for derivative in Derivative.filter(project_id=self.id):
            for volume in CustomerVolume.filter(derivative__id=derivative.id):
                if earliest is None or volume.sop < earliest:
                    earliest = volume.sop
        return earliest

    @graph_ql_property(sortable=True, filterable=True)
    def latest_eop(self) -> Optional[date]:
        latest = None
        for derivative in Derivative.filter(project_id=self.id):
            for volume in CustomerVolume.filter(derivative__id=derivative.id):
                if latest is None or volume.eop > latest:
                    latest = volume.eop
        return latest

    @graph_ql_property(sortable=True, filterable=True)
    def total_volume(self) -> int:
        project_id = _resolve_project_id(self)
        if project_id is None:
            return 0

        total = 0
        for derivative in Derivative.filter(project_id=project_id):
            derivative_id = _extract_related_id(
                getattr(derivative, "id", None)
            ) or _extract_identification_id(derivative)
            if derivative_id is None:
                continue
            for customer_volume in CustomerVolume.filter(derivative__id=derivative_id):
                customer_volume_id = _extract_related_id(
                    getattr(customer_volume, "id", None)
                ) or _extract_identification_id(customer_volume)
                if customer_volume_id is None:
                    continue
                for curve_point in CustomerVolumeCurvePoint.filter(
                    customer_volume_id=customer_volume_id
                ):
                    total += _to_int(getattr(curve_point, "volume", None)) or 0
        return total

    @classmethod
    def create(
        cls,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: Any,
    ) -> "Project":
        with transaction.atomic():
            project = super().create(
                creator_id=creator_id,
                history_comment=history_comment,
                ignore_permission=ignore_permission,
                **kwargs,
            )
            if creator_id is None:
                raise ProjectCreationMissingCreatorError()
            if not User.filter(id=creator_id).first():
                raise ProjectCreationCreatorNotFoundError(creator_id)
            if not ProjectUserRole.filter(id=1).first():
                raise ProjectCreationRoleMissingError()
            project_id = _resolve_project_id(project)
            if project_id is None:
                raise ProjectCreationTeamEntryFailedError()

            team_entry = ProjectTeam.filter(
                project_id=project_id,
                project_user_role_id=1,
            ).first()
            if team_entry is None:
                team_entry = ProjectTeam.create(
                    creator_id=creator_id,
                    ignore_permission=True,
                    project_id=project_id,
                    project_user_role_id=1,
                    responsible_user_id=creator_id,
                    active=True,
                )

            if team_entry is None:
                raise ProjectCreationTeamEntryFailedError()
            return project

    class Permission(ManagerBasedPermission):
        __read__: ClassVar[list[str]] = ["public"]
        __create__: ClassVar[list[str]] = ["isLegacyProjectCreateAllowed"]
        __update__: ClassVar[list[str]] = [
            "isProjectRoleAny:1,2,3,4,5,6,7",
            "isKeyAccountOfProjectCustomer",
        ]
        __delete__: ClassVar[list[str]] = __update__

        project_image_group_id: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2,3,4,5,6,7",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        project_number: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        project_phase_type: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        project_type: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        currency: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        customer: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        invest_number: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        name: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        customer_volume_flex: ClassVar[dict[str, list[str]]] = {
            "update": [
                "isProjectRoleAny:1,2",
                "isKeyAccountOfProjectCustomer",
            ]
        }
        probability_of_nomination: ClassVar[dict[str, list[str]]] = {
            "update": [
                "canUpdateProbabilityOfNomination",
            ]
        }


class Derivative(GeneralManager):
    project: Project
    name: str
    derivative_type: DerivativeType
    _plant: Plant
    derivative_image_group_id: Optional[int]
    pieces_per_car_set: int
    max_daily_quantity: Optional[int]
    norm_daily_quantity: Optional[int]
    volume_description: Optional[str]

    class Interface(DatabaseInterface):
        project = ForeignKey("Project", on_delete=PROTECT)
        name = CharField(max_length=255)
        derivative_type = ForeignKey("DerivativeType", on_delete=PROTECT)
        _plant = ForeignKey("Plant", on_delete=PROTECT)
        derivative_image_group_id = BigIntegerField(null=True, blank=True)
        pieces_per_car_set = IntegerField(default=1)
        max_daily_quantity = IntegerField(null=True, blank=True)
        norm_daily_quantity = IntegerField(null=True, blank=True)
        volume_description = TextField(null=True, blank=True)

        class Meta:
            constraints = (
                constraints.UniqueConstraint(
                    fields=["project", "name"],
                    name="unique_derivative_name_per_project",
                ),
            )

    class Factory:
        name = Sequence(lambda index: f"Derivative-{index + 1:04d}")
        pieces_per_car_set = lazy_integer(1, 8)
        max_daily_quantity = lazy_integer(300, 6500)
        norm_daily_quantity = lazy_integer(100, 4200)
        volume_description = lazy_faker_sentence(7)


class CustomerVolume(GeneralManager):
    derivative: Derivative
    project_phase_type: Optional[ProjectPhaseType]
    sop: date
    eop: date
    description: Optional[str]
    used_volume: bool
    is_volume_in_vehicles: bool

    class Interface(DatabaseInterface):
        derivative = ForeignKey(
            "Derivative",
            on_delete=PROTECT,
        )
        project_phase_type = ForeignKey(
            "ProjectPhaseType",
            on_delete=SET_NULL,
            null=True,
            blank=True,
        )
        sop = DateField()
        eop = DateField()
        description = TextField(null=True, blank=True)
        used_volume = BooleanField(default=False)
        is_volume_in_vehicles = BooleanField(default=False)

    class Factory:
        sop = lazy_date_between(date(2026, 1, 1), date(2031, 12, 31))
        eop = lazy_delta_date(365 * 5, "sop")
        description = lazy_faker_sentence(8)
        used_volume = lazy_boolean(0.7)
        is_volume_in_vehicles = lazy_boolean(0.35)


class CustomerVolumeCurvePoint(GeneralManager):
    customer_volume: CustomerVolume
    volume_date: date
    volume: int

    class Interface(DatabaseInterface):
        customer_volume = ForeignKey("CustomerVolume", on_delete=PROTECT)
        volume_date = DateField()
        volume = IntegerField()

        class Meta:
            constraints = (
                constraints.UniqueConstraint(
                    fields=["customer_volume", "volume_date"],
                    name="unique_customer_volume_curve_point",
                ),
            )

    class Factory:
        volume_date = LazyAttribute(lambda obj: obj.customer_volume.sop)
        volume = lazy_integer(100, 15000)
        _adjustmentMethod = staticmethod(_generate_customer_volume_curve_points)


class ProjectTeam(GeneralManager):
    project: Project
    project_user_role: ProjectUserRole
    responsible_user: User
    active: bool

    class Interface(DatabaseInterface):
        project = ForeignKey("Project", on_delete=PROTECT)
        project_user_role = ForeignKey("ProjectUserRole", on_delete=PROTECT)
        responsible_user = ForeignKey(
            get_user_model(),
            on_delete=PROTECT,
            related_name="responsible_for_project_teams",
        )
        active = BooleanField(default=True)

        class Meta:
            constraints = (
                constraints.UniqueConstraint(
                    fields=["project", "project_user_role"],
                    name="unique_project_role_entry",
                ),
            )

    class Factory:
        active = lazy_boolean(0.9)


class ProjectVolumeCurve(GeneralManager):
    project: Project

    class Interface(CalculationInterface):
        project = Input(Project)

    @graph_ql_property(filterable=True, sortable=True)
    def project_id(self) -> int | None:
        return _resolve_project_id(self.project)

    @graph_ql_property
    def project_name(self) -> str:
        return getattr(self.project, "name", "")

    @graph_ql_property
    def curve_json(self) -> str:
        project_id = _resolve_project_id(self.project)
        if project_id is None:
            return "[]"

        total_curve: dict[date, int] = defaultdict(int)
        used_curve: dict[date, int] = defaultdict(int)

        for derivative in Derivative.filter(project_id=project_id):
            derivative_id = _resolve_project_id(derivative) or _extract_related_id(
                getattr(derivative, "id", None)
            )
            if derivative_id is None:
                continue
            for customer_volume in CustomerVolume.filter(derivative__id=derivative_id):
                customer_volume_id = _extract_related_id(
                    getattr(customer_volume, "id", None)
                ) or _extract_identification_id(customer_volume)
                if customer_volume_id is None:
                    continue

                used_volume = bool(getattr(customer_volume, "used_volume", False))
                for curve_point in CustomerVolumeCurvePoint.filter(
                    customer_volume_id=customer_volume_id
                ):
                    point_date = getattr(curve_point, "volume_date", None)
                    point_volume = _to_int(getattr(curve_point, "volume", None)) or 0
                    if not isinstance(point_date, date):
                        continue
                    total_curve[point_date] += point_volume
                    if used_volume:
                        used_curve[point_date] += point_volume

        output = [
            {
                "date": point_date.isoformat(),
                "total_volume": total_curve[point_date],
                "used_volume": used_curve.get(point_date, 0),
            }
            for point_date in sorted(total_curve.keys())
        ]
        return json.dumps(output)
