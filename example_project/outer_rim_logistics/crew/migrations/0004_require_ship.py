# Generated manually on 2026-01-27

from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def _assign_ship(apps, _schema_editor):
    CrewMember = apps.get_model("crew", "CrewMember")
    Ship = apps.get_model("maintenance", "Ship")
    ShipClassCatalog = apps.get_model("maintenance", "ShipClassCatalog")
    ShipStatusCatalog = apps.get_model("maintenance", "ShipStatusCatalog")
    ship = Ship.objects.first()
    if ship is None:
        ship_class = ShipClassCatalog.objects.order_by("id").first()
        if ship_class is None:
            ship_class = ShipClassCatalog.objects.create(
                name="Courier Cruiser",
                code="CC",
                description="Auto-created for crew migration",
            )
        status = ShipStatusCatalog.objects.order_by("id").first()
        if status is None:
            status = ShipStatusCatalog.objects.create(
                name="Active",
                code="active",
            )
        ship = Ship.objects.create(
            name="Outer Rim Reliant",
            registry="ORL-DEFAULT",
            ship_class=ship_class,
            status=status,
        )
    CrewMember.objects.filter(ship__isnull=True).update(ship=ship)


def _noop_reverse(_apps, _schema_editor):
    return None


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("crew", "0003_crewmember_ship_historicalcrewmember_ship"),
        ("maintenance", "0003_ship_catalogs_and_require_ship"),
    ]

    operations = [
        migrations.RunPython(_assign_ship, _noop_reverse),
        migrations.AlterField(
            model_name="crewmember",
            name="ship",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="maintenance.ship",
            ),
        ),
        migrations.AlterField(
            model_name="historicalcrewmember",
            name="ship",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="maintenance.ship",
            ),
        ),
    ]
