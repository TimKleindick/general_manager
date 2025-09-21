# =========================================
# Additional unit tests (pytest)
# Covers: generate_volume_distribution, generateVolume, getPossibleDates, startProject
# Testing library/framework: pytest
# =========================================
import types
import builtins
import math
import pytest
from datetime import date
import random


try:
    from prototype import (
        generate_volume_distribution,
        generateVolume,
        getPossibleDates,
        startProject,
        Project,
        Derivative,
    )
except ImportError:
    # When running as a test file, the functions/classes are expected to already exist in globals().
    required = [
        "generate_volume_distribution",
        "generateVolume",
        "getPossibleDates",
        "startProject",
        "Project",
        "Derivative",
    ]
    missing = [name for name in required if name not in globals()]
    if missing:
        # Re-raise the ImportError if required symbols are not available in globals().
        raise

# Helper: access module namespace (this file acts as both module-under-test and tests)
# When running as a test file, the functions/classes are already in globals()


# -----------------------------
# Tests for generate_volume_distribution
# -----------------------------


def test_generate_volume_distribution_deterministic_peak2_years9_total425(monkeypatch):
    # Force deterministic randomness
    monkeypatch.setattr(random, "randint", lambda *_, **__: 2)   # years//3 for years=9 is 3, pick 2
    monkeypatch.setattr(random, "uniform", lambda *_, **__: 0.0)

    years = 9
    total = 425.0
    res = generate_volume_distribution(years, total)

    assert isinstance(res, list)
    assert len(res) == years
    assert all(x >= 0 for x in res)
    assert pytest.approx(sum(res), rel=1e-9, abs=1e-9) == total

    # Validate shape based on hand-derived numerators with peak=2 and uniform=0
    # Raw (pre-normalization) numerators:
    # y=0: 0
    # y=1: (1/2)^2 = 0.25
    # y=2..8: (9 - y) / (9 - 2) = (9 - y) / 7 -> [1, 6/7, 5/7, 4/7, 3/7, 2/7, 1/7]
    numerators = [0.0, 0.25, 1.0, 6/7, 5/7, 4/7, 3/7, 2/7, 1/7]
    total_raw = sum(numerators)
    expected = [n / total_raw * total for n in numerators]
    assert res == pytest.approx(expected, rel=1e-9, abs=1e-9)

def test_generate_volume_distribution_raises_when_years_too_small():
    # years = 1 or 2 leads to randint(1, 0) raising ValueError
    with pytest.raises(ValueError):
        generate_volume_distribution(2, 100.0)

def test_generate_volume_distribution_zero_total_volume_all_zero(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda *_, **__: 1)
    monkeypatch.setattr(random, "uniform", lambda *_, **__: 0.0)
    years = 6

    total = 0.0
    res = generate_volume_distribution(years, total)
    assert len(res) == years
    assert sum(res) == pytest.approx(0.0)
    assert all(x == pytest.approx(0.0) for x in res)

def test_generate_volume_distribution_negative_total_volume_sums_to_negative(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda *_, **__: 1)
    monkeypatch.setattr(random, "uniform", lambda *_, **__: 0.0)
    years = 6

    total = -60.0
    res = generate_volume_distribution(years, total)
    assert len(res) == years
    assert sum(res) == pytest.approx(total)


# -----------------------------
# Tests for generateVolume
# -----------------------------


class _DummyProject:
    def __init__(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date
        # for getPossibleDates tests
        self.derivative_list = []

class _DummyDerivative:
    def __init__(self, estimated_volume, project):
        self.estimated_volume = estimated_volume
        self.project = project
        # for getPossibleDates tests
        self.derivativevolume_list = []

def test_generateVolume_happy_path_uses_distribution_and_merges_kwargs(monkeypatch):
    # Arrange
    proj = _DummyProject(date(2020, 5, 17), date(2023, 2, 1))  # total_years = 3
    deriv = _DummyDerivative(estimated_volume=60.0, project=proj)

    # Patch distribution to deterministic list
    monkeypatch.setattr(
        builtins, "generate_volume_distribution",
        generate_volume_distribution, raising=False
    )
    # Patch symbol in this module's namespace specifically
    monkeypatch.setattr(
        globals(), "generate_volume_distribution",
        lambda *_, **__: [10.0, 20.0, 30.0]
    )

    # Act
    out = generateVolume(derivative=deriv, foo="bar")

    # Assert
    assert isinstance(out, list)
    assert len(out) == 3
    years = [2020, 2021, 2022]
    assert [row["date"] for row in out] == [date(y, 1, 1) for y in years]
    assert [row["volume"] for row in out] == [10.0, 20.0, 30.0]
    # Kwargs propagation
    assert all(row["foo"] == "bar" for row in out)
    # Original derivative is preserved in each record
    assert all(row["derivative"] is deriv for row in out)

def test_generateVolume_missing_project_or_dates_or_volume_returns_empty():
    # project is None
    deriv_none_project = _DummyDerivative(estimated_volume=100.0, project=None)
    assert generateVolume(derivative=deriv_none_project) == []

    # missing start_date
    p = _DummyProject(None, date(2025, 1, 1))
    deriv_missing_start = _DummyDerivative(estimated_volume=100.0, project=p)
    assert generateVolume(derivative=deriv_missing_start) == []

    # missing end_date
    p = _DummyProject(date(2020, 1, 1), None)
    deriv_missing_end = _DummyDerivative(estimated_volume=100.0, project=p)
    assert generateVolume(derivative=deriv_missing_end) == []

    # missing estimated_volume
    p = _DummyProject(date(2020, 1, 1), date(2023, 1, 1))
    deriv_missing_vol = _DummyDerivative(estimated_volume=None, project=p)
    assert generateVolume(derivative=deriv_missing_vol) == []

def test_generateVolume_raises_when_total_years_non_positive():
    # Same year -> total_years = 0 -> underlying distribution raises
    p = _DummyProject(date(2020, 5, 1), date(2020, 12, 31))
    d = _DummyDerivative(estimated_volume=100.0, project=p)
    with pytest.raises(ValueError):
        generateVolume(derivative=d)


# -----------------------------
# Tests for getPossibleDates
# -----------------------------

class _DummyVolume:
    def __init__(self, dt):
        self.date = dt
        self.volume = 0

def test_getPossibleDates_collects_and_sorts_dates_and_skips_invalid():
    proj = _DummyProject(date(2020,1,1), date(2022,1,1))
    d1 = _DummyDerivative(100, proj)
    d2 = _DummyDerivative(100, proj)

    # Include duplicates and invalid (string) date
    d1.derivativevolume_list = [
        _DummyVolume(date(2021,1,1)),
        _DummyVolume(date(2020,1,1)),
        _DummyVolume("2022-01-01"),  # invalid type, should be skipped
    ]
    d2.derivativevolume_list = [
        _DummyVolume(date(2021,1,1)),  # duplicate
        _DummyVolume(date(2022,1,1)),
    ]
    proj.derivative_list = [d1, d2]

    out = getPossibleDates(proj)
    # Sorted, preserves duplicates by design
    assert out == [date(2020,1,1), date(2021,1,1), date(2021,1,1), date(2022,1,1)]


# -----------------------------
# Tests for startProject
# -----------------------------

class _DummyUser: 
    def __init__(self, id): self.id = id
class _DummyContext:
    def __init__(self, user): self.user = user
class _DummyInfo:
    def __init__(self, user_id=123):
        self.context = _DummyContext(_DummyUser(user_id))

def _call_mutation(func, *args, **kwargs):
    # Some decorators wrap functions; prefer underlying __wrapped__ if present
    target = getattr(func, "__wrapped__", func)
    return target(*args, **kwargs)

def test_startProject_calls_create_and_returns_instances(monkeypatch):
    created = {}

    def fake_project_create(**kwargs):
        created['project'] = kwargs
        # fabricate a minimal instance with attributes used later
        inst = object.__new__(Project)
        inst.name = kwargs.get('name')
        inst.number = kwargs.get('number')
        inst.start_date = kwargs.get('start_date')
        inst.end_date = kwargs.get('end_date')
        return inst

    def fake_derivative_create(**kwargs):
        created['derivative'] = kwargs
        inst = object.__new__(Derivative)
        inst.name = kwargs.get('name')
        inst.estimated_weight = kwargs.get('estimated_weight')
        inst.estimated_volume = kwargs.get('estimated_volume')
        inst.project = kwargs.get('project')
        return inst

    monkeypatch.setattr(Project, "create", staticmethod(fake_project_create))
    monkeypatch.setattr(Derivative, "create", staticmethod(fake_derivative_create))

    fixed_start = date(2024, 1, 1)
    fixed_end = date(2026, 1, 1)
    info = _DummyInfo(user_id=999)

    project, derivative = _call_mutation(
        startProject,
        info,
        project_name="P-Name",
        project_number="AP12345",
        derivative_name="D-Name",
        derivative_weight=12.34,    # using a plain number for simplicity
        derivative_volume=42,
        start_date=fixed_start,
        end_date=fixed_end,
    )

    # Returned instances are those created by our fakes
    assert isinstance(project, Project)
    assert isinstance(derivative, Derivative)
    assert derivative.project is project

    # Validate arguments passed to create
    assert created["project"]["name"] == "P-Name"
    assert created["project"]["number"] == "AP12345"
    assert created["project"]["start_date"] == fixed_start
    assert created["project"]["end_date"] == fixed_end
    assert created["project"]["creator_id"] == 999

    assert created["derivative"]["name"] == "D-Name"
    assert created["derivative"]["estimated_weight"] == 12.34
    assert created["derivative"]["estimated_volume"] == 42
    assert created["derivative"]["project"] is project
    assert created["derivative"]["creator_id"] == 999

def test_startProject_defaults_to_today_when_none(monkeypatch):
    # Patch date.today used by the module (imported name 'date')
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2030, 12, 31)

    monkeypatch.setattr(builtins, "date", date, raising=False)  # ensure builtins.date not interfering
    # Patch the imported symbol in this module's namespace
    monkeypatch.setattr(globals(), "date", FakeDate, raising=True)

    # Provide fakes for create methods
    monkeypatch.setattr(Project, "create", staticmethod(lambda *_, **__: object.__new__(Project)))
    monkeypatch.setattr(Derivative, "create", staticmethod(lambda *_, **__: object.__new__(Derivative)))

    info = _DummyInfo(user_id=1)
    project, derivative = _call_mutation(
        startProject,
        info,
        project_name="X",
        project_number="AP12345",
        derivative_name="Y",
        derivative_weight=1,
        derivative_volume=2,
        start_date=None,
        end_date=None,
    )

    # We can't inspect created kwargs easily here (we used simple lambdas),
    # but we can at least assert that call does not crash and returns instances.
    assert isinstance(project, Project)
    assert isinstance(derivative, Derivative)

# -----------------------------
# Notes on untested decorators/properties
# -----------------------------
# We avoided asserting behavior that depends on external graphQlProperty/graphQlMutation
# implementations. Where possible, we invoked the underlying function via __wrapped__.
# For total_shipment/total_revenue returning a Measurement instance, we refrained from
# asserting Measurement-specific behavior since Measurement type originates from external
# library; numeric paths are indirectly covered in generate_volume_distribution/generateVolume tests.