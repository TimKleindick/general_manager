# Derivative Aggregation

Group derivatives by project and maturity to produce aggregate statistics.
This application-specific recipe assumes `Project.derivative_list` is a bucket
of derivatives with a non-null `maturity_date` and a non-null energy `volume`
measurement. It extends a domain model; the quickstart's name-only `Project`
does not provide these fields. Adjust the manager import to your application.

```python
from core.managers import Project
from general_manager.measurement import Measurement

def aggregate_derivatives(project: Project) -> dict[str, Measurement]:
    total_volume = Measurement(0, "kWh")
    groups = (
        project.derivative_list
        .group_by("maturity_date")
        .sort("maturity_date")
    )
    result: dict[str, Measurement] = {}
    for group in groups:
        key = group.maturity_date.isoformat()
        result[key] = group.volume.to("MWh")
        total_volume += group.volume
    result["total"] = total_volume.to("MWh")
    return result
```
