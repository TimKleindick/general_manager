# Project Volume Curve

This recipe shows how to compute a volume curve for a project by grouping derivative volumes per date.

```python
from datetime import date

from django.db.models import DateField, ForeignKey

from core.managers import Derivative, Project
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement, MeasurementField

class DerivativeVolume(GeneralManager):
    project: Project
    derivative: Derivative
    date: date
    volume: Measurement

    class Interface(DatabaseInterface):
        project = ForeignKey(Project, on_delete=models.CASCADE)
        derivative = ForeignKey(Derivative, on_delete=models.CASCADE)
        date = DateField()
        volume = MeasurementField(base_unit="kWh")
```

```python
def project_volume_curve(project: Project) -> tuple[tuple[date, Measurement], ...]:
    grouped = (
        project.derivative_volume_list
        .filter(project=project)
        .group_by("date")
        .sort("date")
    )
    return grouped.values_list("date", "volume")
```

Result:

```python
curve = project_volume_curve(Project(id=1))
for entry_date, volume in curve:
    print(entry_date, volume.to("MWh"))
```

Use this pattern to power charts in dashboards or export data to CSV.

## Project selected fields

`values_list()` materializes the grouped bucket in its existing order without
constructing another list of manager objects. Use `values("date", "volume")`
instead when a dictionary shape is more convenient for a serializer or CSV
writer. Both forms keep the group values shallow and preserve the bucket's
source and historical context.
