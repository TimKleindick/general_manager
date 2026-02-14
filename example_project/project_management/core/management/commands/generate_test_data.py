from __future__ import annotations

import math
import random
from uuid import uuid4
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.managers import (
    AccountNumber,
    Plant,
    Currency,
    Customer,
    CustomerVolumeCurvePoint,
    CustomerVolume,
    Derivative,
    DerivativeType,
    Project,
    ProjectPhaseType,
    ProjectTeam,
    ProjectType,
    ProjectUserRole,
    User,
)

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
ERR_AUTH_USERS_PREPARE = "Failed to prepare auth users for creator_id usage."
ERR_ALIGNED_CREATORS = (
    "No aligned creator IDs found between auth users and domain users."
)
ERR_MANAGER_ID_RESOLVE = "Cannot resolve ID for manager instance."
ERR_TEAM_PROBABILITY_RANGE = "--extra-team-role-probability must be between 0 and 1."
ERR_MAX_EXTRA_TEAM_ROLES_MIN = "--max-extra-team-roles must be >= 0."
ERR_PROJECT_ROLE_EMPTY = (
    "ProjectUserRole is empty. Read-only seed data must exist first."
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

        UserModel = get_user_model()
        auth_users = list(UserModel.objects.order_by("id"))
        while len(auth_users) < user_target:
            idx = len(auth_users) + 1
            auth_users.append(
                UserModel.objects.create_user(
                    username=f"seed_user_{idx}",
                    email=f"seed_user_{idx}@example.local",
                )
            )

        auth_ids = [user.id for user in auth_users if user.id is not None][:user_target]
        if not auth_ids:
            raise CommandError(ERR_AUTH_USERS_PREPARE)
        bootstrap_creator_id = auth_ids[0]

        domain_users = list(User.all())
        while len(domain_users) < user_target:
            idx = len(domain_users) + 1
            domain_users.append(
                User.create(
                    creator_id=bootstrap_creator_id,
                    ignore_permission=True,
                    microsoft_id=f"seed-ms-{idx:04d}",
                    first_name=f"Seed{idx}",
                    last_name="User",
                    email=f"seed{idx}@example.local",
                    is_employed=True,
                )
            )

        # Keep domain and auth IDs aligned for creator_id checks in Project.create.
        aligned_creator_ids = [
            user_id
            for user_id in auth_ids
            if User.filter(id=user_id).first() is not None
        ]
        if not aligned_creator_ids:
            raise CommandError(ERR_ALIGNED_CREATORS)

        plants = list(Plant.all())
        while len(plants) < plant_target:
            idx = len(plants) + 1
            creator_id = rng.choice(aligned_creator_ids)
            plants.append(
                Plant.create(
                    creator_id=creator_id,
                    ignore_permission=True,
                    name=f"Plant-{idx:03d}",
                )
            )

        customers = list(Customer.all())
        while len(customers) < customer_target:
            idx = len(customers) + 1
            creator_id = rng.choice(aligned_creator_ids)
            key_account_id = rng.choice(aligned_creator_ids)
            customer = Customer.create(
                creator_id=creator_id,
                ignore_permission=True,
                company_name=f"Customer Company {idx:03d}",
                group_name=f"Group {idx:03d}",
                key_account_id=key_account_id,
                number=10000 + idx,
            )
            customers.append(customer)
            # Randomly attach a couple of sales responsibles.
            sales_count = rng.randint(0, 3)
            if sales_count > 0:
                customer.update(
                    creator_id=creator_id,
                    ignore_permission=True,
                    sales_responsible_id_list=rng.sample(
                        aligned_creator_ids,
                        k=min(sales_count, len(aligned_creator_ids)),
                    ),
                )

        total_derivatives_created = 0
        projects_created = 0

        for project_idx in range(project_target):
            creator_id = rng.choice(aligned_creator_ids)
            customer = rng.choice(customers)
            currency = rng.choice(currencies)
            phase_type = rng.choice(project_phase_types)
            project_type = rng.choice(project_types) if project_types else None

            project_number = AccountNumber.create(
                creator_id=creator_id,
                ignore_permission=True,
                number=f"AP{uuid4().hex[:10]}",
                is_project_account=True,
            )

            invest_ids: list[int] = []
            for _invest_idx in range(rng.randint(0, 3)):
                invest_number = AccountNumber.create(
                    creator_id=creator_id,
                    ignore_permission=True,
                    number=f"I{uuid4().hex[:10]}",
                    is_project_account=False,
                )
                invest_ids.append(_manager_id(invest_number))

            project = Project.create(
                creator_id=creator_id,
                ignore_permission=True,
                name=f"Project {project_idx + 1:04d}",
                project_number_id=_manager_id(project_number),
                project_phase_type_id=_manager_id(phase_type),
                project_type_id=(_manager_id(project_type) if project_type else None),
                currency_id=_manager_id(currency),
                customer_id=_manager_id(customer),
                probability_of_nomination=round(rng.uniform(0.0, 1.0), 4),
                customer_volume_flex=round(rng.uniform(0.0, 0.4), 4),
                invest_number_id_list=invest_ids,
            )
            projects_created += 1

            if (
                additional_project_role_ids
                and rng.random() < extra_team_role_probability
            ):
                extra_role_count = rng.randint(
                    0,
                    min(max_extra_team_roles, len(additional_project_role_ids)),
                )
                if extra_role_count > 0:
                    project_id = _manager_id(project)
                    for role_id in rng.sample(
                        additional_project_role_ids, k=extra_role_count
                    ):
                        if ProjectTeam.filter(
                            project_id=project_id,
                            project_user_role_id=role_id,
                        ).first():
                            continue
                        ProjectTeam.create(
                            creator_id=creator_id,
                            ignore_permission=True,
                            project_id=project_id,
                            project_user_role_id=role_id,
                            responsible_user_id=rng.choice(aligned_creator_ids),
                            active=(rng.random() > 0.05),
                        )

            derivative_count = min(
                max_derivatives,
                max(0, _poisson_draw(avg_derivatives, rng)),
            )
            for derivative_idx in range(derivative_count):
                derivative = Derivative.create(
                    creator_id=creator_id,
                    ignore_permission=True,
                    project_id=_manager_id(project),
                    name=f"Der-{project_idx + 1:04d}-{derivative_idx + 1:02d}",
                    derivative_type_id=_manager_id(rng.choice(derivative_types)),
                    _plant_id=_manager_id(rng.choice(plants)),
                    pieces_per_car_set=rng.randint(1, 8),
                    max_daily_quantity=rng.randint(100, 5000),
                    norm_daily_quantity=rng.randint(50, 3000),
                    volume_description="Generated test derivative",
                )
                total_derivatives_created += 1

                if rng.random() < 0.85:
                    customer_volume = CustomerVolume.create(
                        creator_id=creator_id,
                        ignore_permission=True,
                        derivative__id=_manager_id(derivative),
                        project_phase_type_id=_manager_id(phase_type),
                        sop=date_from_year(rng.randint(2024, 2030)),
                        eop=date_from_year(rng.randint(2031, 2042)),
                        description="Generated volume profile",
                        used_volume=rng.random() < 0.7,
                        is_volume_in_vehicles=rng.random() < 0.3,
                    )
                    customer_volume_id = _manager_id(customer_volume)
                    sop_year = customer_volume.sop.year
                    eop_year = customer_volume.eop.year
                    year_span = max(1, eop_year - sop_year + 1)
                    base_volume = rng.randint(1500, 12000)

                    for idx in range(year_span):
                        year = sop_year + idx
                        center = (year_span - 1) / 2
                        distance = abs(idx - center)
                        shape_factor = max(
                            0.25, 1.0 - (distance / max(center, 1.0)) * 0.85
                        )
                        volume_value = int(
                            base_volume * shape_factor * rng.uniform(0.8, 1.2)
                        )
                        CustomerVolumeCurvePoint.create(
                            creator_id=creator_id,
                            ignore_permission=True,
                            customer_volume_id=customer_volume_id,
                            volume_date=date_from_year(year),
                            volume=max(0, volume_value),
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
