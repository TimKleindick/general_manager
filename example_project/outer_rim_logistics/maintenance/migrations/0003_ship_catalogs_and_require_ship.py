# Generated manually on 2026-01-27

from __future__ import annotations

import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


def _seed_ship_catalogs(apps, _schema_editor):
    ShipClassCatalog = apps.get_model("maintenance", "ShipClassCatalog")
    ShipStatusCatalog = apps.get_model("maintenance", "ShipStatusCatalog")
    Ship = apps.get_model("maintenance", "Ship")
    Module = apps.get_model("maintenance", "Module")

    class_map = {
        "Courier Cruiser": "CC",
        "Cargo Hauler": "CH",
        "Escort Frigate": "EF",
    }
    status_map = {
        "active": "active",
        "docked": "docked",
        "maintenance": "maintenance",
    }

    for name, code in class_map.items():
        ShipClassCatalog.objects.get_or_create(
            name=name,
            defaults={"code": code, "description": ""},
        )
    for name, code in status_map.items():
        ShipStatusCatalog.objects.get_or_create(
            name=name if name[0].isupper() else name.title(),
            defaults={"code": code},
        )

    class_lookup = {sc.name: sc for sc in ShipClassCatalog.objects.all()}
    status_lookup = {ss.code: ss for ss in ShipStatusCatalog.objects.all()}

    for ship in Ship.objects.all():
        if ship.ship_class_ref_id is None:
            ship.ship_class_ref = class_lookup.get(
                getattr(ship, "ship_class", ""),
                next(iter(class_lookup.values())),
            )
        if ship.status_ref_id is None:
            raw_status = getattr(ship, "status", "")
            normalized_status = raw_status.lower() if isinstance(raw_status, str) else ""
            status_key = status_map.get(normalized_status, "active")
            ship.status_ref = status_lookup.get(
                status_key, next(iter(status_lookup.values()))
            )
        ship.save(update_fields=["ship_class_ref", "status_ref"])

    first_ship = Ship.objects.first()
    if first_ship:
        Module.objects.filter(ship__isnull=True).update(ship=first_ship)


def _noop_reverse(_apps, _schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0002_historicalship_ship_historicalmodule_ship_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShipClassCatalog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("code", models.CharField(max_length=20, unique=True)),
                ("description", models.CharField(max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name="ShipStatusCatalog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("name", models.CharField(max_length=80, unique=True)),
                ("code", models.CharField(max_length=20, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name="HistoricalShipClassCatalog",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True, blank=True, db_index=True, verbose_name="ID"
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("name", models.CharField(db_index=True, max_length=120)),
                ("code", models.CharField(db_index=True, max_length=20)),
                ("description", models.CharField(max_length=255)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                (
                    "history_type",
                    models.CharField(
                        choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")],
                        max_length=1,
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "historical ship class catalog",
                "verbose_name_plural": "historical ship class catalogs",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalShipStatusCatalog",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True, blank=True, db_index=True, verbose_name="ID"
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("name", models.CharField(db_index=True, max_length=80)),
                ("code", models.CharField(db_index=True, max_length=20)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                (
                    "history_type",
                    models.CharField(
                        choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")],
                        max_length=1,
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "historical ship status catalog",
                "verbose_name_plural": "historical ship status catalogs",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.AddField(
            model_name="ship",
            name="ship_class_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="maintenance.shipclasscatalog",
            ),
        ),
        migrations.AddField(
            model_name="ship",
            name="status_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="maintenance.shipstatuscatalog",
            ),
        ),
        migrations.AddField(
            model_name="historicalship",
            name="ship_class_ref",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="maintenance.shipclasscatalog",
            ),
        ),
        migrations.AddField(
            model_name="historicalship",
            name="status_ref",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="maintenance.shipstatuscatalog",
            ),
        ),
        migrations.RunPython(_seed_ship_catalogs, _noop_reverse),
        migrations.RemoveField(model_name="ship", name="ship_class"),
        migrations.RemoveField(model_name="ship", name="status"),
        migrations.RemoveField(model_name="historicalship", name="ship_class"),
        migrations.RemoveField(model_name="historicalship", name="status"),
        migrations.RenameField(model_name="ship", old_name="ship_class_ref", new_name="ship_class"),
        migrations.RenameField(model_name="ship", old_name="status_ref", new_name="status"),
        migrations.RenameField(model_name="historicalship", old_name="ship_class_ref", new_name="ship_class"),
        migrations.RenameField(model_name="historicalship", old_name="status_ref", new_name="status"),
        migrations.AlterField(
            model_name="ship",
            name="ship_class",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="maintenance.shipclasscatalog",
            ),
        ),
        migrations.AlterField(
            model_name="ship",
            name="status",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="maintenance.shipstatuscatalog",
            ),
        ),
        migrations.AlterField(
            model_name="module",
            name="ship",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="maintenance.ship",
            ),
        ),
    ]
