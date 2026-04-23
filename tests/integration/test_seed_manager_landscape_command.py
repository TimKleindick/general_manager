from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db.models import CASCADE, CharField, ForeignKey

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


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

        cls.SeedOwner = SeedOwner
        cls.SeedProject = SeedProject
        cls.general_manager_classes = [SeedOwner, SeedProject]
        super().setUpClass()

    def test_command_requires_manager_or_all(self) -> None:
        with pytest.raises(CommandError, match="--manager"):
            call_command("seed_manager_landscape")

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
        assert output.index("SeedOwner") < output.index("SeedProject")
        assert self.SeedOwner.all().count() == 0
        assert self.SeedProject.all().count() == 0

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
