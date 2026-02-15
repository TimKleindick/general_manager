from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from math import exp
from typing import TYPE_CHECKING, Any, Optional

from factory.declarations import LazyAttribute
from django.db.models import (
    BooleanField,
    DateField,
    ForeignKey,
    IntegerField,
    PROTECT,
    SET_NULL,
    TextField,
    constraints,
)

from general_manager.factory import (
    lazy_boolean,
    lazy_date_between,
    lazy_delta_date,
    lazy_faker_sentence,
    lazy_integer,
)
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

from .constants import _RNG
from .ids import (
    _extract_identification_id,
    _extract_related_id,
    _resolve_project_id,
    _to_int,
)

if TYPE_CHECKING:
    from .catalogs import ProjectPhaseType
    from .project_domain import Derivative, Project


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
            round(
                (generated_total_volume * weight / weight_sum) * _RNG.uniform(0.9, 1.1)
            ),
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


class CustomerVolume(GeneralManager):
    derivative: "Derivative"
    project_phase_type: Optional["ProjectPhaseType"]
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

        class Meta:
            app_label = "core"

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
            app_label = "core"
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


class ProjectVolumeCurve(GeneralManager):
    project: "Project"

    class Interface(CalculationInterface):
        from .project_domain import Project

        project = Input(Project)

        class Meta:
            app_label = "core"

    @graph_ql_property(filterable=True, sortable=True)
    def project_id(self) -> int | None:
        return _resolve_project_id(self.project)

    @graph_ql_property
    def project_name(self) -> str:
        return getattr(self.project, "name", "")

    @graph_ql_property
    def curve_json(self) -> str:
        from .project_domain import Derivative

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
