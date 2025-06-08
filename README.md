# GeneralManager

## Overview

GeneralManager is a powerful and flexible framework designed for managing and processing data. It provides a modular structure that enables developers to implement complex business logic efficiently. The module is written entirely in Python and uses Django as the backend framework.

## Key Features

### 1. **Data Management**
- **Flexibility**: Supports managing all kinds of data, not just projects and derivatives.
- **Database Integration**: Seamless integration with the Django ORM for database operations.
- **External Interfaces**: Support for interfaces to other programs, such as Excel.

### 2. **Data Modeling**
- **Django Models**: The data structure is based on Django models, extended by custom fields like `MeasurementField`.
- **Rules and Validations**: Define rules for data validation, e.g., ensuring that a project's start date is before its end date.

### 3. **GraphQL Integration**
- Automatic generation of GraphQL interfaces for all models.
- Support for custom queries and mutations.

### 4. **Permission System**
- **ManagerBasedPermission**: A flexible permission system based on user roles and attributes.
- Attribute-level CRUD permissions.

### 5. **Interfaces**
- **CalculationInterface**: Allows the implementation of calculation logic.
- **DatabaseInterface**: Provides a standardized interface for database operations.
- **ReadOnlyInterface**: For read-only data access.

### 6. **Data Distribution and Calculations**
- **Volume Distribution**: Automatically calculates and distributes volume over multiple years.
- **Commercial Calculations**: Calculates total volume, shipping costs, and revenue for projects.

## Usage

### Installation

Install the module via `pip`:

```bash
pip install GeneralManager
```

### Example Code

The following example demonstrates how to create a GeneralManager and generate sample data (in this case 10 projects):

```python
from general_manager import GeneralManager
from general_manager.interface.database import DatabaseInterface
from general_manager.measurement import MeasurementField, Measurement
from general_manager.permission import ManagerBasedPermission

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
                constraints.UniqueConstraint(
                    fields=["name", "number"], name="unique_booking"
                )
            ]

            rules = [
                Rule["Project"](
                    lambda x: cast(date, x.start_date) < cast(date, x.end_date)
                ),
                Rule["Project"](lambda x: cast(Measurement, x.total_capex) >= "0 EUR"),
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

### GraphQL Integration

The module automatically generates GraphQL endpoints for all models. You can run queries and mutations through the GraphQL URL defined in your Django settings.

Example of a GraphQL query:

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

## Benefits

- **Modularity**: Easy to extend and adapt.
- **Flexibility**: Supports complex business logic and calculations.
- **Integration**: Seamless integration with Django and GraphQL.
- **Permissions**: Fine-grained permissions for users and attributes.
- **Data Validation**: Automatic validation of data through rules and constraints.
- **Caching**: Automatic cache generation with the `@cached` decorator to improve performance.

## Requirements

- Python >= 3.12
- Django >= 5.2
- Additional dependencies (see `requirements.txt`):
  - `graphene`
  - `numpy`
  - `Pint`
  - `factory_boy`
  - and more.

## License

This project is distributed under the **Non-Commercial MIT License**. It may only be used for non-commercial purposes. For further details see the [LICENSE](./LICENSE) file.
