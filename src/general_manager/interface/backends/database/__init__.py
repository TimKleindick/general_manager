"""Database-driven GeneralManager interfaces."""

from .database_based_interface import OrmInterfaceBase
from .database_interface import DatabaseInterface

__all__ = [
    "DatabaseInterface",
    "OrmInterfaceBase",
]
