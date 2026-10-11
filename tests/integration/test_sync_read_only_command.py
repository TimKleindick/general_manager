"""Exercise explicit read-only maintenance against real generated models."""

from io import StringIO
from typing import ClassVar
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.test import override_settings

from general_manager.interface import ReadOnlyInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@override_settings(DEBUG=False, GENERAL_MANAGER={"READ_ONLY_SYNC_ON_STARTUP": False})
class SyncReadOnlyCommandTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class Country(GeneralManager):
            _data: ClassVar = [{"code": "DE"}]

            class Interface(ReadOnlyInterface):
                code = models.CharField(max_length=2, unique=True)

        class City(GeneralManager):
            _data: ClassVar = [{"name": "Berlin", "country": {"code": "DE"}}]

            class Interface(ReadOnlyInterface):
                name = models.CharField(max_length=30, unique=True)
                country = models.ForeignKey(Country.Interface._model, models.CASCADE)

        class Office(GeneralManager):
            _data: ClassVar = [{"name": "Headquarters", "city": {"name": "Berlin"}}]

            class Interface(ReadOnlyInterface):
                name = models.CharField(max_length=30, unique=True)
                city = models.OneToOneField(City.Interface._model, models.CASCADE)

        class Unrelated(GeneralManager):
            _data: ClassVar = [{"code": "unused"}]

            class Interface(ReadOnlyInterface):
                code = models.CharField(max_length=20, unique=True)

        cls.Country, cls.City, cls.Office, cls.Unrelated = (
            Country,
            City,
            Office,
            Unrelated,
        )
        cls.general_manager_classes = [Office, City, Country, Unrelated]

    def setUp(self):
        super().setUp()
        self.registry = patch.object(
            GeneralManagerMeta, "all_classes", list(self.general_manager_classes)
        )
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def test_manual_sync_when_auto_sync_disabled(self):
        assert self.Country.all().count() == 0
        stdout = StringIO()
        call_command("sync_read_only", stdout=stdout)
        assert self.Office.all().first().city.country.code == "DE"
        assert self.Unrelated.all().count() == 1
        assert "Synchronized 4 read-only managers" in stdout.getvalue()

    def test_selection_includes_transitive_dependencies_and_is_repeatable(self):
        stdout = StringIO()
        call_command(
            "sync_read_only",
            "--manager",
            "Office",
            "--manager",
            "Office",
            stdout=stdout,
        )
        assert self.Office.all().first().city.country.code == "DE"
        assert self.Unrelated.all().count() == 0
        lines = stdout.getvalue().splitlines()
        assert "Country" in lines[0]
        assert "City" in lines[1]
        assert "Office" in lines[2]
        assert "Synchronized 3 read-only managers" in lines[-1]
        call_command("sync_read_only", managers=["Office"], stdout=StringIO())
        assert self.Office.all().count() == 1

    def test_qualified_manager_selection(self):
        name = f"{self.Country.__module__}.{self.Country.__qualname__}"
        call_command("sync_read_only", managers=[name], stdout=StringIO())
        assert self.Country.all().count() == 1
        assert self.City.all().count() == 0

    def test_unknown_manager_rejected_before_any_writes(self):
        with pytest.raises(CommandError, match="Missing"):
            call_command("sync_read_only", managers=["Country", "Missing"])
        assert self.Country.all().count() == 0

    def test_schema_warning_fails_before_syncing_any_manager(self):
        capability = self.Unrelated.Interface.require_capability("read_only_management")
        from django.core.checks import Warning

        with (
            patch.object(
                capability,
                "ensure_schema_is_up_to_date",
                return_value=[Warning("migrate first")],
            ),
            pytest.raises(CommandError, match="migrate first"),
        ):
            call_command("sync_read_only")
        assert self.Country.all().count() == 0

    def test_bad_payload_raises_command_error_with_manager_context(self):
        with (
            patch.object(self.Country, "_data", None),
            pytest.raises(CommandError, match="Country"),
        ):
            call_command("sync_read_only", managers=["Country"])
        assert self.Country.all().count() == 0

    def test_ambiguous_short_name_fails_and_qualified_name_works(self):
        original_name = self.Unrelated.__name__
        self.Unrelated.__name__ = "Country"
        try:
            with pytest.raises(CommandError, match=r"[Aa]mbiguous"):
                call_command("sync_read_only", managers=["Country"])
            name = f"{self.Country.__module__}.{self.Country.__qualname__}"
            call_command("sync_read_only", managers=[name], stdout=StringIO())
        finally:
            self.Unrelated.__name__ = original_name
        assert self.Unrelated.all().count() == 0
        assert self.Country.all().count() == 1

    def test_invalid_programmatic_selection_fails(self):
        for value in (123, [123], [], [""]):
            with pytest.raises(CommandError):
                call_command("sync_read_only", managers=value)
        assert self.Country.all().count() == 0

    def test_dependency_cycle_fails_before_writes(self):
        from general_manager.interface.capabilities.read_only import (
            ReadOnlyManagementCapability,
        )

        with (
            patch.object(
                ReadOnlyManagementCapability,
                "_related_readonly_interfaces",
                side_effect=lambda interface: {self.City.Interface}
                if interface is self.Country.Interface
                else {self.Country.Interface},
            ),
            pytest.raises(CommandError, match=r"[Cc]ycl"),
        ):
            call_command("sync_read_only", managers=["Country"])
        assert self.Country.all().count() == 0

    def test_cli_synchronizes_once_and_keeps_unrelated_startup_hooks(self):
        from general_manager.management.commands.sync_read_only import Command
        from general_manager.interface.infrastructure.startup_hooks import (
            StartupHookEntry,
        )

        interface = self.Country.Interface
        capability = interface.require_capability("read_only_management")
        (automatic,) = capability.get_startup_hooks(interface)
        unrelated_calls = []
        entries = {
            interface: (
                StartupHookEntry(
                    automatic,
                    capability.get_startup_hook_dependency_resolver(interface),
                ),
                StartupHookEntry(lambda: unrelated_calls.append("ran"), None),
            )
        }
        with (
            override_settings(
                DEBUG=True, GENERAL_MANAGER={"READ_ONLY_SYNC_ON_STARTUP": True}
            ),
            patch(
                "general_manager.bootstrap.registered_startup_hook_entries",
                return_value=entries,
            ),
            patch.object(capability, "sync_data", wraps=capability.sync_data) as sync,
        ):
            Command(stdout=StringIO()).run_from_argv(
                ["manage.py", "sync_read_only", "--manager", "Country", "--skip-checks"]
            )
        assert self.Country.all().count() == 1
        assert sync.call_count == 1
        assert unrelated_calls == ["ran"]

    def test_cli_failure_exits_nonzero(self):
        from general_manager.management.commands.sync_read_only import Command

        with (
            patch.object(self.Country, "_data", None),
            pytest.raises(SystemExit) as error,
        ):
            Command(stdout=StringIO(), stderr=StringIO()).run_from_argv(
                ["manage.py", "sync_read_only", "--manager", "Country", "--skip-checks"]
            )
        assert error.value.code == 1

    def test_no_registered_managers_is_a_successful_noop(self):
        stdout = StringIO()
        with patch.object(GeneralManagerMeta, "all_classes", []):
            call_command("sync_read_only", stdout=stdout)
        assert "Synchronized 0 read-only managers" in stdout.getvalue()
