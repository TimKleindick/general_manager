from __future__ import annotations

import math
import random
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from general_manager.measurement import Measurement

from core.managers.catalogs import (
    Currency,
    DerivativeType,
    ProjectPhaseType,
    ProjectType,
    ProjectUserRole,
)
from core.managers.identity import User
from core.managers.master_data import AccountNumber, Customer, Plant
from core.managers.project_domain import Derivative, Project, ProjectTeam
from core.managers.volume_domain import CustomerVolume, CustomerVolumeCurvePoint

ERR_PROJECTS_MIN = "--projects must be at least 1."
ERR_MAX_DERIVATIVES_MIN = "--max-derivatives must be >= 0."
ERR_AVG_DERIVATIVES_MIN = "--avg-derivatives must be >= 0."
ERR_PHASE_TYPE_EMPTY = (
    "ProjectPhaseType is empty. Read-only seed data must exist first."
)
ERR_CURRENCY_EMPTY = "Currency is empty. Read-only seed data must exist first."
ERR_DERIVATIVE_TYPE_EMPTY = (
    "DerivativeType is empty. Read-only seed data must exist first."
)
ERR_USERS_PREPARE = "Failed to prepare users for creator_id usage."
ERR_USERS_MIN = "--users must be at least 1."
ERR_MANAGER_ID_RESOLVE = "Cannot resolve ID for manager instance."
ERR_TEAM_PROBABILITY_RANGE = "--extra-team-role-probability must be between 0 and 1."
ERR_MAX_EXTRA_TEAM_ROLES_MIN = "--max-extra-team-roles must be >= 0."
ERR_PROJECT_ROLE_EMPTY = (
    "ProjectUserRole is empty. Read-only seed data must exist first."
)
ERR_PROJECT_ROLE_PM_MISSING = (
    "ProjectUserRole with id=1 (program management) is required."
)


def _poisson_draw(lam: float, rng: random.Random) -> int:
    """Sample a Poisson random variable using Knuth's algorithm."""
    cutoff = math.exp(-lam)
    count = 0
    product = 1.0
    while product > cutoff:
        count += 1
        product *= rng.random()
    return count - 1


def _manager_id(manager_obj: Any) -> int:
    direct_id = getattr(manager_obj, "id", None)
    if isinstance(direct_id, int):
        return direct_id

    identification = getattr(manager_obj, "identification", None)
    if isinstance(identification, dict):
        for key in ("id", "pk"):
            value = identification.get(key)
            if isinstance(value, int):
                return value
        for value in identification.values():
            if isinstance(value, int):
                return value
    raise CommandError(ERR_MANAGER_ID_RESOLVE)


class Command(BaseCommand):
    help = (
        "Generate sample data for local testing: projects with derivatives and "
        "related managers."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--projects",
            type=int,
            default=800,
            help="Number of projects to create (default: 800).",
        )
        parser.add_argument(
            "--avg-derivatives",
            type=float,
            default=3.0,
            help="Average derivative count per project (Poisson lambda).",
        )
        parser.add_argument(
            "--max-derivatives",
            type=int,
            default=45,
            help="Maximum derivative count for one project.",
        )
        parser.add_argument(
            "--users",
            type=int,
            default=40,
            help="Number of domain users and matching auth users to ensure creator IDs.",
        )
        parser.add_argument(
            "--customers",
            type=int,
            default=120,
            help="Number of customers to create.",
        )
        parser.add_argument(
            "--plants",
            type=int,
            default=12,
            help="Number of  plants to create.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for deterministic data generation.",
        )
        parser.add_argument(
            "--extra-team-role-probability",
            type=float,
            default=0.65,
            help=(
                "Probability that a project gets additional team role assignments "
                "(default: 0.65)."
            ),
        )
        parser.add_argument(
            "--max-extra-team-roles",
            type=int,
            default=4,
            help="Maximum number of additional role assignments per project.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        project_target: int = options["projects"]
        avg_derivatives: float = options["avg_derivatives"]
        max_derivatives: int = options["max_derivatives"]
        user_target: int = options["users"]
        customer_target: int = options["customers"]
        plant_target: int = options["plants"]
        seed: int = options["seed"]
        extra_team_role_probability: float = options["extra_team_role_probability"]
        max_extra_team_roles: int = options["max_extra_team_roles"]

        if project_target < 1:
            raise CommandError(ERR_PROJECTS_MIN)
        if max_derivatives < 0:
            raise CommandError(ERR_MAX_DERIVATIVES_MIN)
        if avg_derivatives < 0:
            raise CommandError(ERR_AVG_DERIVATIVES_MIN)
        if user_target < 1:
            raise CommandError(ERR_USERS_MIN)
        if not 0 <= extra_team_role_probability <= 1:
            raise CommandError(ERR_TEAM_PROBABILITY_RANGE)
        if max_extra_team_roles < 0:
            raise CommandError(ERR_MAX_EXTRA_TEAM_ROLES_MIN)

        rng = random.Random(seed)  # noqa: S311
        self.stdout.write(
            self.style.NOTICE(
                f"Generating data with seed={seed}, projects={project_target}, "
                f"avg_derivatives={avg_derivatives}, max_derivatives={max_derivatives}."
            )
        )

        # Ensure read-only catalogs are available.
        project_phase_types = list(ProjectPhaseType.all())
        project_types = list(ProjectType.all())
        currencies = list(Currency.all())
        derivative_types = list(DerivativeType.all())
        project_user_roles = list(ProjectUserRole.all())
        if not project_phase_types:
            raise CommandError(ERR_PHASE_TYPE_EMPTY)
        if not currencies:
            raise CommandError(ERR_CURRENCY_EMPTY)
        if not derivative_types:
            raise CommandError(ERR_DERIVATIVE_TYPE_EMPTY)
        if not project_user_roles:
            raise CommandError(ERR_PROJECT_ROLE_EMPTY)
        additional_project_role_ids = [
            _manager_id(role) for role in project_user_roles if _manager_id(role) != 1
        ]
        role_by_id = {_manager_id(role): role for role in project_user_roles}
        if role_by_id.get(1) is None:
            raise CommandError(ERR_PROJECT_ROLE_PM_MISSING)

        users = sorted(
            [user for user in User.all() if getattr(user, "id", None) is not None],
            key=lambda user: int(user.id),  # type: ignore[arg-type]
        )
        if len(users) > user_target:
            keep_ids = {int(user.id) for user in users[:user_target]}
            get_user_model().objects.exclude(id__in=keep_ids).delete()
            users = sorted(
                [user for user in User.all() if getattr(user, "id", None) is not None],
                key=lambda user: int(user.id),  # type: ignore[arg-type]
            )
        while len(users) < user_target:
            users.append(
                User.Factory.create(
                    creator_id=None,
                    is_active=True,
                )
            )

        creator_ids = [
            user.id for user in users if getattr(user, "id", None) is not None
        ]
        if not creator_ids:
            raise CommandError(ERR_USERS_PREPARE)

        plants = list(Plant.all())
        while len(plants) < plant_target:
            plants.append(
                Plant.Factory.create(
                    creator_id=rng.choice(creator_ids),
                    ignore_permission=True,
                    changed_by=None,
                    changed_by_id=rng.choice(creator_ids),
                    plant_officer=None,
                    plant_deputy_officer=None,
                )
            )

        customers = list(Customer.all())
        while len(customers) < customer_target:
            creator_id = rng.choice(creator_ids)
            key_account_id = rng.choice(creator_ids)
            customer = Customer.Factory.create(
                creator_id=creator_id,
                ignore_permission=True,
                changed_by=None,
                changed_by_id=creator_id,
                key_account=None,
                key_account_id=key_account_id,
                sales_responsible=[],
            )
            customers.append(customer)
            # Randomly attach a couple of sales responsibles.
            sales_count = rng.randint(0, 3)
            if sales_count > 0:
                customer.update(
                    creator_id=creator_id,
                    ignore_permission=True,
                    sales_responsible_id_list=rng.sample(
                        creator_ids,
                        k=min(sales_count, len(creator_ids)),
                    ),
                )

        total_derivatives_created = 0
        projects_created = 0

        for _project_idx in range(project_target):
            creator_id = rng.choice(creator_ids)
            customer = rng.choice(customers)
            currency = rng.choice(currencies)
            phase_type = rng.choice(project_phase_types)
            project_type = rng.choice(project_types) if project_types else None

            project_number = AccountNumber.Factory.create(
                creator_id=creator_id,
                ignore_permission=True,
                changed_by=None,
                changed_by_id=creator_id,
                is_project_account=True,
            )

            invest_numbers: list[AccountNumber] = []
            for _invest_idx in range(rng.randint(0, 3)):
                invest_numbers.append(
                    AccountNumber.Factory.create(
                        creator_id=creator_id,
                        ignore_permission=True,
                        changed_by=None,
                        changed_by_id=creator_id,
                        is_project_account=False,
                    )
                )

            project = Project.Factory.create(
                creator_id=creator_id,
                ignore_permission=True,
                changed_by=None,
                changed_by_id=creator_id,
                project_number=project_number,
                project_phase_type=phase_type,
                project_type=project_type,
                currency=currency,
                customer=customer,
                probability_of_nomination=Measurement(
                    round(rng.uniform(0.0, 100.0), 4), "percent"
                ),
                customer_volume_flex=Measurement(
                    round(rng.uniform(0.0, 40.0), 4), "percent"
                ),
                invest_number=invest_numbers,
            )
            projects_created += 1
            project_id = _manager_id(project)

            # Keep role=1 (program management) assignment behavior from Project.create.
            if not ProjectTeam.filter(
                project_id=project_id,
                project_user_role_id=1,
            ).first():
                ProjectTeam.Factory.create(
                    creator_id=creator_id,
                    ignore_permission=True,
                    changed_by=None,
                    changed_by_id=creator_id,
                    project=project,
                    project_user_role=role_by_id.get(1),
                    responsible_user=None,
                    responsible_user_id=creator_id,
                    active=True,
                )

            if (
                additional_project_role_ids
                and rng.random() < extra_team_role_probability
            ):
                extra_role_count = rng.randint(
                    0,
                    min(max_extra_team_roles, len(additional_project_role_ids)),
                )
                if extra_role_count > 0:
                    for role_id in rng.sample(
                        additional_project_role_ids, k=extra_role_count
                    ):
                        if ProjectTeam.filter(
                            project_id=project_id,
                            project_user_role_id=role_id,
                        ).first():
                            continue
                        ProjectTeam.Factory.create(
                            creator_id=creator_id,
                            ignore_permission=True,
                            changed_by=None,
                            changed_by_id=creator_id,
                            project=project,
                            project_user_role=role_by_id.get(role_id),
                            responsible_user=None,
                            responsible_user_id=rng.choice(creator_ids),
                            active=(rng.random() > 0.05),
                        )

            derivative_count = min(
                max_derivatives,
                max(0, _poisson_draw(avg_derivatives, rng)),
            )
            for _derivative_idx in range(derivative_count):
                derivative = Derivative.Factory.create(
                    creator_id=creator_id,
                    ignore_permission=True,
                    changed_by=None,
                    changed_by_id=creator_id,
                    project=project,
                    derivative_type=rng.choice(derivative_types),
                    _plant=rng.choice(plants),
                    pieces_per_car_set=rng.randint(1, 8),
                    max_daily_quantity=rng.randint(100, 5000),
                    norm_daily_quantity=rng.randint(50, 3000),
                    volume_description="Generated test derivative",
                )
                total_derivatives_created += 1

                if rng.random() < 0.85:
                    customer_volume = CustomerVolume.Factory.create(
                        creator_id=creator_id,
                        ignore_permission=True,
                        changed_by=None,
                        changed_by_id=creator_id,
                        derivative=derivative,
                        project_phase_type=phase_type,
                        sop=date_from_year(rng.randint(2024, 2030)),
                        eop=date_from_year(rng.randint(2031, 2042)),
                        description="Generated volume profile",
                        used_volume=rng.random() < 0.7,
                        is_volume_in_vehicles=rng.random() < 0.3,
                    )
                    sop_year = customer_volume.sop.year
                    eop_year = customer_volume.eop.year
                    year_span = max(1, eop_year - sop_year + 1)
                    base_volume = rng.randint(1500, 12000)
                    CustomerVolumeCurvePoint.Factory.create(
                        creator_id=creator_id,
                        ignore_permission=True,
                        changed_by=None,
                        changed_by_id=creator_id,
                        customer_volume=customer_volume,
                        datapoints=year_span,
                        total_volume=base_volume * year_span,
                        min_volume=max(100, base_volume // 3),
                        max_volume=max(base_volume * 2, 1000),
                    )

        avg_created = total_derivatives_created / projects_created
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {projects_created} projects and "
                f"{total_derivatives_created} derivatives "
                f"(avg {avg_created:.2f} derivatives/project)."
            )
        )


def date_from_year(year: int):
    from datetime import date

    return date(year, 1, 1)
