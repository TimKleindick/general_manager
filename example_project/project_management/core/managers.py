from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any, ClassVar, Optional

from django.db.models import (
    BigIntegerField,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
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
    ReadOnlyInterface,
)
from general_manager.manager import GeneralManager, Input, graph_ql_property
from general_manager.permission import ManagerBasedPermission, register_permission


LEGACY_CREATE_ALLOWED_MICROSOFT_IDS: set[str] = {
    "test1234-5678-90ab-cdef12345678",
    "test2345-6789-0abc-def123456789",
}

LEGACY_MANAGEMENT_MICROSOFT_IDS: set[str] = {
    "test1234-5678-90ab-cdef12345678",
    "test2345-6789-0abc-def123456789",
    "test3456-7890-1bcd-ef1234567890",
    "test4567-8901-2cde-f12345678901",
    "test5678-9012-3cde-123456789012",
    "test6789-0123-4def-234567890123",
    "test7890-1234-5ef0-345678901234",
    "test8901-2345-6f01-456789012345",
}

PHASE_IDS_WITH_FIXED_NOMINATION_PROBABILITY: set[int] = {3, 4, 5, 6, 7, 8}


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
    microsoft_id = getattr(user_manager, "microsoft_id", None)
    if microsoft_id in LEGACY_CREATE_ALLOWED_MICROSOFT_IDS:
        return True
    if microsoft_id in LEGACY_MANAGEMENT_MICROSOFT_IDS:
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
    microsoft_id: str
    last_login: Optional[datetime]
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    job_title: Optional[str]
    office_location_name: Optional[str]
    cost_center: Optional[int]
    employee_identification_number: Optional[int]
    is_employed: bool
    supervisor_microsoft_id: Optional[str]
    allow_everybody_to_see_my_absence: bool

    class Interface(DatabaseInterface):
        microsoft_id = CharField(max_length=36, unique=True)
        last_login = DateTimeField(null=True, blank=True)
        first_name = CharField(max_length=255, null=True, blank=True)
        last_name = CharField(max_length=255, null=True, blank=True)
        email = CharField(max_length=255, null=True, blank=True)
        job_title = CharField(max_length=255, null=True, blank=True)
        office_location_name = CharField(max_length=255, null=True, blank=True)
        cost_center = IntegerField(null=True, blank=True)
        employee_identification_number = IntegerField(null=True, blank=True)
        is_employed = BooleanField(default=True)
        supervisor_microsoft_id = CharField(max_length=36, null=True, blank=True)
        allow_everybody_to_see_my_absence = BooleanField(default=True)

    @graph_ql_property(sortable=True, filterable=True)
    def full_name(self) -> str:
        first = (self.first_name or "").strip()
        last = (self.last_name or "").strip()
        full_name = f"{first} {last}".strip()
        if not full_name:
            return "Unknown User"
        if self.is_employed:
            return full_name
        return f"{full_name} (left company)"


class AccountNumber(GeneralManager):
    number: str
    is_project_account: bool
    network_number: Optional[int]

    class Interface(DatabaseInterface):
        number = CharField(max_length=16, unique=True)
        is_project_account = BooleanField(default=True)
        network_number = BigIntegerField(null=True, blank=True)


class Customer(GeneralManager):
    company_name: str
    group_name: str
    key_account: Optional[User]
    number: Optional[int]

    class Interface(DatabaseInterface):
        company_name = CharField(max_length=255)
        group_name = CharField(max_length=255)
        key_account = ForeignKey(
            "User",
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="key_account_for_customers",
        )
        sales_responsible = ManyToManyField(
            "User",
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


class Plant(GeneralManager):
    name: str
    plant_officer: Optional[User]
    plant_deputy_officer: Optional[User]
    work_pattern_name: Optional[str]
    _plant_image_group_id: Optional[int]

    class Interface(DatabaseInterface):
        name = CharField(max_length=255, unique=True)
        plant_officer = ForeignKey(
            "User",
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="plant_officer_for",
        )
        plant_deputy_officer = ForeignKey(
            "User",
            on_delete=SET_NULL,
            null=True,
            blank=True,
            related_name="plant_deputy_officer_for",
        )
        work_pattern_name = CharField(max_length=255, null=True, blank=True)
        _plant_image_group_id = BigIntegerField(null=True, blank=True)


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


class CustomerVolume(GeneralManager):
    derivative_: Derivative
    project_phase_type: Optional[ProjectPhaseType]
    sop: date
    eop: date
    description: Optional[str]
    used_volume: bool
    is_volume_in_vehicles: bool

    class Interface(DatabaseInterface):
        derivative_ = ForeignKey(
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


class ProjectTeam(GeneralManager):
    project: Project
    project_user_role: ProjectUserRole
    responsible_user: User
    active: bool

    class Interface(DatabaseInterface):
        project = ForeignKey("Project", on_delete=PROTECT)
        project_user_role = ForeignKey("ProjectUserRole", on_delete=PROTECT)
        responsible_user = ForeignKey("User", on_delete=PROTECT)
        active = BooleanField(default=True)

        class Meta:
            constraints = (
                constraints.UniqueConstraint(
                    fields=["project", "project_user_role"],
                    name="unique_project_role_entry",
                ),
            )


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
