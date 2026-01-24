from __future__ import annotations

from typing import ClassVar

import pytest
from django.test import SimpleTestCase

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.search.config import IndexConfig
from general_manager.search.registry import (
    collect_index_settings,
    get_filterable_fields,
    get_index_config,
    get_index_names,
    get_searchable_type_map,
    iter_index_configs,
    validate_filter_keys,
    InvalidFilterFieldError,
)
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class _BaseInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {1: {"name": "Alpha"}}

    def get_data(self, search_date=None):
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls):
        return {"name": lambda interface: interface.get_data()["name"]}

    @classmethod
    def filter(cls, **kwargs):
        ids = kwargs.get("id__in") or list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class Project(GeneralManager):
    Interface = _BaseInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(
                name="global",
                fields=["name"],
                filters=["status"],
                sorts=["name"],
                boost=1.5,
            )
        ]


class SearchRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
        GeneralManagerMeta.all_classes = [Project]

    def test_index_config_helpers(self) -> None:
        config = get_index_config(Project, "global")
        assert config is not None
        assert config.name == "global"

        names = get_index_names()
        assert "global" in names

        type_map = get_searchable_type_map()
        assert "Project" in type_map

    def test_collect_index_settings(self) -> None:
        settings = collect_index_settings("global")
        assert "name" in settings.searchable_fields
        assert "status" in settings.filterable_fields
        assert "name" in settings.sortable_fields

    def test_iter_index_configs(self) -> None:
        entries = list(iter_index_configs("global"))
        assert entries
        manager_class, config = entries[0]
        assert manager_class is Project
        assert config.name == "global"

    def test_get_filterable_fields(self) -> None:
        fields = get_filterable_fields("global")
        assert "status" in fields

    def test_validate_filter_keys_raises(self) -> None:
        with pytest.raises(InvalidFilterFieldError):
            validate_filter_keys("global", {"not_allowed": "value"})
