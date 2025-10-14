# GeneralManager

[![PyPI](https://img.shields.io/pypi/v/GeneralManager.svg)](https://pypi.org/project/GeneralManager/)
[![Python](https://img.shields.io/pypi/pyversions/GeneralManager.svg)](https://pypi.org/project/GeneralManager/)
[![Build](https://github.com/TimKleindick/general_manager/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/TimKleindick/general_manager/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/codecov/c/github/TimKleindick/general_manager)](https://app.codecov.io/gh/TimKleindick/general_manager)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Overview

GeneralManager helps teams ship complex, data-driven products on top of Django without rewriting the same plumbing for every project. It combines domain modelling, GraphQL APIs, calculations, and permission logic in one toolkit so that you can focus on business rules instead of infrastructure.

## Documentation

The full documentation is published on GitHub Pages: [GeneralManager Documentation](https://timkleindick.github.io/general_manager/). It covers tutorials, concept guides, API reference, and examples.

## Key Features

- **Domain-first modelling**: Describe rich business entities in plain Python and let GeneralManager project them onto the Django ORM.
- **GraphQL without boilerplate**: Generate a complete API, then extend it with custom queries and mutations when needed.
- **Attribute-based access control**: Enforce permissions with `ManagerBasedPermission` down to single fields and operations.
- **Deterministic calculations**: Ship reusable interfaces e.g. for volume distributions, KPI calculations, and derived data.
- **Factory-powered testing**: Create large, realistic datasets quickly for demos, QA, and load tests.
- **Composable interfaces**: Connect to databases, spreadsheets, or computed sources with the same consistent abstractions.

## Quick Start

### Installation

Install the package from PyPI:

```bash
pip install GeneralManager
```

### Minimal example

```python
from datetime import date
from typing import Optional

from django.db.models import CharField, DateField

from general_manager import GeneralManager
from general_manager.interface.database import DatabaseInterface
from general_manager.measurement import Measurement, MeasurementField
from general_manager.permission import ManagerBasedPermission


class Project(GeneralManager):
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    total_capex: Optional[Measurement]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        start_date = DateField(null=True, blank=True)
        end_date = DateField(null=True, blank=True)
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isAdmin"]
        __update__ = ["isAdmin"]


Project.Factory.createBatch(10)
```

The example above defines a project model, exposes it through the auto-generated GraphQL schema, and produces ten sample records with a single call. The full documentation walks through extending this setup with custom rules, interfaces, and queries.

## Core Building Blocks

- **Entities & interfaces**: Compose domain entities with database-backed or computed interfaces to control persistence and data flows.
- **Rules & validation**: Protect your data with declarative constraints and business rules that run automatically.
- **Permissions**: Implement attribute-based access control with reusable policies that match your organisation’s roles.
- **GraphQL layer**: Serve a typed schema that mirrors your models and stays in sync as you iterate.
- **Caching & calculations**: Use the built-in caching decorator and calculation helpers to keep derived data fast and reliable.

## Production-Ready Extras

- Works with Postgres, SQLite, and any database supported by Django.
- Plays nicely with CI thanks to deterministic factories, typing, and code coverage.
- Ships with MkDocs documentation, auto-generated API reference, and a growing cookbook of recipes.
- Designed for teams: opinionated defaults without blocking custom extensions or overrides.

## Use Cases

- Internal tooling that mirrors real-world workflows, pricing models, or asset hierarchies.
- Customer-facing platforms that combine transactional data with live calculations.
- Analytics products that need controlled data sharing between teams or clients.
- Proof-of-concept projects that must scale into production without a rewrite.

## Requirements

- Python >= 3.12
- Django >= 5.2
- Additional dependencies (see `requirements/base.txt`):
  - `graphene`
  - `numpy`
  - `Pint`
  - `factory_boy`
  - and more.

## License

This project is distributed under the **MIT License**. For further details see the [LICENSE](./LICENSE) file.
