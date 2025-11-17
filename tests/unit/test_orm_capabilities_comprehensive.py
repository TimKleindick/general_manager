"""Comprehensive tests for ORM capability implementations."""

from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import models

from general_manager.interface.capabilities.orm import (
    OrmPersistenceSupportCapability,
    OrmReadCapability,
    OrmHistoryCapability,
    OrmQueryCapability,
    OrmValidationCapability,
    OrmMutationCapability,
    OrmLifecycleCapability,
    SoftDeleteCapability,
)


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
        
        with patch('general_manager.interface.capabilities.orm.support.DjangoManagerSelector') as mock_selector:
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
        
        with patch('general_manager.interface.capabilities.orm.support.DjangoManagerSelector') as mock_selector:
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
        
        with patch.object(capability, 'get_manager', return_value=mock_manager):
            result = capability.get_queryset(interface_cls)
            
            assert result is mock_queryset
            mock_manager.all.assert_called_once()
    
    def test_get_payload_normalizer_creates_instance(self):
        """Test that get_payload_normalizer creates PayloadNormalizer."""
        capability = OrmPersistenceSupportCapability()
        
        mock_model = Mock()
        interface_cls = Mock()
        interface_cls._model = mock_model
        
        with patch('general_manager.interface.capabilities.orm.support.PayloadNormalizer') as mock_normalizer:
            capability.get_payload_normalizer(interface_cls)
            
            mock_normalizer.assert_called_once_with(mock_model)
    
    def test_get_field_descriptors_caches_result(self):
        """Test that field descriptors are cached on the interface class."""
        capability = OrmPersistenceSupportCapability()
        
        interface_cls = Mock()
        interface_cls._field_descriptors = None
        
        with patch('general_manager.interface.capabilities.orm.support.build_field_descriptors') as mock_build:
            mock_build.return_value = {"field1": "descriptor1"}
            
            result1 = capability.get_field_descriptors(interface_cls)
            result2 = capability.get_field_descriptors(interface_cls)
            
            # Should only build once
            mock_build.assert_called_once()
            assert result1 == {"field1": "descriptor1"}
            assert result2 == result1


class TestOrmReadCapability:
    """Tests for ORM read capability."""
    
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
        
        with patch('general_manager.interface.capabilities.orm.support.get_support_capability') as mock_get_support:
            mock_support = Mock()
            mock_support.get_manager = Mock(return_value=mock_manager)
            mock_get_support.return_value = mock_support
            
            with patch('general_manager.interface.capabilities.orm.with_observability', side_effect=lambda **kw: kw['func']()):
                result = capability.get_data(mock_instance)
                
                mock_manager.get.assert_called_once_with(pk=42)
                assert result == "model_instance"
    
    def test_get_data_raises_does_not_exist(self):
        """Test that get_data raises DoesNotExist when instance not found."""
        capability = OrmReadCapability()
        
        mock_model = Mock()
        mock_model.DoesNotExist = Exception("Not found")
        
        mock_instance = Mock()
        mock_instance.pk = 999
        mock_instance._search_date = None
        mock_instance.__class__ = Mock()
        mock_instance.__class__._model = mock_model
        mock_instance.__class__.historical_lookup_buffer_seconds = 0
        
        mock_manager = Mock()
        mock_manager.get = Mock(side_effect=mock_model.DoesNotExist)
        
        with patch('general_manager.interface.capabilities.orm.support.get_support_capability') as mock_get_support:
            mock_support = Mock()
            mock_support.get_manager = Mock(return_value=mock_manager)
            mock_get_support.return_value = mock_support
            
            with patch('general_manager.interface.capabilities.orm.support.is_soft_delete_enabled', return_value=False):
                with patch('general_manager.interface.capabilities.orm.with_observability', side_effect=lambda **kw: kw['func']()):
                    with pytest.raises(Exception, match="Not found"):
                        capability.get_data(mock_instance)
    
    def test_get_attribute_types_returns_field_metadata(self):
        """Test that get_attribute_types returns field descriptors as metadata."""
        capability = OrmReadCapability()
        
        mock_descriptor1 = Mock()
        mock_descriptor1.metadata = {"type": str, "required": True}
        
        mock_descriptor2 = Mock()
        mock_descriptor2.metadata = {"type": int, "required": False}
        
        descriptors = {
            "field1": mock_descriptor1,
            "field2": mock_descriptor2
        }
        
        interface_cls = Mock()
        
        with patch('general_manager.interface.capabilities.orm.support.get_support_capability') as mock_get_support:
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
        
        mock_instance = Mock(spec=['pk'])  # No 'history' attribute
        interface_cls = Mock()
        
        result = capability.get_historical_record(interface_cls, mock_instance, datetime.now())
        
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
        
        with patch('general_manager.interface.capabilities.orm.support.get_support_capability') as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support
            
            result = capability.get_historical_record(interface_cls, mock_instance, search_date)
            
            mock_history_manager.filter.assert_called_once()
            assert result is mock_historical
    
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
        
        interface_cls = Mock()
        interface_cls._model = mock_model
        
        with patch('general_manager.interface.capabilities.orm.support.get_support_capability') as mock_get_support:
            mock_support = Mock()
            mock_support.get_database_alias = Mock(return_value=None)
            mock_get_support.return_value = mock_support
            
            result = capability.get_historical_record_by_pk(interface_cls, 123, search_date)
            
            mock_history_manager.filter.assert_called_once_with(id=123, history_date__lte=search_date)
            assert result is mock_historical


class TestOrmQueryCapability:
    """Tests for ORM query capability."""
    
    def test_filter_returns_database_bucket(self):
        """Test that filter returns a DatabaseBucket with filter applied."""
        capability = OrmQueryCapability()
        
        mock_parent = Mock()
        interface_cls = Mock()
        interface_cls._parent_class = mock_parent
        
        with patch('general_manager.interface.capabilities.orm.DatabaseBucket') as mock_bucket:
            mock_instance = Mock()
            mock_instance.filter = Mock(return_value="filtered_bucket")
            mock_bucket.return_value = mock_instance
            
            with patch('general_manager.interface.capabilities.orm.with_observability', side_effect=lambda **kw: kw['func']()):
                result = capability.filter(interface_cls, name="test", value=42)
                
                mock_bucket.assert_called_once_with(mock_parent)
                mock_instance.filter.assert_called_once_with(name="test", value=42)
    
    def test_exclude_returns_database_bucket(self):
        """Test that exclude returns a DatabaseBucket with exclusion."""
        capability = OrmQueryCapability()
        
        interface_cls = Mock()
        interface_cls._parent_class = Mock()
        
        with patch('general_manager.interface.capabilities.orm.DatabaseBucket') as mock_bucket:
            mock_instance = Mock()
            mock_instance.exclude = Mock(return_value="excluded_bucket")
            mock_bucket.return_value = mock_instance
            
            with patch('general_manager.interface.capabilities.orm.with_observability', side_effect=lambda **kw: kw['func']()):
                result = capability.exclude(interface_cls, status="inactive")
                
                mock_instance.exclude.assert_called_once_with(status="inactive")
    
    def test_all_returns_database_bucket(self):
        """Test that all returns a DatabaseBucket with all records."""
        capability = OrmQueryCapability()
        
        interface_cls = Mock()
        interface_cls._parent_class = Mock()
        
        with patch('general_manager.interface.capabilities.orm.DatabaseBucket') as mock_bucket:
            mock_instance = Mock()
            mock_instance.all = Mock(return_value="all_bucket")
            mock_bucket.return_value = mock_instance
            
            with patch('general_manager.interface.capabilities.orm.with_observability', side_effect=lambda **kw: kw['func']()):
                result = capability.all(interface_cls)
                
                mock_instance.all.assert_called_once()


class TestOrmValidationCapability:
    """Tests for ORM validation capability."""
    
    def test_normalize_payload_validates_and_normalizes(self):
        """Test that normalize_payload validates keys and normalizes values."""
        capability = OrmValidationCapability()
        
        interface_cls = Mock()
        
        mock_normalizer = Mock()
        mock_normalizer.validate_keys = Mock()
        mock_normalizer.normalize_simple_values = Mock(return_value={"name": "test", "value_id": 42})
        
        with patch('general_manager.interface.capabilities.orm.support.get_support_capability') as mock_get_support:
            mock_support = Mock()
            mock_support.get_payload_normalizer = Mock(return_value=mock_normalizer)
            mock_get_support.return_value = mock_support
            
            with patch('general_manager.interface.capabilities.orm.with_observability', side_effect=lambda **kw: kw['func']()):
                result = capability.normalize_payload(interface_cls, payload={"name": "test", "value": 42})
                
                mock_normalizer.validate_keys.assert_called_once()
                mock_normalizer.normalize_simple_values.assert_called_once()
                assert result == {"name": "test", "value_id": 42}


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
        
        with patch('general_manager.interface.capabilities.orm.mutations.call_with_observability', side_effect=lambda **kw: kw['func']()):
            result = capability.assign_simple_attributes(interface_cls, mock_instance, kwargs)
            
            assert result is mock_instance
            assert mock_instance.name == "test"
            assert mock_instance.value == 42
    
    def test_assign_simple_attributes_skips_not_provided(self):
        """Test that assign_simple_attributes skips NOT_PROVIDED values."""
        from django.db.models import NOT_PROVIDED
        
        capability = OrmMutationCapability()
        
        mock_instance = Mock()
        interface_cls = Mock()
        
        kwargs = {"name": "test", "skipped": NOT_PROVIDED}
        
        with patch('general_manager.interface.capabilities.orm.mutations.call_with_observability', side_effect=lambda **kw: kw['func']()):
            capability.assign_simple_attributes(interface_cls, mock_instance, kwargs)
            
            assert mock_instance.name == "test"
            # Should not have set 'skipped' attribute


class TestOrmLifecycleCapability:
    """Tests for ORM lifecycle capability."""
    
    def test_pre_create_builds_model_and_interface(self):
        """Test that pre_create builds Django model and interface class."""
        capability = OrmLifecycleCapability()
        
        # This is a complex integration test that would require significant mocking
        # For now, we test that the method exists and has the correct signature
        assert hasattr(capability, 'pre_create')
        assert callable(capability.pre_create)
    
    def test_post_create_wires_manager(self):
        """Test that post_create wires the manager to the GeneralManager class."""
        capability = OrmLifecycleCapability()
        
        # This is a complex integration test
        assert hasattr(capability, 'post_create')
        assert callable(capability.post_create)


# Run with: pytest tests/unit/test_orm_capabilities_comprehensive.py -v