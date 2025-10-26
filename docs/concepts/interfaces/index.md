# Interfaces Overview

Interfaces define how managers store or compute data. They encapsulate persistence logic, conversion between Django models and managers, and the inputs required to instantiate a manager.

GeneralManager ships with three main interface flavours:

- [Database interfaces](db_based_interface.md) persist records to relational databases.
- [Read-only interfaces](db_based_interface.md#read-only-data) synchronise static datasets from JSON.
- [Calculation interfaces](computed_data_interfaces.md) compute values on the fly from inputs and related managers.

All interfaces inherit from `general_manager.interface.base_interface.InterfaceBase`, which provides shared behaviour such as identification, validation, and integration with the dependency tracker.

Understanding the capabilities of each interface helps you pick the right tool for each domain object.
