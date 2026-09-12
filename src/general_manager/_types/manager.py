"""Type-only imports for public API re-exports."""

from __future__ import annotations

__all__ = [
    "CreateManyBatchResult",
    "CreateManyError",
    "CreateManyInvalidBatchSizeError",
    "CreateManyPostCommitError",
    "CreateManyUnsupportedError",
    "DateRangeDomain",
    "GeneralManager",
    "GeneralManagerMeta",
    "GroupManager",
    "Input",
    "InputDomain",
    "NumericRangeDomain",
    "TrustedOrmHydrationNotSupportedError",
    "UnsupportedUnionOperandError",
    "graph_ql_property",
]

from general_manager.manager.input import DateRangeDomain
from general_manager.manager.bulk_create import CreateManyBatchResult
from general_manager.manager.bulk_create import CreateManyError
from general_manager.manager.bulk_create import CreateManyInvalidBatchSizeError
from general_manager.manager.bulk_create import CreateManyPostCommitError
from general_manager.manager.bulk_create import CreateManyUnsupportedError
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.group_manager import GroupManager
from general_manager.manager.input import Input
from general_manager.manager.input import InputDomain
from general_manager.manager.input import NumericRangeDomain
from general_manager.manager.general_manager import TrustedOrmHydrationNotSupportedError
from general_manager.manager.general_manager import UnsupportedUnionOperandError
from general_manager.api.property import graph_ql_property
