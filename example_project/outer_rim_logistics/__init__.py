"""Outer Rim Logistics example project package."""

from __future__ import annotations

import importlib
import sys

for _module in ("crew", "maintenance", "mission", "supply"):
    sys.modules.setdefault(_module, importlib.import_module(f"{__name__}.{_module}"))
