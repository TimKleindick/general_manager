"""Outer Rim Logistics example project package."""

from __future__ import annotations

import importlib

for _module in ("crew", "maintenance", "mission", "supply"):
    globals()[_module] = importlib.import_module(f"{__name__}.{_module}")
