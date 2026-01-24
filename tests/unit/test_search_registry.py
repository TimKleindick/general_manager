from __future__ import annotations

from typing import ClassVar

from django.test import SimpleTestCase

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.search.config import FieldConfig, IndexConfig
from general_manager.search.registry import (
    collect_index_settings,
    get_searchable_type_map,
)
from tests.utils.simple_manager_interface import BaseTestInterface


class AlphaInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}


class BetaInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}


class Alpha(GeneralManager):
    Interface = AlphaInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(
                name="global",
                fields=[FieldConfig(name="name", boost=2.0)],
                filters=["status"],
            )
        ]


class Beta(GeneralManager):
    Interface = BetaInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(
                name="global",
                fields=["description"],
                filters=["region"],
            )
        ]


class SearchRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        GeneralManagerMeta.all_classes = [Alpha, Beta]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [Alpha, Beta], [Alpha, Beta]
        )

    def test_collect_index_settings_merges_fields(self) -> None:
        settings = collect_index_settings("global")
        assert "name" in settings.searchable_fields
        assert "description" in settings.searchable_fields
        assert "status" in settings.filterable_fields
        assert "region" in settings.filterable_fields
        assert settings.field_boosts["name"] == 2.0

    def test_searchable_type_map(self) -> None:
        mapping = get_searchable_type_map()
        assert mapping["Alpha"].__name__ == "Alpha"
        assert mapping["Beta"].__name__ == "Beta"
