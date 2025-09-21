"""
Additional unit tests focused on new/changed logic.

Testing library/framework:
- Primary: pytest (assert-style tests), with unittest.mock for patching.
- Rationale: This repository appears to favor pytest patterns (asserts, function tests).
  These tests avoid non-standard plugins so they can also run under unittest-compatible runners.

Scope:
- Pure/function-style behaviors for robustness without requiring a live Django/DB stack.
- Focus on functions visible in the provided diff: 
  - generate_volume_distribution
  - generateVolume
  - getPossibleDates
  - getPossibleProjects (delegation behavior)
  - startProject (argument plumbing and delegation)
"""

from __future__ import annotations

import math
import random
import types
import sys
import pathlib
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


# --- Test helper: safe-load the prototype module with lightweight stubs if Django/general_manager are absent ---


def _ensure_stubbed_imports():
    """
    Install minimal stub modules only if the real ones are not importable.
    This avoids polluting environments that already provide Django/general_manager.
    """
    import importlib

    def _ensure_module(path: str) -> types.ModuleType:
        if path in sys.modules:
            return sys.modules[path]
        mod = types.ModuleType(path)
        sys.modules[path] = mod
        return mod

    # Attempt to import Django; if unavailable, create stubs
    try:
        importlib.import_module("django")
    except ImportError:
        _ensure_module("django")
        _ensure_module("django.db")
        django_db_models = _ensure_module("django.db.models")
        _ensure_module("django.core")
        django_core_validators = _ensure_module("django.core.validators")

        class _Field:
            def __init__(self, *args, **kwargs):
                pass

        class CharField(_Field): ...
        class TextField(_Field): ...
        class DateField(_Field): ...
        class IntegerField(_Field): ...
        class BooleanField(_Field): ...
        class ForeignKey(_Field):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

        CASCADE = object()

        class _UniqueConstraint:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        class _Constraints(types.SimpleNamespace):
            UniqueConstraint = _UniqueConstraint

        django_db_models.CharField = CharField
        django_db_models.TextField = TextField
        django_db_models.DateField = DateField
        django_db_models.IntegerField = IntegerField
        django_db_models.ForeignKey = ForeignKey
        django_db_models.BooleanField = BooleanField
        django_db_models.CASCADE = CASCADE
        django_db_models.constraints = _Constraints()

        def RegexValidator(pattern):
            # Minimal no-op validator
            def _validate(_value):  # pragma: no cover - not exercised
                return True
            _validate.pattern = pattern
            return _validate

        django_core_validators.RegexValidator = RegexValidator

    # Attempt to import general_manager; if unavailable, create stubs
    try:
        importlib.import_module("general_manager")
    except ImportError:
        _ensure_module("general_manager")
        _ensure_module("general_manager.interface")
        _ensure_module("general_manager.bucket")
        gm_bucket_db = _ensure_module("general_manager.bucket.databaseBucket")
        gm_manager = _ensure_module("general_manager.manager")
        gm_permission = _ensure_module("general_manager.permission")
        gm_measurement = _ensure_module("general_manager.measurement")
        gm_rule = _ensure_module("general_manager.rule")
        gm_factory = _ensure_module("general_manager.factory")
        gm_utils = _ensure_module("general_manager.utils")
        _ensure_module("general_manager.api")
        gm_api_mut = _ensure_module("general_manager.api.mutation")
        gm_ro = _ensure_module("general_manager.interface.readOnlyInterface")
        gm_calc = _ensure_module("general_manager.interface.calculationInterface")
        gm_dbif = _ensure_module("general_manager.interface.databaseInterface")

        # Minimal stubs
        class GeneralManager:  # pragma: no cover - behavior not exercised directly
            pass

        def graphQlProperty(*dargs, **dkwargs):
            # Supports both @graphQlProperty and @graphQlProperty(...)
            if dargs and callable(dargs[0]) and not dkwargs:
                return dargs[0]
            def _wrap(func):
                return func
            return _wrap

        class Input:
            def __init__(self, typ, possible_values=None):
                self.typ = typ
                self.possible_values = possible_values

        gm_manager.GeneralManager = GeneralManager
        gm_manager.graphQlProperty = graphQlProperty
        gm_manager.Input = Input

        class ManagerBasedPermission:  # pragma: no cover
            pass

        gm_permission.ManagerBasedPermission = ManagerBasedPermission

        class MeasurementField:  # pragma: no cover
            def __init__(self, *args, **kwargs):
                pass

        class Measurement(float):
            # Provide minimal ops used in code paths (multiplication)
            def __new__(cls, value=0.0, unit=""):
                obj = float.__new__(cls, float(value))
                obj.unit = unit
                return obj

            def __mul__(self, other):
                try:
                    return Measurement(float(self) * float(other), getattr(self, "unit", ""))
                except (TypeError, ValueError):
                    return float(self) * other

            __rmul__ = __mul__

        gm_measurement.MeasurementField = MeasurementField
        gm_measurement.Measurement = Measurement

        class Rule:  # pragma: no cover - not executed at import
            def __init__(self, fn):
                self.fn = fn
            def __class_getitem__(cls, item):
                return cls

        gm_rule.Rule = Rule

        class LazyMeasurement:  # pragma: no cover
            def __init__(self, *args, **kwargs): ...
        class LazyDeltaDate:  # pragma: no cover
            def __init__(self, *args, **kwargs): ...
        class LazyProjectName:  # pragma: no cover
            def __init__(self, *args, **kwargs): ...

        gm_factory.LazyMeasurement = LazyMeasurement
        gm_factory.LazyDeltaDate = LazyDeltaDate
        gm_factory.LazyProjectName = LazyProjectName

        def noneToZero(v):
            return 0 if v is None else v

        gm_utils.noneToZero = noneToZero

        def graphQlMutation(fn=None, *_args, **_kwargs):
            if callable(fn):
                return fn
            def _wrap(inner):
                return inner
            return _wrap

        gm_api_mut.graphQlMutation = graphQlMutation

        # Interfaces
        class ReadOnlyInterface: ...
        class CalculationInterface: ...
        class DatabaseInterface: ...

        gm_ro.ReadOnlyInterface = ReadOnlyInterface
        gm_calc.CalculationInterface = CalculationInterface
        gm_dbif.DatabaseInterface = DatabaseInterface

        # Buckets (not needed for our tests)
        class DatabaseBucket: ...  # pragma: no cover
        gm_bucket_db.DatabaseBucket = DatabaseBucket


def load_prototype_module():
    """
    Dynamically load tests/unit/test_prototype.py as a module named 'prototype_module',
    installing minimal stubs for external deps if necessary.
    """
    _ensure_stubbed_imports()
    module_path = pathlib.Path(__file__).resolve().parent / "test_prototype.py"
    assert module_path.exists(), f"Missing module under test at {module_path}"
    import importlib.util
    spec = importlib.util.spec_from_file_location("prototype_module", str(module_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["prototype_module"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


proto = load_prototype_module()


# --- Tests for generate_volume_distribution ---


@pytest.mark.parametrize("years,total", [(6, 1000.0), (9, 2500.0), (12, 0.0)])
def test_generate_volume_distribution_invariants(years, total):
    random.seed(12345)
    vols = proto.generate_volume_distribution(years, total)
    assert isinstance(vols, list)
    assert len(vols) == years
    # Non-negativity
    assert all(v >= 0 for v in vols)
    # Sum to total within tolerance (allowing floating and normalization jitter)
    assert math.isclose(sum(vols), total, rel_tol=1e-7, abs_tol=1e-6)


@pytest.mark.parametrize("bad_years", [0, 1, 2])
def test_generate_volume_distribution_raises_for_too_few_years(bad_years):
    random.seed(42)
    with pytest.raises(ValueError):
        proto.generate_volume_distribution(bad_years, 100.0)


# --- Tests for generateVolume ---


def _project_stub(start: date | None, end: date | None):
    return SimpleNamespace(start_date=start, end_date=end)


@pytest.mark.parametrize(
    "desc,derivative_kwargs",
    [
        ("no project", {"estimated_volume": 1000, "project": None}),
        ("no total volume", {"estimated_volume": None, "project": _project_stub(date(2020, 1, 1), date(2026, 1, 1))}),
        ("no start date", {"estimated_volume": 1000, "project": _project_stub(None, date(2026, 1, 1))}),
        ("no end date", {"estimated_volume": 1000, "project": _project_stub(date(2020, 1, 1), None)}),
    ],
)
def test_generateVolume_returns_empty_when_required_data_missing(_desc, derivative_kwargs):
    random.seed(999)
    derivative = SimpleNamespace(**derivative_kwargs)
    out = proto.generateVolume(derivative=derivative, other="kept")
    assert out == []


def test_generateVolume_happy_path_distribution_and_shape():
    random.seed(20240907)
    start = date(2020, 1, 1)
    end = date(2026, 1, 1)  # 6 years
    derivative = SimpleNamespace(estimated_volume=1500.0, project=_project_stub(start, end))
    out = proto.generateVolume(derivative=derivative, tag="x")
    # Expect one record per whole year in [start.year, end.year)
    assert len(out) == (end.year - start.year) == 6
    # Records should include kwargs merged back
    assert all(r["derivative"] is derivative and r["tag"] == "x" for r in out)
    # Dates should be Jan 1st of each year
    years = [start.year + i for i in range(0, end.year - start.year)]
    assert [r["date"] for r in out] == [date(y, 1, 1) for y in years]
    # Volumes should be floats and non-negative
    vols = [r["volume"] for r in out]
    assert all(isinstance(v, float) and v >= 0 for v in vols)
    # Conservation of total volume within tolerance
    assert math.isclose(sum(vols), 1500.0, rel_tol=1e-7, abs_tol=1e-6)


# --- Tests for getPossibleDates ---


def test_getPossibleDates_collects_only_valid_dates_and_sorts():
    class Vol:
        def __init__(self, d): self.date = d

    # Mixed valid/invalid dates
    d1 = Vol(date(2022, 1, 1))
    d2 = Vol("2021-01-01")  # invalid type -> skipped
    d3 = Vol(date(2021, 1, 1))
    d4 = Vol(date(2023, 1, 1))

    der1 = SimpleNamespace(derivativevolume_list=[d1, d2])
    der2 = SimpleNamespace(derivativevolume_list=[d4, d3])
    project = SimpleNamespace(derivative_list=[der1, der2])

    got = proto.getPossibleDates(project)
    assert got == [date(2021, 1, 1), date(2022, 1, 1), date(2023, 1, 1)]


# --- Tests for getPossibleProjects delegation ---


def test_getPossibleProjects_delegates_to_Project_exclude():
    expected = ["proj-a", "proj-b"]
    # Patch Project.exclude (it may not exist on the stubbed class)
    with patch.object(proto.Project, "exclude", create=True) as exclude_mock:
        exclude_mock.return_value = expected
        got = proto.getPossibleProjects()
        assert got == expected
        # Ensure the function was called with the correct filter expression
        kwargs = exclude_mock.call_args.kwargs
        assert kwargs == {"derivative__derivativevolume__isnull": True}


# --- Tests for startProject mutation plumbing ---


def test_startProject_calls_create_and_returns_instances():
    # Prepare mocks for Project.create and Derivative.create
    project_instance = SimpleNamespace(id=1, name="P1")
    derivative_instance = SimpleNamespace(id=2, name="D1")

    with patch.object(proto.Project, "create", create=True) as project_create, \
         patch.object(proto.Derivative, "create", create=True) as derivative_create:
        project_create.return_value = project_instance
        derivative_create.return_value = derivative_instance

        user = SimpleNamespace(id=777)
        info = SimpleNamespace(context=SimpleNamespace(user=user))

        p_name = "Alpha"
        p_num = "AP1234"
        d_name = "Deriv-X"
        d_weight = 12.5  # Accept any value; passed through
        d_volume = 42
        s_date = date(2024, 5, 1)
        e_date = date(2026, 5, 1)

        result = proto.startProject(
            info,
            project_name=p_name,
            project_number=p_num,
            derivative_name=d_name,
            derivative_weight=d_weight,
            derivative_volume=d_volume,
            start_date=s_date,
            end_date=e_date,
        )

        # Return value
        assert result == (project_instance, derivative_instance)

        # Project.create called with expected fields
        project_create.assert_called_once()
        pc_kwargs = project_create.call_args.kwargs
        assert pc_kwargs["name"] == p_name
        assert pc_kwargs["number"] == p_num
        assert pc_kwargs["start_date"] == s_date
        assert pc_kwargs["end_date"] == e_date
        assert pc_kwargs["creator_id"] == 777

        # Derivative.create called with expected fields and back-reference to project
        derivative_create.assert_called_once()
        dc_kwargs = derivative_create.call_args.kwargs
        assert dc_kwargs["name"] == d_name
        assert dc_kwargs["estimated_weight"] == d_weight
        assert dc_kwargs["estimated_volume"] == d_volume
        assert dc_kwargs["project"] is project_instance
        assert dc_kwargs["creator_id"] == 777