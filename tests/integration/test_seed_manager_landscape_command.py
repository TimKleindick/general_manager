from __future__ import annotations

import json
from io import StringIO
from typing import ClassVar

import pytest
from django.core.management import CommandError, call_command
from django.db import connections, models
from django.db.models import CASCADE, CharField, ForeignKey

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from tests.utils.database import create_test_models, drop_test_models


class SeedBrokenError(RuntimeError):
    pass


class TestSeedManagerLandscapeCommand(GeneralManagerTransactionTestCase):
    databases: ClassVar[set[str]] = {"default", "secondary"}
    _secondary_created_models: ClassVar[list[type[models.Model]]] = []

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

        class SecondarySeedBatch(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = CharField(max_length=64)
                database = "secondary"

            class Factory:
                @staticmethod
                def create_batch(count: int) -> list[object]:
                    model = SecondarySeedBatch.Interface._model
                    created: list[object] = []
                    for index in range(count):
                        created.append(
                            model.objects.using("secondary").create(
                                name=f"secondary-{index}"
                            )
                        )
                        if index == 1:
                            raise SeedBrokenError
                    return created

        cls.SeedOwner = SeedOwner
        cls.SeedProject = SeedProject
        cls.SeedBroken = SeedBroken
        cls.SecondarySeedBatch = SecondarySeedBatch
        cls.general_manager_classes = [
            SeedOwner,
            SeedProject,
            SeedBroken,
            SecondarySeedBatch,
        ]
        super().setUpClass()
        cls._secondary_created_models = create_test_models(
            connections["secondary"],
            (
                SecondarySeedBatch.Interface._model,
                SecondarySeedBatch.Interface._model.history.model,
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cleanup_error: Exception | None = None
        try:
            with connections["secondary"].schema_editor() as editor:
                drop_test_models(editor, reversed(cls._secondary_created_models))
        except Exception as error:  # noqa: BLE001 - superclass cleanup must run.
            cleanup_error = error
        cls._secondary_created_models = []
        try:
            super().tearDownClass()
        except Exception as error:  # noqa: BLE001 - preserve the first cleanup error.
            if cleanup_error is None:
                cleanup_error = error
        if cleanup_error is not None:
            raise cleanup_error

    def test_command_requires_manager_or_all(self) -> None:
        with pytest.raises(CommandError, match="--manager"):
            call_command("seed_manager_landscape")

    def test_command_rejects_all_with_manager_selection(self) -> None:
        with pytest.raises(CommandError, match="--all and --manager"):
            call_command("seed_manager_landscape", all=True, manager=["SeedOwner"])

    def test_command_rejects_non_integer_programmatic_options(self) -> None:
        with pytest.raises(CommandError, match="--count must be an integer"):
            call_command(
                "seed_manager_landscape",
                manager=["SeedOwner"],
                count="bad",
            )

        with pytest.raises(CommandError, match="--batch-size must be an integer"):
            call_command(
                "seed_manager_landscape",
                manager=["SeedOwner"],
                batch_size="bad",
            )

    def test_command_rejects_non_positive_programmatic_counts(self) -> None:
        with pytest.raises(CommandError, match="--count must be greater than zero"):
            call_command(
                "seed_manager_landscape",
                manager=["SeedOwner"],
                count="0",
            )

        with pytest.raises(
            CommandError,
            match="--batch-size must be greater than zero",
        ):
            call_command(
                "seed_manager_landscape",
                manager=["SeedOwner"],
                batch_size="0",
            )

        with pytest.raises(
            CommandError,
            match="--batch-size must be greater than zero",
        ):
            call_command(
                "seed_manager_landscape",
                manager=["SeedOwner"],
                batch_size=0,
                dry_run=True,
            )

    def test_command_rejects_invalid_programmatic_option_types(self) -> None:
        invalid_calls = [
            {"manager": [object()]},
            {"target": [object()]},
            {"manager": ["SeedOwner"], "count": True},
            {"manager": ["SeedOwner"], "dry_run": "yes"},
        ]

        for kwargs in invalid_calls:
            with pytest.raises(CommandError):
                call_command("seed_manager_landscape", **kwargs)

    def test_command_accepts_programmatic_scalar_strings_and_none(self) -> None:
        stdout = StringIO()

        call_command(
            "seed_manager_landscape",
            manager="SeedOwner",
            target="SeedOwner=2",
            count="1",
            batch_size="1",
            dry_run=True,
            stdout=stdout,
        )

        assert "SeedOwner target=2" in stdout.getvalue()

        with pytest.raises(CommandError, match="--manager"):
            call_command("seed_manager_landscape", manager=None, target=None)

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
        failure_output = stderr.getvalue()
        assert "Seeding completed with failures:" in failure_output
        assert "SeedBroken" in failure_output
        assert "created=0" in failure_output
        assert "remaining=1" in failure_output
        assert "batch_size=1" in failure_output

    def test_same_secondary_batch_rolls_back_when_later_item_fails(self) -> None:
        """A failed secondary batch leaves both aliases without its partial rows."""
        model = self.SecondarySeedBatch.Interface._model

        with pytest.raises(CommandError, match="SecondarySeedBatch"):
            call_command(
                "seed_manager_landscape",
                manager=["SecondarySeedBatch"],
                target=["SecondarySeedBatch=2"],
                batch_size=2,
            )

        assert model.objects.using("secondary").count() == 0
        assert model.objects.using("default").count() == 0
