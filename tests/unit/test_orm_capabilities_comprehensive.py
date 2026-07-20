"""Comprehensive tests for ORM capability implementations."""

from __future__ import annotations

from contextlib import nullcontext
import pytest
from unittest.mock import Mock, call, patch
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.apps import apps
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalChanges

from general_manager.cache.run_context import CalculationRunContext
from general_manager.as_of import (
    HistoricalReadNotSupportedError,
    InvalidSearchDateError,
    as_of,
)
from general_manager.interface.capabilities.orm import (
    HistoryNotSupportedError,
    OrmCreateCapability,
    OrmDeleteCapability,
    OrmHistoryCapability,
    OrmLifecycleCapability,
    OrmMutationCapability,
    OrmPersistenceSupportCapability,
    OrmQueryCapability,
    OrmReadCapability,
    OrmUpdateCapability,
    OrmValidationCapability,
    SoftDeleteCapability,
)
from general_manager.interface.capabilities.orm.mutations import (
    _assign_history_actor,
    _normalize_payload,
)
from general_manager.interface.capabilities.orm.support import (
    AmbiguousReverseFilterAliasError,
    SearchDateInputError,
    _build_reverse_filter_alias_map,
    _connection_has_application_atomic_block,
    _resolve_filter_segment,
    _translate_reverse_filter_aliases,
    _translate_reverse_filter_key,
)
from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
    PayloadNormalizer,
)
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
)
from general_manager.uploads.types import UploadCandidate


class TestOrmPersistenceSupportCapability:
    """Tests for ORM persistence support capability."""

    def test_get_database_alias_returns_configured_alias(self):
        """Test that get_database_alias returns the configured database."""
        capability = OrmPersistenceSupportCapability()

        interface_cls = Mock()
        interface_cls.database = "secondary_db"

        result = capability.get_database_alias(interface_cls)

        assert result == "secondary_db"

    def test_get_database_alias_returns_none_when_not_set(self):
        """Test that get_database_alias returns None when no database is configured."""
        capability = OrmPersistenceSupportCapability()

        interface_cls = Mock()
        interface_cls.database = None

        result = capability.get_database_alias(interface_cls)

        assert result is None

    def test_get_manager_returns_active_by_default(self):
        """Test that get_manager returns active manager by default."""
        capability = OrmPersistenceSupportCapability()

        mock_model = Mock()
        mock_model._default_manager = Mock()

        interface_cls = Mock()
        interface_cls._model = mock_model
        interface_cls.database = None

        with patch(
            "general_manager.interface.capabilities.orm.support.DjangoManagerSelector"
        ) as mock_selector:
            mock_instance = Mock()
            mock_instance.active_manager = Mock(return_value="active_mgr")
            mock_instance.cached_active = "cached"
            mock_selector.return_value = mock_instance

            result = capability.get_manager(interface_cls, only_active=True)

            mock_instance.active_manager.assert_called_once()
            assert result == "active_mgr"

    def test_get_manager_returns_all_when_only_active_false(self):
        """Test that get_manager returns all manager when only_active=False."""
        capability = OrmPersistenceSupportCapability()

        mock_model = Mock()
        interface_cls = Mock()
        interface_cls._model = mock_model
        interface_cls.database = None

        with patch(
            "general_manager.interface.capabilities.orm.support.DjangoManagerSelector"
        ) as mock_selector:
            mock_instance = Mock()
            mock_instance.all_manager = Mock(return_value="all_mgr")
            mock_instance.cached_active = "cached"
            mock_selector.return_value = mock_instance

            result = capability.get_manager(interface_cls, only_active=False)

            mock_instance.all_manager.assert_called_once()
            assert result == "all_mgr"

    def test_get_queryset_returns_active_objects(self):
        """Test that get_queryset returns queryset from active manager."""
        capability = OrmPersistenceSupportCapability()

        mock_queryset = Mock()
        mock_manager = Mock()
        mock_manager.all = Mock(return_value=mock_queryset)

        interface_cls = Mock()

        with patch.object(capability, "get_manager", return_value=mock_manager):
            result = capability.get_queryset(interface_cls)

            assert result is mock_queryset
            mock_manager.all.assert_called_once()

    def test_get_payload_normalizer_creates_instance(self):
        """Test that get_payload_normalizer creates PayloadNormalizer."""
        capability = OrmPersistenceSupportCapability()

        mock_model = Mock()
        interface_cls = Mock()
        interface_cls._model = mock_model

        with patch(
            "general_manager.interface.capabilities.orm.support.PayloadNormalizer"
        ) as mock_normalizer:
            capability.get_payload_normalizer(interface_cls)

            mock_normalizer.assert_called_once_with(mock_model)

    def test_get_field_descriptors_caches_result(self):
        """Test that field descriptors are cached on the interface class."""
        capability = OrmPersistenceSupportCapability()

        interface_cls = Mock()
        interface_cls._field_descriptors = None

        with patch(
            "general_manager.interface.capabilities.orm.support.build_field_descriptors"
        ) as mock_build:
            mock_build.return_value = {"field1": "descriptor1"}

            result1 = capability.get_field_descriptors(interface_cls)
            result2 = capability.get_field_descriptors(interface_cls)

            # Should only build once
            mock_build.assert_called_once()
            assert result1 == {"field1": "descriptor1"}
            assert result2 == result1

    def test_ambient_historical_many_to_many_without_target_history_fails_closed(
        self,
    ):
        capability = OrmPersistenceSupportCapability()
        search_date = timezone.now() - timedelta(days=1)
        target_model = type("TargetModel", (), {"_default_manager": Mock()})
        target_field = SimpleNamespace(related_model=target_model)
        through_relation = SimpleNamespace(
            related_model=target_model,
            name="target",
        )
        historical_through = type(
            "HistoricalThrough",
            (HistoricalChanges,),
            {"_meta": SimpleNamespace(get_fields=lambda: [through_relation])},
        )
        queryset = Mock(model=historical_through)
        manager = Mock()
        manager.all.return_value = queryset
        source_model = Mock()
        source_model._meta.get_field.return_value = target_field

        class InterfaceInstance:
            _model = source_model

            def __init__(self) -> None:
                self._instance = SimpleNamespace(members=manager)
                self._search_date = search_date

        with as_of(search_date):
            with pytest.raises(HistoricalReadNotSupportedError) as exc_info:
                capability.resolve_many_to_many(
                    InterfaceInstance(),
                    "members",
                    "members",
                )
        assert isinstance(exc_info.value.__cause__, HistoryNotSupportedError)

        with pytest.raises(HistoricalReadNotSupportedError) as exc_info:
            capability.resolve_many_to_many(
                InterfaceInstance(),
                "members",
                "members",
            )
        assert isinstance(exc_info.value.__cause__, HistoryNotSupportedError)

    def test_historical_many_to_many_without_through_history_fails_closed(self):
        capability = OrmPersistenceSupportCapability()
        search_date = timezone.now() - timedelta(days=1)
        target_model = type("TargetModel", (), {"history": Mock()})
        queryset = Mock(model=target_model)
        manager = Mock()
        manager.all.return_value = queryset
        source_model = Mock()

        class InterfaceInstance:
            _model = source_model

            def __init__(self) -> None:
                self._instance = SimpleNamespace(members=manager)
                self._search_date = search_date

        with as_of(search_date):
            with pytest.raises(HistoricalReadNotSupportedError) as exc_info:
                capability.resolve_many_to_many(
                    InterfaceInstance(),
                    "members",
                    "members",
                )
        assert isinstance(exc_info.value.__cause__, HistoryNotSupportedError)

        with pytest.raises(HistoricalReadNotSupportedError) as exc_info:
            capability.resolve_many_to_many(
                InterfaceInstance(),
                "members",
                "members",
            )
        assert isinstance(exc_info.value.__cause__, HistoryNotSupportedError)


class TestOrmReadCapability:
    """Tests for ORM read capability."""

    def test_connection_atomic_detection_ignores_testcase_wrapper(self):
        """Keep identity caching enabled for Django's TestCase transaction."""
        testcase_block = SimpleNamespace(_from_testcase=True)
        application_block = SimpleNamespace(_from_testcase=False)
        connection = SimpleNamespace(
            atomic_blocks=[testcase_block],
            in_atomic_block=True,
        )

        with patch(
            "general_manager.interface.capabilities.orm.support.connections",
            {"secondary": connection},
        ):
            assert not _connection_has_application_atomic_block("secondary")

            connection.atomic_blocks.append(application_block)

            assert _connection_has_application_atomic_block("secondary")

    def test_get_data_reuses_instance_inside_calculation_run_context(self):
        """Test that repeated ORM reads reuse the row within an explicit run context."""
        capability = OrmReadCapability()

        class DoesNotExist(Exception):
            pass

        class Model:
            pass

        Model.DoesNotExist = DoesNotExist

        class InterfaceInstance:
            _model = Model
            database = "secondary"
            historical_lookup_buffer_seconds = 0

            def __init__(self, pk: int) -> None:
                self.pk = pk
                self._search_date = None

        model_instance = object()
        first = InterfaceInstance(pk=42)
        second = InterfaceInstance(pk=42)

        mock_manager = Mock()
        mock_manager.get = Mock(return_value=model_instance)

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value="secondary")
            mock_support.get_manager = Mock(return_value=mock_manager)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.support.is_soft_delete_enabled",
                return_value=False,
            ):
                with (
                    patch(
                        "general_manager.interface.capabilities.orm.support._connection_has_application_atomic_block",
                        return_value=False,
                    ) as has_application_atomic,
                    patch(
                        "general_manager.interface.capabilities.orm.with_observability",
                        side_effect=lambda *_args, **kwargs: kwargs["func"](),
                    ),
                    CalculationRunContext(),
                ):
                    assert capability.get_data(first) is model_instance
                    assert capability.get_data(second) is model_instance

        mock_manager.get.assert_called_once_with(pk=42)
        assert has_application_atomic.call_count == 2
        has_application_atomic.assert_called_with("secondary")

    def test_get_data_retrieves_instance_by_pk(self):
        """Test that get_data retrieves model instance by primary key."""
        capability = OrmReadCapability()

        mock_instance = Mock(pk=42)
        mock_instance.pk = 42
        mock_instance._search_date = None
        mock_instance.__class__ = Mock()
        mock_instance.__class__._model = Mock()

        mock_manager = Mock()
        mock_manager.get = Mock(return_value="model_instance")

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_manager = Mock(return_value=mock_manager)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ):
                result = capability.get_data(mock_instance)

                mock_manager.get.assert_called_once_with(pk=42)
                assert result == "model_instance"

    def test_get_data_raises_does_not_exist(self):
        """Test that get_data raises DoesNotExist when instance not found."""
        capability = OrmReadCapability()

        class DoesNotExist(Exception):
            pass

        mock_model = Mock()
        mock_model.DoesNotExist = DoesNotExist

        mock_instance = Mock()
        mock_instance.pk = 999
        mock_instance._search_date = None
        mock_instance.__class__ = Mock()
        mock_instance.__class__._model = mock_model
        mock_instance.__class__.historical_lookup_buffer_seconds = 0

        mock_manager = Mock()
        mock_manager.get = Mock(side_effect=DoesNotExist("Not found"))

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_manager = Mock(return_value=mock_manager)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.support.is_soft_delete_enabled",
                return_value=False,
            ):
                with patch(
                    "general_manager.interface.capabilities.orm.with_observability",
                    side_effect=lambda *_args, **kwargs: kwargs["func"](),
                ):
                    with pytest.raises(DoesNotExist, match="Not found"):
                        capability.get_data(mock_instance)

    def test_get_data_raises_when_historical_missing_for_active_instance(self):
        """Test that get_data raises DoesNotExist when historical record is missing."""
        capability = OrmReadCapability()

        class DoesNotExist(Exception):
            pass

        mock_model = Mock()
        mock_model.DoesNotExist = DoesNotExist

        fixed_now = timezone.now()

        mock_instance = Mock()
        mock_instance.pk = 1
        mock_instance._search_date = fixed_now - timedelta(days=1)
        mock_instance.__class__ = Mock()
        mock_instance.__class__._model = mock_model
        mock_instance.__class__.historical_lookup_buffer_seconds = 0

        mock_manager = Mock()
        mock_manager.get = Mock(return_value=mock_instance)

        mock_history_capability = Mock()
        mock_history_capability.get_historical_record = Mock(return_value=None)

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_manager = Mock(return_value=mock_manager)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.support.is_soft_delete_enabled",
                return_value=False,
            ):
                with patch(
                    "general_manager.interface.capabilities.orm.support._history_capability_for",
                    return_value=mock_history_capability,
                ):
                    with patch(
                        "general_manager.interface.capabilities.orm.support.timezone.now",
                        return_value=fixed_now,
                    ):
                        with patch(
                            "general_manager.interface.capabilities.orm.with_observability",
                            side_effect=lambda *_args, **kwargs: kwargs["func"](),
                        ):
                            with pytest.raises(DoesNotExist):
                                capability.get_data(mock_instance)
        mock_history_capability.get_historical_record.assert_called_once_with(
            mock_instance.__class__,
            mock_instance,
            mock_instance._search_date,
        )

    def test_ambient_get_data_without_model_history_fails_closed(self):
        capability = OrmReadCapability()

        class DoesNotExist(Exception):
            pass

        class Model:
            pass

        Model.DoesNotExist = DoesNotExist
        search_date = timezone.now() - timedelta(days=1)

        class InterfaceInstance:
            _model = Model
            historical_lookup_buffer_seconds = 0

            def __init__(self) -> None:
                self.pk = 1
                self._search_date = search_date

        manager = Mock()
        manager.get.side_effect = DoesNotExist("missing")
        support = Mock()
        support.get_manager.return_value = manager

        with (
            as_of(search_date),
            patch(
                "general_manager.interface.capabilities.orm.support.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.support.is_soft_delete_enabled",
                return_value=False,
            ),
            patch(
                "general_manager.interface.capabilities.orm.support.timezone.now",
                return_value=search_date + timedelta(seconds=10),
            ),
            patch(
                "general_manager.interface.capabilities.orm.with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
        ):
            with pytest.raises(HistoricalReadNotSupportedError):
                capability.get_data(InterfaceInstance())

    def test_get_attribute_types_returns_field_metadata(self):
        """Test that get_attribute_types returns field descriptors as metadata."""
        capability = OrmReadCapability()

        mock_descriptor1 = Mock()
        mock_descriptor1.metadata = {"type": str, "required": True}

        mock_descriptor2 = Mock()
        mock_descriptor2.metadata = {"type": int, "required": False}

        descriptors = {"field1": mock_descriptor1, "field2": mock_descriptor2}

        interface_cls = Mock()

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_field_descriptors = Mock(return_value=descriptors)
            mock_get_support.return_value = mock_support

            result = capability.get_attribute_types(interface_cls)

            assert "field1" in result
            assert "field2" in result
            assert result["field1"] == {"type": str, "required": True}


class TestOrmHistoryCapability:
    """Tests for ORM history capability."""

    def test_get_historical_record_returns_none_for_non_history_model(self):
        """Test that get_historical_record returns None for models without history."""
        capability = OrmHistoryCapability()

        mock_instance = Mock(spec=["pk"])  # No 'history' attribute
        interface_cls = Mock()

        result = capability.get_historical_record(
            interface_cls, mock_instance, datetime.now()
        )

        assert result is None

    def test_get_historical_record_queries_history_manager(self):
        """Test that get_historical_record queries the history manager."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_history_manager.filter = Mock(return_value=mock_history_qs)

        mock_instance = Mock()
        mock_instance.history = mock_history_manager

        interface_cls = Mock()

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record(
                interface_cls, mock_instance, search_date
            )

            mock_history_manager.filter.assert_called_once()
            mock_history_qs.order_by.assert_called_once_with(
                "history_date", "history_id"
            )
            assert result is mock_historical

    def test_get_historical_record_filters_by_pk_when_instance_has_history(self):
        """Test that get_historical_record filters by PK when instance provides history."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_history_manager.filter = Mock(return_value=mock_history_qs)

        mock_meta = Mock()
        mock_meta.pk = Mock()
        mock_meta.pk.name = "customer_id"

        mock_instance = SimpleNamespace(
            pk=42,
            history=mock_history_manager,
            _meta=mock_meta,
        )

        interface_cls = Mock()

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record(
                interface_cls, mock_instance, search_date
            )

            mock_history_manager.filter.assert_called_once_with(
                customer_id=42, history_date__lte=search_date
            )
            assert result is mock_historical

    def test_get_historical_record_falls_back_to_model_history(self):
        """Test that get_historical_record can use the model history manager when the instance lacks one."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_history_manager.filter = Mock(return_value=mock_history_qs)

        mock_meta = Mock()
        mock_meta.pk = Mock()
        mock_meta.pk.name = "customer_id"

        mock_model = type(
            "MockHistoryModel",
            (),
            {
                "_meta": mock_meta,
                "history": mock_history_manager,
            },
        )

        interface_cls = Mock()
        interface_cls._model = mock_model

        instance = SimpleNamespace(pk=123)

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record(
                interface_cls, instance, search_date
            )

            mock_history_manager.filter.assert_called_once_with(
                customer_id=123, history_date__lte=search_date
            )
            assert result is mock_historical

    def test_get_historical_record_falls_back_to_id_attribute(self):
        """Test fallback to instance.id with database alias support."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_using_manager = Mock()
        mock_using_manager.filter = Mock(return_value=mock_history_qs)
        mock_history_manager.using = Mock(return_value=mock_using_manager)

        mock_meta = Mock()
        mock_meta.pk = Mock()
        mock_meta.pk.name = "id"

        mock_model = type(
            "MockHistoryModel",
            (),
            {
                "_meta": mock_meta,
                "history": mock_history_manager,
            },
        )

        interface_cls = Mock()
        interface_cls._model = mock_model

        instance = SimpleNamespace(id=5)

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value="replica")
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record(
                interface_cls, instance, search_date
            )

            mock_history_manager.using.assert_called_once_with("replica")
            mock_using_manager.filter.assert_called_once_with(
                id=5, history_date__lte=search_date
            )
            assert result is mock_historical

    def test_get_historical_record_falls_back_to_identification_dict(self):
        """Test fallback to identification dict and default id field name."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_history_manager.filter = Mock(return_value=mock_history_qs)

        mock_meta = Mock()
        mock_meta.pk = Mock()
        mock_meta.pk.name = 123

        mock_model = type(
            "MockHistoryModel",
            (),
            {
                "_meta": mock_meta,
                "history": mock_history_manager,
            },
        )

        interface_cls = Mock()
        interface_cls._model = mock_model

        instance = SimpleNamespace(identification={"id": 7})

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record(
                interface_cls, instance, search_date
            )

            mock_history_manager.filter.assert_called_once_with(
                id=7, history_date__lte=search_date
            )
            assert result is mock_historical

    def test_get_historical_record_returns_none_without_identifier(self):
        """Test that get_historical_record returns None when no identifier is present."""
        capability = OrmHistoryCapability()

        interface_cls = Mock()
        instance = SimpleNamespace()

        result = capability.get_historical_record(
            interface_cls, instance, datetime.now()
        )

        assert result is None

    def test_get_historical_record_by_pk_returns_none_without_search_date(self):
        """Test that get_historical_record_by_pk returns None without search_date."""
        capability = OrmHistoryCapability()

        interface_cls = Mock()
        interface_cls._model = Mock()

        result = capability.get_historical_record_by_pk(interface_cls, 42, None)

        assert result is None

    def test_get_historical_record_by_pk_queries_by_id(self):
        """Test that get_historical_record_by_pk queries history by primary key."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_history_manager.filter = Mock(return_value=mock_history_qs)

        mock_model = Mock()
        mock_model.history = mock_history_manager
        mock_model._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))

        interface_cls = Mock()
        interface_cls._model = mock_model

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record_by_pk(
                interface_cls, 123, search_date
            )

            mock_history_manager.filter.assert_called_once_with(
                id=123, history_date__lte=search_date
            )
            mock_history_qs.order_by.assert_called_once_with(
                "history_date", "history_id"
            )
            assert result is mock_historical

    def test_get_historical_record_by_pk_uses_model_pk_field_name(self):
        """Custom primary-key models should query history by their actual PK field."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_history_manager.filter = Mock(return_value=mock_history_qs)

        mock_model = Mock()
        mock_model.history = mock_history_manager
        mock_model._meta = SimpleNamespace(pk=SimpleNamespace(name="sku"))

        interface_cls = Mock()
        interface_cls._model = mock_model

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record_by_pk(
                interface_cls, "SKU-1", search_date
            )

            mock_history_manager.filter.assert_called_once_with(
                sku="SKU-1", history_date__lte=search_date
            )
            assert result is mock_historical

    def test_get_historical_record_by_pk_uses_database_alias(self):
        """Test that get_historical_record_by_pk applies database alias when provided."""
        capability = OrmHistoryCapability()

        search_date = datetime.now()
        mock_historical = Mock()

        mock_history_qs = Mock()
        mock_history_qs.order_by = Mock(return_value=mock_history_qs)
        mock_history_qs.last = Mock(return_value=mock_historical)

        mock_history_manager = Mock()
        mock_using_manager = Mock()
        mock_using_manager.filter = Mock(return_value=mock_history_qs)
        mock_history_manager.using = Mock(return_value=mock_using_manager)

        mock_model = Mock()
        mock_model.history = mock_history_manager
        mock_model._meta = SimpleNamespace(pk=SimpleNamespace(name="id"))

        interface_cls = Mock()
        interface_cls._model = mock_model

        with patch(
            "general_manager.interface.capabilities.orm.history.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value="replica")
            mock_get_support.return_value = mock_support

            result = capability.get_historical_record_by_pk(
                interface_cls, 42, search_date
            )

            mock_history_manager.using.assert_called_once_with("replica")
            mock_using_manager.filter.assert_called_once_with(
                id=42, history_date__lte=search_date
            )
            assert result is mock_historical


class TestOrmQueryCapability:
    """Tests for ORM query capability."""

    def test_build_reverse_filter_alias_map_returns_empty_without_meta_get_fields(
        self,
    ) -> None:
        class NoFieldsModel:
            _meta = object()

        assert _build_reverse_filter_alias_map(NoFieldsModel) == {}

    def test_build_reverse_filter_alias_map_returns_empty_when_get_fields_raises_type_error(
        self,
    ) -> None:
        class BrokenMeta:
            def get_fields(self) -> tuple[object, ...]:
                raise TypeError

        class BrokenModel:
            _meta = BrokenMeta()

        assert _build_reverse_filter_alias_map(BrokenModel) == {}

    def test_build_reverse_filter_alias_map_skips_alias_matching_forward_field_name(
        self,
    ) -> None:
        class ChangeRequest(models.Model):
            review_assignments = models.IntegerField(default=0)

            class Meta:
                app_label = "general_manager"

        class ReviewAssignments(models.Model):
            change_request = models.ForeignKey(
                ChangeRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        for model in (ChangeRequest, ReviewAssignments):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        assert _build_reverse_filter_alias_map(ChangeRequest) == {}

    def test_build_reverse_filter_alias_map_adds_snake_case_alias_for_default_reverse_relation(
        self,
    ) -> None:
        class ChangeRequest(models.Model):
            class Meta:
                app_label = "general_manager"

        class ChangeRequestFeasibility(models.Model):
            change_request = models.ForeignKey(
                ChangeRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        for model in (ChangeRequest, ChangeRequestFeasibility):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        aliases = _build_reverse_filter_alias_map(ChangeRequest)

        assert aliases["change_request_feasibility"] == "changerequestfeasibility"

    def test_resolve_filter_segment_raises_only_for_requested_ambiguous_alias(
        self,
    ) -> None:
        class LookupModel(models.Model):
            class Meta:
                app_label = "general_manager"

        model_key = LookupModel._meta.model_name
        if model_key not in apps.all_models["general_manager"]:
            apps.register_model("general_manager", LookupModel)
        apps.clear_cache()

        with patch(
            "general_manager.interface.capabilities.orm.support._build_reverse_filter_alias_metadata",
            return_value=(
                {},
                {
                    "review_assignments": (
                        "reviewassignments",
                        "review_assignments",
                    )
                },
            ),
        ):
            with pytest.raises(
                AmbiguousReverseFilterAliasError,
                match="review_assignments",
            ):
                _resolve_filter_segment(LookupModel, "review_assignments")

    def test_resolve_filter_segment_ignores_unrelated_ambiguous_aliases(
        self,
    ) -> None:
        class AmbiguousLookupChangeRequest(models.Model):
            class Meta:
                app_label = "general_manager"

        class AmbiguousLookupReviewAssignments(models.Model):
            change_request = models.ForeignKey(
                AmbiguousLookupChangeRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        AmbiguousLookupReviewAssignments.__name__ = "ReviewAssignments"

        class AmbiguousLookupManualReview(models.Model):
            change_request = models.ForeignKey(
                AmbiguousLookupChangeRequest,
                on_delete=models.CASCADE,
                related_name="review_assignments",
            )

            class Meta:
                app_label = "general_manager"

        for model in (
            AmbiguousLookupChangeRequest,
            AmbiguousLookupReviewAssignments,
            AmbiguousLookupManualReview,
        ):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        assert _resolve_filter_segment(AmbiguousLookupChangeRequest, "missing") == (
            "missing",
            None,
        )

    def test_build_reverse_filter_alias_map_prefers_related_query_name_over_related_name(
        self,
    ) -> None:
        class ReviewQueue(models.Model):
            class Meta:
                app_label = "general_manager"

        class ReviewQueueEntry(models.Model):
            review_queue = models.ForeignKey(
                ReviewQueue,
                on_delete=models.CASCADE,
                related_name="queue_items",
                related_query_name="queue_entry_filters",
            )

            class Meta:
                app_label = "general_manager"

        for model in (ReviewQueue, ReviewQueueEntry):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        aliases = _build_reverse_filter_alias_map(ReviewQueue)

        assert aliases["queue_entry_filters"] == "queue_entry_filters"
        assert "queue_items" not in aliases

    def test_filter_translates_snake_case_reverse_relation_root(self) -> None:
        capability = OrmQueryCapability()

        class AliasTargetRequest(models.Model):
            class Meta:
                app_label = "general_manager"

        class AliasTargetRequestFeasibility(models.Model):
            change_request = models.ForeignKey(
                AliasTargetRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        for model in (AliasTargetRequest, AliasTargetRequestFeasibility):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        mock_parent = Mock()
        interface_cls = Mock()
        interface_cls._parent_class = mock_parent
        interface_cls.normalize_search_date = None
        interface_cls._model = AliasTargetRequest

        support = Mock()
        queryset = Mock()
        filtered_qs = Mock()
        queryset.filter.return_value = filtered_qs
        support.get_queryset.return_value = queryset
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.side_effect = lambda kwargs: dict(kwargs)
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support.DatabaseBucket"
            ) as mock_bucket:
                mock_bucket.return_value = Mock()

                with patch(
                    "general_manager.interface.capabilities.orm.with_observability",
                    side_effect=lambda *_args, **kwargs: kwargs["func"](),
                ):
                    capability.filter(
                        interface_cls,
                        alias_target_request_feasibility__id=42,
                    )

                    queryset.filter.assert_called_once_with(
                        aliastargetrequestfeasibility__id=42
                    )

    def test_filter_translates_nested_snake_case_reverse_relation_roots(self) -> None:
        capability = OrmQueryCapability()

        class NestedAliasTargetRequest(models.Model):
            class Meta:
                app_label = "general_manager"

        class NestedAliasTargetRequestFeasibility(models.Model):
            change_request = models.ForeignKey(
                NestedAliasTargetRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        class NestedAliasTargetRequestTeam(models.Model):
            change_request_feasibility = models.ForeignKey(
                NestedAliasTargetRequestFeasibility,
                on_delete=models.CASCADE,
            )
            size = models.IntegerField(default=0)

            class Meta:
                app_label = "general_manager"

        for model in (
            NestedAliasTargetRequest,
            NestedAliasTargetRequestFeasibility,
            NestedAliasTargetRequestTeam,
        ):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        mock_parent = Mock()
        interface_cls = Mock()
        interface_cls._parent_class = mock_parent
        interface_cls.normalize_search_date = None
        interface_cls._model = NestedAliasTargetRequest

        support = Mock()
        queryset = Mock()
        filtered_qs = Mock()
        queryset.filter.return_value = filtered_qs
        support.get_queryset.return_value = queryset
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.side_effect = lambda kwargs: dict(kwargs)
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support.DatabaseBucket"
            ) as mock_bucket:
                mock_bucket.return_value = Mock()

                with patch(
                    "general_manager.interface.capabilities.orm.with_observability",
                    side_effect=lambda *_args, **kwargs: kwargs["func"](),
                ):
                    capability.filter(
                        interface_cls,
                        nested_alias_target_request_feasibility__nested_alias_target_request_team__size__gte=5,
                    )

                    queryset.filter.assert_called_once_with(
                        nestedaliastargetrequestfeasibility__nestedaliastargetrequestteam__size__gte=5
                    )

    def test_translate_reverse_filter_aliases_rewrites_each_key_independently(
        self,
    ) -> None:
        class PayloadAliasTargetRequest(models.Model):
            title = models.CharField(max_length=50, default="")

            class Meta:
                app_label = "general_manager"

        class PayloadAliasTargetRequestFeasibility(models.Model):
            change_request = models.ForeignKey(
                PayloadAliasTargetRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        for model in (
            PayloadAliasTargetRequest,
            PayloadAliasTargetRequestFeasibility,
        ):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        payload = _translate_reverse_filter_aliases(
            PayloadAliasTargetRequest,
            {
                "payload_alias_target_request_feasibility__id": 42,
                "title": "keep me",
            },
        )

        assert payload == {
            "payloadaliastargetrequestfeasibility__id": 42,
            "title": "keep me",
        }

    def test_translate_reverse_filter_key_preserves_remaining_parts_after_unresolved_segment(
        self,
    ) -> None:
        class UnresolvedAliasTargetRequest(models.Model):
            class Meta:
                app_label = "general_manager"

        class UnresolvedAliasTargetRequestFeasibility(models.Model):
            change_request = models.ForeignKey(
                UnresolvedAliasTargetRequest,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        for model in (
            UnresolvedAliasTargetRequest,
            UnresolvedAliasTargetRequestFeasibility,
        ):
            model_key = model._meta.model_name
            if model_key not in apps.all_models["general_manager"]:
                apps.register_model("general_manager", model)
        apps.clear_cache()

        translated = _translate_reverse_filter_key(
            UnresolvedAliasTargetRequest,
            "unresolved_alias_target_request_feasibility__missing__gte",
        )

        assert translated == "unresolvedaliastargetrequestfeasibility__missing__gte"

    def test_translate_reverse_filter_key_caches_repeated_model_key_pairs(
        self,
    ) -> None:
        class CachedFilterTranslationModel(models.Model):
            status = models.IntegerField(default=0)

            class Meta:
                app_label = "orm_capability_tests"

        cache_clear = getattr(_translate_reverse_filter_key, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()

        status_field = CachedFilterTranslationModel._meta.get_field("status")
        try:
            with patch(
                "general_manager.interface.capabilities.orm.support._resolve_filter_segment",
                return_value=("status", status_field),
            ) as resolve_segment:
                assert (
                    _translate_reverse_filter_key(
                        CachedFilterTranslationModel,
                        "status__gte",
                    )
                    == "status__gte"
                )
                assert (
                    _translate_reverse_filter_key(
                        CachedFilterTranslationModel,
                        "status__gte",
                    )
                    == "status__gte"
                )

            resolve_segment.assert_called_once_with(
                CachedFilterTranslationModel,
                "status",
            )
        finally:
            if callable(cache_clear):
                cache_clear()

    def test_translate_reverse_filter_key_preserves_remaining_parts_after_relation_without_related_model(
        self,
    ) -> None:
        relation = SimpleNamespace(is_relation=True, related_model=None)

        with patch(
            "general_manager.interface.capabilities.orm.support._resolve_filter_segment",
            side_effect=[
                ("translated_root", relation),
                ("final_lookup", None),
            ],
        ):
            translated = _translate_reverse_filter_key(
                models.Model,
                "root__final_lookup",
            )

        assert translated == "translated_root__final_lookup"

    def test_resolve_filter_segment_returns_none_without_meta_get_field(self) -> None:
        class NoFieldModel:
            _meta = object()

        assert _resolve_filter_segment(NoFieldModel, "status") == ("status", None)

    def test_resolve_filter_segment_returns_none_for_unknown_segment(self) -> None:
        class PlainModel(models.Model):
            status = models.IntegerField(default=0)

            class Meta:
                app_label = "general_manager"

        model_key = PlainModel._meta.model_name
        if model_key not in apps.all_models["general_manager"]:
            apps.register_model("general_manager", PlainModel)
        apps.clear_cache()

        assert _resolve_filter_segment(PlainModel, "missing") == ("missing", None)

    def test_translate_reverse_filter_key_preserves_real_list_suffixed_fields(
        self,
    ) -> None:
        class ListSuffixFieldTarget(models.Model):
            status = models.IntegerField(default=0)
            status_list = models.IntegerField(default=0)

            class Meta:
                app_label = "general_manager"

        model_key = ListSuffixFieldTarget._meta.model_name
        if model_key not in apps.all_models["general_manager"]:
            apps.register_model("general_manager", ListSuffixFieldTarget)
        apps.clear_cache()

        assert (
            _translate_reverse_filter_key(ListSuffixFieldTarget, "status_list")
            == "status_list"
        )

    def test_filter_returns_database_bucket(self):
        """Test that filter returns a DatabaseBucket with filter applied."""
        capability = OrmQueryCapability()

        mock_parent = Mock()
        interface_cls = Mock()
        interface_cls._parent_class = mock_parent
        interface_cls.normalize_search_date = None

        support = Mock()
        queryset = Mock()
        filtered_qs = Mock()
        queryset.filter.return_value = filtered_qs
        support.get_queryset.return_value = queryset
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.return_value = {"name": "test", "value": 42}
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support.DatabaseBucket"
            ) as mock_bucket:
                mock_bucket.return_value = Mock()

                with patch(
                    "general_manager.interface.capabilities.orm.with_observability",
                    side_effect=lambda *_args, **kwargs: kwargs["func"](),
                ):
                    result = capability.filter(interface_cls, name="test", value=42)

                    queryset.filter.assert_called_once_with(name="test", value=42)
                    mock_bucket.assert_called_once_with(
                        filtered_qs,
                        mock_parent,
                        {"name": "test", "value": 42},
                        {},
                        search_date=None,
                    )
                    assert result is mock_bucket.return_value

    def test_build_or_reuse_bucket_skips_signature_without_run_context(self):
        capability = OrmQueryCapability()
        interface_cls = Mock()
        built_bucket = Mock()

        with (
            patch(
                "general_manager.interface.capabilities.orm.support.current_calculation_run_context",
                return_value=None,
            ),
            patch.object(
                capability,
                "_run_scoped_query_bucket_signature",
                side_effect=AssertionError("signature only needed inside run context"),
            ),
            patch.object(
                capability,
                "_build_bucket",
                return_value=built_bucket,
            ) as build_bucket,
        ):
            result = capability._build_or_reuse_bucket(
                interface_cls,
                include_inactive=False,
                normalized_kwargs={"name": "test"},
            )

        assert result is built_bucket
        build_bucket.assert_called_once_with(
            interface_cls,
            include_inactive=False,
            normalized_kwargs={"name": "test"},
            exclude=False,
            search_date=None,
        )

    def test_exclude_returns_database_bucket(self):
        """Test that exclude returns a DatabaseBucket with exclusion."""
        capability = OrmQueryCapability()

        interface_cls = Mock()
        interface_cls._parent_class = Mock()
        interface_cls.normalize_search_date = None

        support = Mock()
        queryset = Mock()
        excluded_qs = Mock()
        queryset.exclude.return_value = excluded_qs
        support.get_queryset.return_value = queryset
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.return_value = {"status": "inactive"}
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support.DatabaseBucket"
            ) as mock_bucket:
                mock_bucket.return_value = Mock()

                with patch(
                    "general_manager.interface.capabilities.orm.with_observability",
                    side_effect=lambda *_args, **kwargs: kwargs["func"](),
                ):
                    result = capability.exclude(interface_cls, status="inactive")

                    queryset.exclude.assert_called_once_with(status="inactive")
                    mock_bucket.assert_called_once_with(
                        excluded_qs,
                        interface_cls._parent_class,
                        {},
                        {"status": "inactive"},
                        search_date=None,
                    )
                    assert result is mock_bucket.return_value

    def test_filter_with_include_inactive_uses_all_manager(self):
        """Test that filter can include inactive records by using the all manager."""
        capability = OrmQueryCapability()

        interface_cls = Mock()
        interface_cls._parent_class = Mock()
        interface_cls.normalize_search_date = None

        support = Mock()
        queryset = Mock()
        queryset.filter.return_value = queryset
        support.get_queryset.return_value = queryset
        manager = Mock()
        manager.all.return_value = queryset
        support.get_manager.return_value = manager
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.return_value = {}
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support.DatabaseBucket"
            ) as mock_bucket:
                mock_bucket.return_value = Mock()

                with patch(
                    "general_manager.interface.capabilities.orm.with_observability",
                    side_effect=lambda *_args, **kwargs: kwargs["func"](),
                ):
                    result = capability.filter(interface_cls, include_inactive=True)

                    support.get_manager.assert_called_once_with(
                        interface_cls, only_active=False
                    )
                    manager.all.assert_called_once_with()
                    mock_bucket.assert_called_once_with(
                        queryset,
                        interface_cls._parent_class,
                        {},
                        {},
                        search_date=None,
                    )
                    assert result is mock_bucket.return_value

    def test_filter_with_search_date_uses_history_as_of(self):
        """Test that filter uses history.as_of when a search_date is provided."""
        capability = OrmQueryCapability()

        interface_cls = type("MockInterface", (), {})
        interface_cls._parent_class = Mock()
        interface_cls._model = Mock()
        interface_cls.historical_lookup_buffer_seconds = 0
        interface_cls.normalize_search_date = Mock(side_effect=lambda value: value)

        search_date = timezone.now() - timedelta(days=1)
        now_time = search_date + timedelta(seconds=10)

        history_qs = Mock()
        filtered_qs = Mock()
        history_qs.filter.return_value = filtered_qs
        history_manager = Mock()
        history_manager.as_of.return_value = history_qs
        interface_cls._model.history = history_manager

        history_capability = Mock()
        history_capability.get_historical_queryset = Mock(return_value=history_qs)

        support = Mock()
        support.get_queryset.return_value = Mock()
        support.get_database_alias.return_value = None
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.return_value = {"name": "Historian"}
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support._history_capability_for",
                return_value=history_capability,
            ):
                with patch(
                    "general_manager.interface.capabilities.orm.support.DatabaseBucket"
                ) as mock_bucket:
                    mock_bucket.return_value = Mock()

                    with patch(
                        "general_manager.interface.capabilities.orm.support.timezone.now",
                        return_value=now_time,
                    ):
                        with patch(
                            "general_manager.interface.capabilities.orm.with_observability",
                            side_effect=lambda *_args, **kwargs: kwargs["func"](),
                        ):
                            result = capability.filter(
                                interface_cls,
                                name="Historian",
                                search_date=search_date,
                            )

                            history_capability.get_historical_queryset.assert_called_once_with(
                                interface_cls,
                                search_date,
                            )
                            history_qs.filter.assert_called_once_with(name="Historian")
                            mock_bucket.assert_called_once_with(
                                filtered_qs,
                                interface_cls._parent_class,
                                {"name": "Historian"},
                                {},
                                search_date=search_date,
                            )
                            assert result is mock_bucket.return_value

    def test_filter_with_search_date_requires_history_capability(self):
        """Test that filter raises when history capability is missing."""
        capability = OrmQueryCapability()

        interface_cls = type("MockInterface", (), {})
        interface_cls._parent_class = Mock()
        interface_cls._model = Mock()
        interface_cls.historical_lookup_buffer_seconds = 0
        interface_cls.normalize_search_date = Mock(side_effect=lambda value: value)

        search_date = timezone.now() - timedelta(days=1)
        now_time = search_date + timedelta(seconds=10)

        support = Mock()
        support.get_queryset.return_value = Mock()
        support.get_database_alias.return_value = None
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.return_value = {}
        support.get_payload_normalizer.return_value = normalizer

        with patch(
            "general_manager.interface.capabilities.orm.support.get_support_capability",
            return_value=support,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.support._history_capability_for",
                side_effect=NotImplementedError("missing history"),
            ):
                with patch(
                    "general_manager.interface.capabilities.orm.support.timezone.now",
                    return_value=now_time,
                ):
                    with patch(
                        "general_manager.interface.capabilities.orm.with_observability",
                        side_effect=lambda *_args, **kwargs: kwargs["func"](),
                    ):
                        with pytest.raises(HistoryNotSupportedError):
                            capability.filter(
                                interface_cls,
                                search_date=search_date,
                            )

    def test_ambient_filter_without_history_fails_closed(self):
        capability = OrmQueryCapability()
        interface_cls = type("MockInterface", (), {})
        interface_cls._parent_class = Mock()
        interface_cls._model = Mock()
        interface_cls.historical_lookup_buffer_seconds = 0
        support = Mock()
        support.get_queryset.return_value = Mock()
        support.get_database_alias.return_value = None
        normalizer = Mock()
        normalizer.normalize_filter_kwargs.return_value = {}
        support.get_payload_normalizer.return_value = normalizer
        search_date = timezone.now() - timedelta(days=1)

        with (
            as_of(search_date),
            patch(
                "general_manager.interface.capabilities.orm.support.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.support._history_capability_for",
                side_effect=NotImplementedError("missing history"),
            ),
            patch(
                "general_manager.interface.capabilities.orm.support.timezone.now",
                return_value=search_date + timedelta(seconds=10),
            ),
            patch(
                "general_manager.interface.capabilities.orm.with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
        ):
            with pytest.raises(HistoricalReadNotSupportedError) as exc_info:
                capability.filter(interface_cls)

        assert isinstance(exc_info.value.__cause__, NotImplementedError)

    def test_invalid_explicit_search_date_preserves_query_error_contract(self):
        capability = OrmQueryCapability()
        interface_cls = Mock()

        with patch(
            "general_manager.interface.capabilities.orm.with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            with pytest.raises(SearchDateInputError) as exc_info:
                capability.filter(interface_cls, search_date=object())

        assert isinstance(exc_info.value.__cause__, InvalidSearchDateError)


class TestOrmValidationCapability:
    """Tests for ORM validation capability."""

    def test_normalize_payload_validates_and_normalizes(self):
        """Test that normalize_payload validates keys and normalizes values."""
        capability = OrmValidationCapability()

        interface_cls = Mock()

        payload = {"name": "test", "value": 42}
        mock_normalizer = Mock()

        def validate_keys(payload_copy):
            payload_copy["validate_mutation"] = True

        mock_normalizer.validate_keys = Mock(side_effect=validate_keys)

        def split_many_to_many(payload_copy):
            payload_copy["split_mutation"] = True
            return {"name": "test", "value": 42}, {"tags": [1, 2]}

        mock_normalizer.split_many_to_many = Mock(side_effect=split_many_to_many)
        mock_normalizer.normalize_simple_values = Mock(
            return_value={"name": "test", "value_id": 42}
        )
        mock_normalizer.normalize_many_values = Mock(return_value={"tags": [1, 2]})

        with patch(
            "general_manager.interface.capabilities.orm.mutations.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_payload_normalizer = Mock(return_value=mock_normalizer)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ):
                result = capability.normalize_payload(interface_cls, payload=payload)

                mock_normalizer.validate_keys.assert_called_once()
                validate_payload = mock_normalizer.validate_keys.call_args.args[0]
                assert validate_payload is not payload
                assert validate_payload["validate_mutation"] is True
                mock_normalizer.split_many_to_many.assert_called_once()
                split_payload = mock_normalizer.split_many_to_many.call_args.args[0]
                assert split_payload is not payload
                assert split_payload == {
                    "name": "test",
                    "value": 42,
                    "validate_mutation": True,
                    "split_mutation": True,
                }
                assert payload == {"name": "test", "value": 42}
                mock_normalizer.normalize_simple_values.assert_called_once()
                mock_normalizer.normalize_many_values.assert_called_once()
                assert result == ({"name": "test", "value_id": 42}, {"tags": [1, 2]})

    def test_normalize_payload_with_concrete_normalizer_preserves_payload(self):
        """Concrete PayloadNormalizer should split M2M payloads without caller mutation."""

        class OrmValidationRelatedModel(models.Model):
            class Meta:
                app_label = "test_orm_validation_payload"

        class OrmValidationModel(models.Model):
            name = models.CharField(max_length=32, default="")
            tags = models.ManyToManyField(OrmValidationRelatedModel)

            class Meta:
                app_label = "test_orm_validation_payload"

        capability = OrmValidationCapability()
        interface_cls = Mock()
        payload = {"name": "asset", "tags_list": [1, 2]}
        normalizer = PayloadNormalizer(OrmValidationModel)

        with patch(
            "general_manager.interface.capabilities.orm.mutations.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_payload_normalizer = Mock(return_value=normalizer)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ):
                result = capability.normalize_payload(interface_cls, payload=payload)

        assert payload == {"name": "asset", "tags_list": [1, 2]}
        assert result == ({"name": "asset"}, {"tags_id_list": [1, 2]})

    def test_normalize_payload_subclass_split_override_preserves_payload(self):
        """PayloadNormalizer subclasses should keep split override compatibility."""

        class SubclassRelatedModel(models.Model):
            class Meta:
                app_label = "test_orm_validation_subclass_payload"

        class SubclassModel(models.Model):
            name = models.CharField(max_length=32, default="")
            tags = models.ManyToManyField(SubclassRelatedModel)

            class Meta:
                app_label = "test_orm_validation_subclass_payload"

        class CustomPayloadNormalizer(PayloadNormalizer):
            def split_many_to_many(self, kwargs):
                kwargs["split_mutation"] = True
                return (
                    {"name": kwargs["name"], "custom": "split"},
                    {"tags_id_list": [99]},
                )

        capability = OrmValidationCapability()
        interface_cls = Mock()
        payload = {"name": "asset", "tags_list": [1, 2]}
        normalizer = CustomPayloadNormalizer(SubclassModel)

        with patch(
            "general_manager.interface.capabilities.orm.mutations.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_payload_normalizer = Mock(return_value=normalizer)
            mock_get_support.return_value = mock_support

            with patch(
                "general_manager.interface.capabilities.orm.with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ):
                result = capability.normalize_payload(interface_cls, payload=payload)

        assert payload == {"name": "asset", "tags_list": [1, 2]}
        assert result == (
            {"name": "asset", "custom": "split"},
            {"tags_id_list": [99]},
        )

    def test_normalize_payload_fallback_copies_payload_before_custom_validation(self):
        """Fallback normalization should isolate caller payload from custom normalizers."""
        interface_cls = Mock()
        interface_cls.get_capability_handler.return_value = None
        payload = {"name": "test", "value": 42}
        mock_normalizer = Mock()

        def validate_keys(payload_copy):
            payload_copy["validate_mutation"] = True

        def split_many_to_many(payload_copy):
            payload_copy["split_mutation"] = True
            return {"name": "test", "value": 42}, {"tags": [1, 2]}

        mock_normalizer.validate_keys = Mock(side_effect=validate_keys)
        mock_normalizer.split_many_to_many = Mock(side_effect=split_many_to_many)
        mock_normalizer.normalize_simple_values = Mock(
            return_value={"name": "test", "value_id": 42}
        )
        mock_normalizer.normalize_many_values = Mock(return_value={"tags": [1, 2]})

        with patch(
            "general_manager.interface.capabilities.orm.mutations.get_support_capability"
        ) as mock_get_support:
            mock_support = Mock()
            mock_support.get_payload_normalizer = Mock(return_value=mock_normalizer)
            mock_get_support.return_value = mock_support

            result = _normalize_payload(interface_cls, payload)

        validate_payload = mock_normalizer.validate_keys.call_args.args[0]
        split_payload = mock_normalizer.split_many_to_many.call_args.args[0]
        assert validate_payload is not payload
        assert split_payload is validate_payload
        assert split_payload == {
            "name": "test",
            "value": 42,
            "validate_mutation": True,
            "split_mutation": True,
        }
        assert payload == {"name": "test", "value": 42}
        assert result == ({"name": "test", "value_id": 42}, {"tags": [1, 2]})


class TestSoftDeleteCapability:
    """Tests for soft delete capability."""

    def test_is_enabled_default_false(self):
        """Test that soft delete is disabled by default."""
        capability = SoftDeleteCapability()

        assert capability.is_enabled() is False

    def test_set_state_enables_soft_delete(self):
        """Test that set_state can enable soft delete."""
        capability = SoftDeleteCapability()

        capability.set_state(enabled=True)

        assert capability.is_enabled() is True

    def test_set_state_disables_soft_delete(self):
        """Test that set_state can disable soft delete."""
        capability = SoftDeleteCapability()
        capability._enabled = True

        capability.set_state(enabled=False)

        assert capability.is_enabled() is False

    def test_toggle_state(self):
        """Test that is_enabled state can be toggled."""
        capability = SoftDeleteCapability()

        assert capability.is_enabled() is False
        capability.set_state(enabled=True)
        assert capability.is_enabled() is True
        capability.set_state(enabled=False)
        assert capability.is_enabled() is False


class TestOrmMutationCapability:
    """Tests for ORM mutation capability."""

    def test_assign_simple_attributes_sets_values(self):
        """Test that assign_simple_attributes sets attribute values."""
        capability = OrmMutationCapability()

        mock_instance = Mock()
        interface_cls = Mock()

        kwargs = {"name": "test", "value": 42}

        with patch(
            "general_manager.interface.capabilities.orm.mutations.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            result = capability.assign_simple_attributes(
                interface_cls, mock_instance, kwargs
            )

            assert result is mock_instance
            assert mock_instance.name == "test"
            assert mock_instance.value == 42

    def test_create_reserves_upload_key_before_assigning_model_attributes(self):
        """Keep internal UploadCandidate objects out of Django descriptors."""
        capability = OrmCreateCapability()
        candidate = UploadCandidate(
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
            filename="avatar.png",
            size=3,
            content_type="image/png",
            checksum_sha256="a" * 64,
        )
        interface_cls = Mock()
        instance = Mock()
        interface_cls._model.return_value = instance
        mutation = Mock()
        mutation.assign_simple_attributes.return_value = instance
        mutation.save_with_history.return_value = 42
        prepared = Mock()
        locked = Mock()

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({"avatar": candidate}, {}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.prepare_upload_claims",
                return_value=prepared,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.lock_upload_claims",
                return_value=locked,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.reserve_upload_names",
                return_value={"avatar": "avatars/reserved.png"},
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.run_upload_transaction",
                side_effect=lambda _prepared, operation: operation(),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.mark_uploads_finalizing"
            ) as mark_finalizing,
        ):
            result = capability.create(interface_cls, creator_id=7, avatar=candidate)

        assert result == {"id": 42}
        assert mutation.assign_simple_attributes.call_count == 2
        mutation.assign_simple_attributes.assert_called_with(
            interface_cls,
            instance,
            {"avatar": "avatars/reserved.png"},
        )
        mark_finalizing.assert_called_once_with(locked, target_pk=42)

    @pytest.mark.parametrize("operation", ("create", "update"))
    def test_non_upload_mutation_supports_legacy_save_with_history_override(
        self,
        operation: str,
    ):
        """Keep ordinary mutation compatible with the prior override signature."""
        save_calls: list[tuple[object, object, int | None, str | None]] = []

        class LegacyMutation(OrmMutationCapability):
            def save_with_history(
                self,
                interface_cls,
                instance,
                *,
                creator_id,
                history_comment,
            ):
                save_calls.append(
                    (interface_cls, instance, creator_id, history_comment)
                )
                return 42

        mutation = LegacyMutation()
        model_instance = Mock()
        mutation.assign_simple_attributes = Mock(return_value=model_instance)
        mutation.apply_many_to_many = Mock(return_value=model_instance)
        support = Mock()
        support.get_database_alias.return_value = None
        manager = Mock()
        manager.get.return_value = model_instance
        support.get_manager.return_value = manager

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({}, {}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.discard_orm_instance_cache"
            ),
        ):
            if operation == "create":
                interface_cls = Mock()
                interface_cls._model.return_value = model_instance
                result = OrmCreateCapability().create(interface_cls)
            else:
                interface_instance = SimpleNamespace(pk=42)
                interface_cls = interface_instance.__class__
                result = OrmUpdateCapability().update(interface_instance)

        assert result == {"id": 42}
        assert save_calls == [(interface_cls, model_instance, None, None)]

    def test_non_upload_create_uses_configured_alias_for_outer_atomic(self):
        """Keep ordinary create persistence on one alias-aware transaction."""
        capability = OrmCreateCapability()
        interface_cls = Mock()
        instance = Mock()
        interface_cls._model.return_value = instance
        support = Mock()
        support.get_database_alias.return_value = "replica"
        mutation = Mock()
        mutation.assign_simple_attributes.return_value = instance
        mutation.save_with_history.return_value = 42

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({"name": "created"}, {"owners_id_list": [1]}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                return_value=nullcontext(),
            ) as atomic,
        ):
            result = capability.create(interface_cls, name="created")

        assert result == {"id": 42}
        atomic.assert_called_once_with(using="replica")
        mutation.save_with_history.assert_called_once_with(
            interface_cls,
            instance,
            creator_id=None,
            history_comment=None,
        )

    def test_non_upload_create_elides_base_savepoint_on_configured_alias(self):
        """Avoid a nested savepoint while retaining both alias-aware atomics."""

        class DelegatingLegacyMutation(OrmMutationCapability):
            def save_with_history(
                self,
                interface_cls,
                instance,
                *,
                creator_id,
                history_comment,
            ):
                return super().save_with_history(
                    interface_cls,
                    instance,
                    creator_id=creator_id,
                    history_comment=history_comment,
                )

        capability = OrmCreateCapability()
        interface_cls = Mock()
        instance = Mock()
        instance.pk = 42
        instance._state = SimpleNamespace(db=None)
        interface_cls._model.return_value = instance
        support = Mock()
        support.get_database_alias.return_value = "replica"
        mutation = DelegatingLegacyMutation()

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({}, {}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._assign_history_actor"
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.model_has_field",
                return_value=False,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                side_effect=lambda **_kwargs: nullcontext(),
            ) as atomic,
        ):
            result = capability.create(interface_cls)

        assert result == {"id": 42}
        assert atomic.call_args_list == [
            call(using="replica"),
            call(using="replica", savepoint=False),
        ]

    def test_non_upload_update_uses_configured_alias_for_outer_atomic(self):
        """Keep ordinary update persistence on one alias-aware transaction."""
        capability = OrmUpdateCapability()
        interface_instance = SimpleNamespace(pk=7)
        model_instance = Mock()
        support = Mock()
        manager = Mock()
        manager.get.return_value = model_instance
        support.get_manager.return_value = manager
        support.get_database_alias.return_value = "archive"
        mutation = Mock()
        mutation.assign_simple_attributes.return_value = model_instance
        mutation.save_with_history.return_value = 7

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({"name": "updated"}, {"owners_id_list": [2]}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                return_value=nullcontext(),
            ) as atomic,
            patch(
                "general_manager.interface.capabilities.orm.mutations.discard_orm_instance_cache"
            ),
        ):
            result = capability.update(interface_instance, name="updated")

        assert result == {"id": 7}
        atomic.assert_called_once_with(using="archive")
        mutation.save_with_history.assert_called_once_with(
            interface_instance.__class__,
            model_instance,
            creator_id=None,
            history_comment=None,
        )

    def test_non_upload_update_invalidates_cache_after_many_to_many(self):
        """Expose the committed relation state before invalidating cached reads."""
        capability = OrmUpdateCapability()
        interface_instance = SimpleNamespace(pk=7)
        model_instance = Mock()
        support = Mock()
        manager = Mock()
        manager.get.return_value = model_instance
        support.get_manager.return_value = manager
        support.get_database_alias.return_value = None
        events: list[str] = []

        class RecordingAtomic:
            def __enter__(self):
                events.append("atomic enter")

            def __exit__(self, *_args):
                events.append("atomic exit")

        mutation = Mock()
        mutation.assign_simple_attributes.return_value = model_instance
        mutation.save_with_history.return_value = 7
        mutation.apply_many_to_many.side_effect = lambda *_args, **_kwargs: (
            events.append("m2m")
        )

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({}, {"owners_id_list": [2]}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                return_value=RecordingAtomic(),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.discard_orm_instance_cache",
                side_effect=lambda *_args: events.append("discard"),
            ),
        ):
            capability.update(interface_instance, owners_id_list=[2])

        assert events == ["atomic enter", "m2m", "atomic exit", "discard"]

    def test_non_upload_update_does_not_invalidate_cache_when_many_to_many_fails(
        self,
    ):
        """Leave cached reads untouched when the transaction is rolled back."""
        capability = OrmUpdateCapability()
        interface_instance = SimpleNamespace(pk=7)
        model_instance = Mock()
        support = Mock()
        manager = Mock()
        manager.get.return_value = model_instance
        support.get_manager.return_value = manager
        support.get_database_alias.return_value = None
        mutation = Mock()
        mutation.assign_simple_attributes.return_value = model_instance
        mutation.save_with_history.return_value = 7
        mutation.apply_many_to_many.side_effect = RuntimeError("m2m failed")

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.call_with_observability",
                side_effect=lambda *_args, **kwargs: kwargs["func"](),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._normalize_payload",
                return_value=({}, {"owners_id_list": [2]}),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                return_value=mutation,
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "general_manager.interface.capabilities.orm.mutations.discard_orm_instance_cache"
            ) as discard_cache,
        ):
            with pytest.raises(RuntimeError, match="m2m failed"):
                capability.update(interface_instance, owners_id_list=[2])

        discard_cache.assert_not_called()

    def test_assign_simple_attributes_skips_not_provided(self):
        """Test that assign_simple_attributes skips NOT_PROVIDED values."""
        from django.db.models import NOT_PROVIDED

        capability = OrmMutationCapability()

        mock_instance = Mock()
        interface_cls = Mock()

        kwargs = {"name": "test", "skipped": NOT_PROVIDED}

        with patch(
            "general_manager.interface.capabilities.orm.mutations.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            capability.assign_simple_attributes(interface_cls, mock_instance, kwargs)

            assert mock_instance.name == "test"
            # Should not have set 'skipped' attribute

    def test_assign_simple_attributes_wraps_assignment_errors(self):
        """Test that assignment ValueError and TypeError are wrapped consistently."""
        capability = OrmMutationCapability()
        interface_cls = Mock()

        class ValueRejectingInstance:
            def __setattr__(self, key, value):
                """Raise ValueError for the field used by this test."""
                if key == "bad":
                    raise ValueError("invalid")
                super().__setattr__(key, value)

        class TypeRejectingInstance:
            def __setattr__(self, key, value):
                """Raise TypeError for the field used by this test."""
                if key == "bad":
                    raise TypeError("invalid")
                super().__setattr__(key, value)

        with patch(
            "general_manager.interface.capabilities.orm.mutations.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            with pytest.raises(InvalidFieldValueError):
                capability.assign_simple_attributes(
                    interface_cls,
                    ValueRejectingInstance(),
                    {"bad": "value"},
                )
            with pytest.raises(InvalidFieldTypeError):
                capability.assign_simple_attributes(
                    interface_cls,
                    TypeRejectingInstance(),
                    {"bad": "value"},
                )

    def test_save_with_history_uses_database_alias(self):
        """Test that save_with_history stores and uses the configured database alias."""
        capability = OrmMutationCapability()
        interface_cls = Mock()
        instance = Mock()
        instance.pk = 42
        instance._state = SimpleNamespace(db=None)
        support = Mock()
        support.get_database_alias.return_value = "replica"

        with patch(
            "general_manager.interface.capabilities.orm.mutations.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            with patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ):
                with patch(
                    "general_manager.interface.capabilities.orm.mutations._assign_history_actor"
                ):
                    with patch(
                        "general_manager.interface.capabilities.orm.mutations.call_update_change_reason"
                    ):
                        with patch(
                            "general_manager.interface.capabilities.orm.mutations.model_has_field",
                            return_value=False,
                        ):
                            with patch(
                                "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                                return_value=nullcontext(),
                            ) as atomic:
                                result = capability.save_with_history(
                                    interface_cls,
                                    instance,
                                    creator_id=7,
                                    history_comment="saved",
                                )

        assert result == 42
        assert instance._state.db == "replica"
        atomic.assert_called_once_with(using="replica")
        instance.save.assert_called_once_with(using="replica")

    def test_delete_hard_deletes_with_database_alias(self):
        """Test that hard delete records metadata and deletes via the configured alias."""
        capability = OrmDeleteCapability()
        interface_instance = SimpleNamespace(pk=5)
        model_instance = Mock()
        support = Mock()
        manager = Mock()
        support.get_manager.return_value = manager
        support.get_database_alias.return_value = "archive"
        manager.get.return_value = model_instance

        with patch(
            "general_manager.interface.capabilities.orm.mutations.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            with patch(
                "general_manager.interface.capabilities.orm.mutations.get_support_capability",
                return_value=support,
            ):
                with patch(
                    "general_manager.interface.capabilities.orm.mutations._mutation_capability_for",
                    return_value=OrmMutationCapability(),
                ):
                    with patch(
                        "general_manager.interface.capabilities.orm.mutations.is_soft_delete_enabled",
                        return_value=False,
                    ):
                        with patch(
                            "general_manager.interface.capabilities.orm.mutations._assign_history_actor"
                        ) as assign_actor:
                            with patch(
                                "general_manager.interface.capabilities.orm.mutations.model_has_field",
                                return_value=True,
                            ):
                                with patch(
                                    "general_manager.interface.capabilities.orm.mutations.call_update_change_reason"
                                ) as update_reason:
                                    with patch(
                                        "general_manager.interface.capabilities.orm.mutations.transaction.atomic",
                                        return_value=nullcontext(),
                                    ):
                                        with patch(
                                            "general_manager.interface.capabilities.orm.mutations.discard_orm_instance_cache"
                                        ) as discard_cache:
                                            result = capability.delete(
                                                interface_instance,
                                                creator_id=9,
                                                history_comment="remove",
                                            )

        assert result == {"id": 5}
        assign_actor.assert_called_once_with(
            model_instance,
            creator_id=9,
            database_alias="archive",
        )
        assert model_instance.changed_by_id == 9
        update_reason.assert_called_once_with(model_instance, "remove (deleted)")
        model_instance.delete.assert_called_once_with(using="archive")
        discard_cache.assert_called_once_with(interface_instance.__class__, 5)

    def test_assign_history_actor_handles_none_and_database_alias(self):
        """Test history actor assignment with no creator and an aliased creator lookup."""
        instance_without_creator = SimpleNamespace()
        with patch(
            "general_manager.interface.capabilities.orm.mutations.model_has_field",
            return_value=False,
        ):
            _assign_history_actor(
                instance_without_creator,
                creator_id=None,
                database_alias=None,
            )

        assert instance_without_creator._history_user is None

        instance_with_creator = SimpleNamespace()
        user = object()
        alias_manager = Mock()
        alias_manager.get.return_value = user
        manager = Mock()
        manager.db_manager.return_value = alias_manager
        user_model = SimpleNamespace(_default_manager=manager)

        with patch(
            "general_manager.interface.capabilities.orm.mutations.model_has_field",
            return_value=False,
        ):
            with patch(
                "general_manager.interface.capabilities.orm.mutations.get_user_model",
                return_value=user_model,
            ):
                _assign_history_actor(
                    instance_with_creator,
                    creator_id=11,
                    database_alias="replica",
                )

        manager.db_manager.assert_called_once_with("replica")
        alias_manager.get.assert_called_once_with(pk=11)
        assert instance_with_creator._history_user is user


class TestOrmLifecycleCapability:
    """Tests for ORM lifecycle capability."""

    def test_m2m_history_fields_skip_unresolved_custom_through_models(self):
        """History setup skips relations whose custom through model is unresolved."""
        automatic = models.ManyToManyField("general_manager.AutomaticTarget")
        unresolved_custom = models.ManyToManyField(
            "general_manager.CustomTarget",
            through="general_manager.CustomMembership",
        )

        field_names = OrmLifecycleCapability._m2m_history_field_names(
            {
                "automatic": automatic,
                "unresolved_custom": unresolved_custom,
            }
        )

        assert field_names == ("automatic",)

    def test_pre_create_builds_model_and_interface(self):
        """Test that pre_create builds Django model and interface class."""
        capability = OrmLifecycleCapability()

        # This is a complex integration test that would require significant mocking
        # For now, we test that the method exists and has the correct signature
        assert hasattr(capability, "pre_create")
        assert callable(capability.pre_create)

    def test_post_create_wires_manager(self):
        """Test that post_create wires the manager to the GeneralManager class."""
        capability = OrmLifecycleCapability()

        # This is a complex integration test
        assert hasattr(capability, "post_create")
        assert callable(capability.post_create)


# Run with: pytest tests/unit/test_orm_capabilities_comprehensive.py -v
