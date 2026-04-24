from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db.models import CASCADE, CharField, ForeignKey

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class SeedBrokenError(RuntimeError):
    pass


class TestSeedManagerLandscapeCommand(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class SeedOwner(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = CharField(max_length=64)

            class Factory:
                name = "Owner"

        class SeedProject(GeneralManager):
            name: str
            owner: SeedOwner

            class Interface(DatabaseInterface):
                name = CharField(max_length=64)
                owner = ForeignKey(SeedOwner.Interface._model, on_delete=CASCADE)

            class Factory:
                name = "Project"

        class SeedBroken(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = CharField(max_length=64)

            class Factory:
                @staticmethod
                def create_batch(_count: int) -> list[object]:
                    raise SeedBrokenError

        cls.SeedOwner = SeedOwner
        cls.SeedProject = SeedProject
        cls.SeedBroken = SeedBroken
        cls.general_manager_classes = [SeedOwner, SeedProject, SeedBroken]
        super().setUpClass()

    def test_command_requires_manager_or_all(self) -> None:
        with pytest.raises(CommandError, match="--manager"):
            call_command("seed_manager_landscape")

    def test_command_rejects_all_with_manager_selection(self) -> None:
        with pytest.raises(CommandError, match="--all and --manager"):
            call_command("seed_manager_landscape", all=True, manager=["SeedOwner"])

    def test_dry_run_prints_ordered_plan_without_creating_rows(self) -> None:
        stdout = StringIO()

        call_command(
            "seed_manager_landscape",
            manager=["SeedProject", "SeedOwner"],
            count=2,
            dry_run=True,
            stdout=stdout,
        )

        output = stdout.getvalue()
        assert "SeedOwner target=2" in output
        assert "SeedProject target=2" in output
        lines = output.splitlines()
        owner_line = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("SeedOwner target=")
        )
        project_line = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("SeedProject target=")
        )
        assert owner_line < project_line
        assert self.SeedOwner.all().count() == 0
        assert self.SeedProject.all().count() == 0

    def test_dry_run_can_print_json_plan(self) -> None:
        stdout = StringIO()

        call_command(
            "seed_manager_landscape",
            manager=["SeedProject", "SeedOwner"],
            count=2,
            dry_run=True,
            output_format="json",
            stdout=stdout,
        )

        rows = json.loads(stdout.getvalue())
        assert rows == [
            {
                "manager_name": "SeedOwner",
                "target_count": 2,
                "missing_dependencies": [],
            },
            {
                "manager_name": "SeedProject",
                "target_count": 2,
                "missing_dependencies": [],
            },
        ]

    def test_command_seeds_selected_managers_to_target_count(self) -> None:
        stdout = StringIO()

        call_command(
            "seed_manager_landscape",
            manager=["SeedOwner", "SeedProject"],
            target=["SeedOwner=1", "SeedProject=2"],
            batch_size=1,
            stdout=stdout,
        )

        assert self.SeedOwner.all().count() >= 1
        assert self.SeedProject.all().count() >= 2
        assert "SeedProject created=2" in stdout.getvalue()

    def test_command_writes_failure_summary_to_stderr(self) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with pytest.raises(CommandError) as exc_info:
            call_command(
                "seed_manager_landscape",
                manager=["SeedOwner", "SeedBroken"],
                count=1,
                continue_on_error=True,
                stdout=stdout,
                stderr=stderr,
            )

        assert str(exc_info.value) == "Seeding completed with failures"
        assert "SeedOwner created=1" in stdout.getvalue()
        assert "Seeding completed with failures:" in stderr.getvalue()
        assert "SeedBroken" in stderr.getvalue()
