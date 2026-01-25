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
        """
        Retrieve the current instance's data record from the in-memory store.
        
        Parameters:
            search_date (optional): Ignored; present for compatibility with other implementations.
        
        Returns:
            The stored data record for this instance's `id`.
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Provide attribute type definitions for this interface.
        
        Returns:
            dict: Mapping from attribute name to a descriptor dict containing the Python type, e.g. {"name": {"type": str}}.
        """
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls):
        """
        Map attribute names to callables that extract those attributes from a manager interface.
        
        Returns:
            dict: Mapping where each key is an attribute name and each value is a callable that
            accepts an interface instance and returns that attribute's value.
        """
        return {"name": lambda interface: interface.get_data()["name"]}

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket of parent manager instances for the selected ids.
        
        Parameters:
            id__in (iterable, optional): Sequence of ids to include. If omitted, all ids from the class's data_store are used.
        
        Returns:
            SimpleBucket: A bucket containing instances of the parent manager created with each selected id.
        """
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
        """
        Prepare the test environment by initializing the general manager configuration and registry for the Project class.
        
        Sets up GeneralmanagerConfig with Project as both manager and index provider and assigns GeneralManagerMeta.all_classes to [Project] so tests run with a known class registry.
        """
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
        GeneralManagerMeta.all_classes = [Project]

    def test_index_config_helpers(self) -> None:
        """
        Verifies registry helper functions return the expected index configuration, index names, and searchable type mapping for the Project manager.
        
        Asserts that:
        - get_index_config(Project, "global") returns a config whose name is "global".
        - get_index_names() includes "global".
        - get_searchable_type_map() includes the key "Project".
        """
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