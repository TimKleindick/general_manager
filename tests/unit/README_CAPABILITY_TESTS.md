# Capability Infrastructure Test Suite

## Overview

This directory contains comprehensive unit tests for the capability-based architecture refactoring introduced in the `refactor_interfaces` branch. The tests cover all new capability infrastructure components including registry, configuration, manifest system, factory pattern, and core utilities.

## Test Files

| File | Lines | Purpose |
|------|-------|---------|
| `test_capability_registry.py` | 220 | Tests capability tracking and binding |
| `test_capability_configuration.py` | 242 | Tests declarative capability composition |
| `test_capability_exceptions.py` | 58 | Tests error handling and exceptions |
| `test_capability_factory.py` | 169 | Tests capability instantiation |
| `test_capability_manifest.py` | 245 | Tests manifest resolution and inheritance |
| `test_capability_models.py` | 227 | Tests data models (Plan, Config, Selection) |
| `test_capability_core_utils.py` | 263 | Tests observability wrapper utilities |
| `test_capability_builder.py` | 103 | Tests manifest-driven builder (enhanced) |
| `test_orm_capabilities_comprehensive.py` | 484 | Tests ORM capability implementations |

**Total**: 2,011 lines of test code covering 140+ test scenarios

## Quick Start

### Run All Capability Tests
```bash
pytest tests/unit/test_capability*.py tests/unit/test_orm_capabilities*.py -v
```

### Run with Coverage
```bash
pytest tests/unit/test_capability*.py tests/unit/test_orm_capabilities*.py \
    --cov=src/general_manager/interface/capabilities \
    --cov=src/general_manager/interface/manifests \
    --cov-report=html \
    --cov-report=term-missing
```

### Run Individual Test Suite
```bash
pytest tests/unit/test_capability_registry.py -v
```

## Test Coverage by Component

### 1. Capability Registry (`registry.py`)
- ✅ Registration (single, multiple, incremental)
- ✅ Instance binding and retrieval
- ✅ Snapshot functionality
- ✅ Multi-interface isolation
- ✅ Replace vs. merge semantics

### 2. Capability Configuration (`configuration.py`)
- ✅ InterfaceCapabilityConfig instantiation
- ✅ CapabilitySet bundling
- ✅ Flattening nested structures
- ✅ Lazy iteration
- ✅ Immutability guarantees

### 3. Capability Factory (`factory.py`)
- ✅ Built-in capability instantiation
- ✅ Override mechanisms (class & callable)
- ✅ Error handling for unknown capabilities
- ✅ Order preservation
- ✅ CAPABILITY_CLASS_MAP validation

### 4. Capability Manifest (`manifests/`)
- ✅ Plan resolution
- ✅ MRO inheritance aggregation
- ✅ Required vs. optional capabilities
- ✅ Flag-based toggling
- ✅ Interface-specific plans
- ✅ CapabilitySelection computation

### 5. Core Utilities (`core/utils.py`)
- ✅ Observability wrapper (with_observability)
- ✅ Hook lifecycle (before/after/error)
- ✅ Payload isolation
- ✅ Exception propagation
- ✅ Graceful degradation

### 6. ORM Capabilities (`capabilities/orm/`)
- ✅ Persistence support
- ✅ Read operations (current & historical)
- ✅ History lookups
- ✅ Query operations (filter/exclude/all)
- ✅ Validation and normalization
- ✅ Mutation operations
- ✅ Lifecycle hooks
- ✅ Soft delete

## Testing Patterns Used

### Happy Path
Every public API tested with valid inputs and expected outputs.

### Edge Cases
- Empty collections
- None/null values
- Single-item collections
- Boundary conditions

### Error Conditions
- Invalid inputs
- Missing dependencies
- Type mismatches
- Unknown identifiers

### State Management
- Initialization
- Transitions
- Immutability enforcement
- Caching behavior

### Isolation
- No cross-test contamination
- Independent test execution
- Mock-based dependency injection

## Code Quality Standards

All tests follow these standards:

1. **Descriptive Names**: Test names clearly describe what is being tested
2. **AAA Pattern**: Arrange-Act-Assert structure
3. **Single Responsibility**: Each test validates one behavior
4. **No External Dependencies**: Pure unit tests, no database required
5. **Fast Execution**: All tests run in seconds
6. **Deterministic**: No flaky tests, reproducible results

## Examples

### Testing Registry Behavior
```python
def test_registry_tracks_multiple_interfaces():
    """Test that registry properly tracks capabilities for multiple interfaces."""
    registry = CapabilityRegistry()
    registry.register(DatabaseInterface, ["read", "write", "query"])
    registry.register(CalculationInterface, ["read", "query"])
    
    assert registry.get(DatabaseInterface) == frozenset(["read", "write", "query"])
    assert registry.get(CalculationInterface) == frozenset(["read", "query"])
```

### Testing Observability Hooks
```python
def test_with_observability_before_operation():
    """Test that before_operation is called when present."""
    capability = Mock()
    capability.before_operation = Mock()
    
    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)
    
    def func():
        return "result"
    
    with_observability(target, operation="create", payload={"test": "data"}, func=func)
    
    capability.before_operation.assert_called_once()
```

### Testing Manifest Resolution
```python
def test_manifest_resolve_inheritance():
    """Test resolving with inheritance aggregation."""
    base_plan = CapabilityPlan(required=frozenset(["read"]))
    derived_plan = CapabilityPlan(required=frozenset(["write"]))
    
    manifest = CapabilityManifest(plans={
        BaseInterface: base_plan,
        DerivedInterface: derived_plan
    })
    
    resolved = manifest.resolve(DerivedInterface)
    
    # Should merge capabilities from both classes
    assert "read" in resolved.required
    assert "write" in resolved.required
```

## Continuous Integration

These tests are designed to run in CI/CD pipelines:

```yaml
# Example CI configuration
test:
  script:
    - pytest tests/unit/test_capability*.py tests/unit/test_orm_capabilities*.py --cov --cov-report=xml
  coverage: '/TOTAL.*\s+(\d+%)$/'
```

## Maintenance

### Adding New Tests
1. Follow the naming convention: `test_<component>_<behavior>.py`
2. Use descriptive test function names
3. Include docstrings explaining the test purpose
4. Follow the AAA pattern
5. Use appropriate mocks for dependencies

### Running Tests Locally
```bash
# Install test dependencies
pip install pytest pytest-cov pytest-django

# Run tests
pytest tests/unit/test_capability*.py -v

# Generate coverage report
pytest tests/unit/test_capability*.py --cov --cov-report=html
open htmlcov/index.html
```

## Related Documentation

- [Architecture Decision Records](../../docs/adr/)
- [Capability-First Interfaces Guide](../../docs/concepts/interfaces/capability-first.md)
- [Custom Capability How-To](../../docs/howto/create_custom_capability.md)
- [Custom Interface Type How-To](../../docs/howto/create_custom_interface_type.md)

## Support

For questions or issues with these tests:
1. Check existing test patterns for examples
2. Review the ADRs for architectural context
3. Consult the main project documentation
4. Open an issue on GitHub

---

**Last Updated**: November 2024  
**Test Framework**: pytest  
**Python Version**: 3.12+  
**Django Version**: 5.2+