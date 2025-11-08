"""Database-driven GeneralManager interfaces."""

from .database_based_interface import DBBasedInterface, WritableDBBasedInterface
from .database_interface import DatabaseInterface

__all__ = [
    "DBBasedInterface",
    "DatabaseInterface",
    "WritableDBBasedInterface",
]
