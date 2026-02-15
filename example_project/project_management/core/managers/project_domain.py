from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from factory import Sequence
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import (
    BigIntegerField,
    BooleanField,
    CharField,
    FloatField,
    ForeignKey,
    IntegerField,
    ManyToManyField,
    PROTECT,
    SET_NULL,
    TextField,
    constraints,
)

from general_manager.factory import (
    lazy_boolean,
    lazy_decimal,
    lazy_faker_sentence,
    lazy_integer,
)
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager, graph_ql_property
from general_manager.permission import ManagerBasedPermission

from .exceptions import (
    ProjectCreationCreatorNotFoundError,
    ProjectCreationMissingCreatorError,
    ProjectCreationRoleMissingError,
    ProjectCreationTeamEntryFailedError,
)
from .ids import (
    _extract_identification_id,
    _extract_related_id,
    _resolve_project_id,
    _to_int,
)

if TYPE_CHECKING:
    from .catalogs import (
        Currency,
        DerivativeType,
        ProjectPhaseType,
        ProjectType,
        ProjectUserRole,
    )
    from .identity import User
    from .master_data import AccountNumber, Customer, Plant


class Project(GeneralManager):
    id: int
    name: str
    project_number: Optional["AccountNumber"]
    project_phase_type: "ProjectPhaseType"
    project_type: Optional["ProjectType"]
    currency: "Currency"
    project_image_group_id: Optional[int]
    customer: "Customer"
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

        class Meta:
            app_label = "core"

    class Factory:
        name = Sequence(lambda index: f"Project {index + 1:04d}")
        probability_of_nomination = lazy_decimal(0.01, 0.99, 4)
        customer_volume_flex = lazy_decimal(0.0, 0.4, 4)

    @graph_ql_property(sortable=True, filterable=True)
    def earliest_sop(self) -> Optional[date]:
        from .volume_domain import CustomerVolume

        earliest = None
        for derivative in Derivative.filter(project_id=self.id):
            for volume in CustomerVolume.filter(derivative__id=derivative.id):
                if earliest is None or volume.sop < earliest:
                    earliest = volume.sop
        return earliest

    @graph_ql_property(sortable=True, filterable=True)
    def latest_eop(self) -> Optional[date]:
        from .volume_domain import CustomerVolume

        latest = None
        for derivative in Derivative.filter(project_id=self.id):
            for volume in CustomerVolume.filter(derivative__id=derivative.id):
                if latest is None or volume.eop > latest:
                    latest = volume.eop
        return latest

    @graph_ql_property(sortable=True, filterable=True, warm_up=True)
    def total_volume(self) -> int:
        from .volume_domain import CustomerVolume, CustomerVolumeCurvePoint

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
        from .catalogs import ProjectUserRole
        from .identity import User

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
    derivative_type: "DerivativeType"
    _plant: "Plant"
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
            app_label = "core"
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


class ProjectTeam(GeneralManager):
    project: Project
    project_user_role: "ProjectUserRole"
    responsible_user: "User"
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
            app_label = "core"
            constraints = (
                constraints.UniqueConstraint(
                    fields=["project", "project_user_role"],
                    name="unique_project_role_entry",
                ),
            )

    class Factory:
        active = lazy_boolean(0.9)
