# GeneralManager User Guide

This document explains how to use the GeneralManager framework from an application developer's perspective. It provides a brief introduction and points you to the detailed reference files for more information.

## Getting Started

Install the package with `pip install GeneralManager`. After installation you can start defining your own managers as shown below.

### Example

```python
from datetime import date
from typing import Optional, cast

from django.core.validators import RegexValidator
from django.db.models import CharField, DateField, TextField, constraints

from general_manager.bucket.databaseBucket import DatabaseBucket
from general_manager.factory import LazyDeltaDate, LazyMeasurement, LazyProjectName
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import MeasurementField, Measurement
from general_manager.permission import ManagerBasedPermission
from general_manager.rule import Rule

# Example derivative manager from your application
from yourapp.managers import Derivative

class Project(GeneralManager):
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    total_capex: Optional[Measurement]
    derivative_list: DatabaseBucket[Derivative]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        number = CharField(max_length=7, validators=[RegexValidator(r"^AP\d{4,5}$")])
        description = TextField(null=True, blank=True)
        start_date = DateField(null=True, blank=True)
        end_date = DateField(null=True, blank=True)
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

        class Meta:
            constraints = [
                constraints.UniqueConstraint(fields=["name", "number"], name="unique_booking")
            ]
            rules = [
                Rule["Project"](lambda x: x.start_date < x.end_date),
                Rule["Project"](lambda x: x.total_capex >= "0 EUR"),
            ]

        class Factory:
            name = LazyProjectName()
            end_date = LazyDeltaDate(365 * 6, "start_date")
            total_capex = LazyMeasurement(75_000, 1_000_000, "EUR")

    class Permission(ManagerBasedPermission):
        __read__ = ["ends_with:name:X-771", "public"]
        __create__ = ["admin", "isMatchingKeyAccount"]
        __update__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]
        __delete__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]

        total_capex = {"update": ["isSalesResponsible", "isProjectManager"]}

Project.Factory.createBatch(10)
```


## Manager Basics

A manager is a small class derived from `GeneralManager`. It declares the attributes you want to expose as type annotations and configures its data access through a nested `Interface` class. The interface can be a `DatabaseInterface`, a `ReadOnlyInterface` or a `CalculationInterface` depending on whether the data is stored, static or computed.

Every manager provides a common set of operations:

- `create()` to add a new object via the interface.
- `update()` to modify an existing object.
- `deactivate()` to mark an object as inactive instead of deleting it.
- `filter()` and `exclude()` to build a bucket containing matching managers.
- `all()` to retrieve a bucket with every instance.
- Managers can be combined with the `|` operator to merge buckets or add single objects to a bucket.

Buckets are collections of managers. They behave like lists and support iteration, slicing and indexing. You can access elements with `first()`, `last()` or `get()`, sort them with `sort()` and group them with `group_by()` as described in [GroupManager.md](GroupManager.md).

## Defining Models

Most managers use the `DatabaseInterface` to map a manager to a Django model. The interface defines all fields and connects CRUD operations. Example usage can be found in the [DatabaseInterface documentation](DatabaseInterface.md).

## Read-Only Data

If you need to store static lookup tables that should never be edited by users you can use `ReadOnlyInterface`. It synchronises JSON data with the database on startup. See the [ReadOnlyInterface documentation](ReadOnlyInterface.md) for details.

## Calculations and Derived Data

For derived values that do not need database storage use the `CalculationInterface`. Inputs are declared with the `Input` class and results are exposed through `@graphQlProperty`. Read more in [CalculationInterface.md](CalculationInterface.md).

The `Input` class itself is explained in [Input.md](Input.md).

## Working with Measurements

To handle physical units or currencies the framework provides the `Measurement` class. It behaves like a numeric value and supports unit conversions. The [Measurements documentation](Measurements.md) describes available operations and how to store measurements in models.

## Grouping and Aggregation

Buckets of managers can be grouped by one or more attributes using `group_by()`. Each group provides merged access to the contained objects. Refer to [GroupManager.md](GroupManager.md) for usage examples.

## Rules and Validation

Managers can validate their data using small predicate functions called rules. Rules are defined on the interface and prevent invalid updates. The full system is documented in [Rules.md](Rules.md).

## GraphQL API

GeneralManager automatically exposes models through GraphQL. You can also create custom mutations using `graphQlMutation`. See [GraphQlMutation.md](GraphQlMutation.md) for an example.

```graphql
query {
  projectList {
    name
    startDate
    endDate
    totalCapex {
      value
      unit
    }
  }
}
```

With these components you can build a modular and scalable data layer for your application. Check the individual documentation files whenever you need more in depth information about a feature.
