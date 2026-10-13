"""Type-check the public collection Input declarations."""

from datetime import date
from typing import assert_type

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input


class CollectionEmployee(GeneralManager):
    class Interface(CalculationInterface):
        id = Input(int)


class CollectionConsumer(GeneralManager):
    month: date
    employees: list[CollectionEmployee] | None

    class Interface(CalculationInterface):
        month = Input(date)
        employees = Input(list[CollectionEmployee], required=False)
        values = Input(list[int])


def accepts_consumer(summary: CollectionConsumer) -> None:
    assert_type(summary.month, date)
    assert_type(summary.employees, list[CollectionEmployee] | None)


CollectionConsumer(month="2026-10-01", employees_id=["17"], values=[1, 2])
