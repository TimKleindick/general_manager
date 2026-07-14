from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from simple_history.models import HistoricalRecords

from general_manager.interface.capabilities.orm import update_change_reason
from general_manager.interface.utils.history import DatabaseAwareHistoricalRecords


def test_base_history_uses_instance_database_alias_when_signal_omits_using() -> None:
    records = DatabaseAwareHistoricalRecords()
    instance = SimpleNamespace(_state=SimpleNamespace(db="secondary"))

    with patch.object(HistoricalRecords, "create_historical_record") as create_record:
        records.create_historical_record(instance, "~")

    create_record.assert_called_once_with(instance, "~", using="secondary")


def test_m2m_history_reads_and_writes_on_instance_database_alias() -> None:
    records = DatabaseAwareHistoricalRecords()
    field = Mock()
    field.name = "owners"
    through_model = Mock()
    through_model._meta.fields = []
    through_model.objects.filter.return_value.select_related.return_value = []
    alias_rows = through_model.objects.using.return_value.filter.return_value
    alias_rows.select_related.return_value = []
    history_m2m_model = Mock()
    records.m2m_models = {field: history_m2m_model}

    relation = SimpleNamespace(through=through_model)
    original_instance = SimpleNamespace(owners=relation)
    history_instance = SimpleNamespace(
        _history_m2m_fields=[field],
        _state=SimpleNamespace(db="secondary"),
        instance=original_instance,
        pk="history-pk",
    )
    instance = SimpleNamespace(_state=SimpleNamespace(db="secondary"))

    with patch(
        "simple_history.models.utils.get_m2m_field_name",
        return_value="multidatabaserecord",
    ):
        records.create_historical_record_m2ms(history_instance, instance)

    through_model.objects.using.assert_called_once_with("secondary")
    history_m2m_model.objects.using.assert_called_once_with("secondary")
    history_m2m_model.objects.using.return_value.bulk_create.assert_called_once()


def test_change_reason_uses_model_instance_database_alias() -> None:
    model_field = SimpleNamespace(attname="id", primary_key=True)
    instance = SimpleNamespace(
        id=7,
        pk=7,
        _state=SimpleNamespace(db="secondary"),
        _meta=SimpleNamespace(fields=[model_field]),
    )
    history_record = Mock()
    history_record.history_change_reason = None
    alias_history = Mock()
    alias_history.model._meta.fields = [model_field]
    alias_history.filter.return_value.order_by.return_value.first.return_value = (
        history_record
    )
    history_manager = Mock()
    history_manager.model._meta.fields = [model_field]
    history_manager.using.return_value = alias_history

    with patch(
        "general_manager.interface.utils.history.get_history_manager_for_model",
        return_value=history_manager,
    ):
        update_change_reason(instance, "secondary reason")  # type: ignore[arg-type]

    history_manager.using.assert_called_once_with("secondary")
    history_record.save.assert_called_once_with(using="secondary")
    assert history_record.history_change_reason == "secondary reason"
