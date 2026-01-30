# Generated manually on 2026-01-27

from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def _assign_ship(apps, _schema_editor):
    CrewMember = apps.get_model("crew", "CrewMember")
    Ship = apps.get_model("maintenance", "Ship")
    ship = Ship.objects.first()
    if ship is None:
        return
    CrewMember.objects.filter(ship__isnull=True).update(ship=ship)


def _noop_reverse(_apps, _schema_editor):
    return None


class Migration(migrations.Migration):
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
