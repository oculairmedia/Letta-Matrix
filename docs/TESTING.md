# Letta-Matrix Integration - Testing Guide

## Overview

This document provides comprehensive information about the test suite for the Letta-Matrix integration project. The test suite is designed to ensure reliability, catch regressions early, and provide confidence for refactoring.

## Table of Contents

- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Test Categories](#test-categories)
- [Writing Tests](#writing-tests)
- [Coverage Reports](#coverage-reports)
- [CI/CD Integration](#cicd-integration)
- [Troubleshooting](#troubleshooting)

## Test Structure

```
tests/
├── __init__.py                           # Test package initialization
├── conftest.py                           # Shared fixtures and configuration
├── test_smoke.py                         # Quick smoke tests
├── unit/                                 # Unit tests
│   ├── __init__.py
│   ├── test_agent_user_manager.py       # AgentUserManager tests
│   ├── test_custom_matrix_client.py     # Matrix client tests
│   └── test_matrix_api.py               # FastAPI endpoint tests
└── integration/                          # Integration tests
    ├── __init__.py
    └── test_multi_agent_workflow.py     # End-to-end workflow tests
```

## Running Tests

### Quick Start

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
./run_tests.sh

# Or use pytest directly
pytest
```

### Test Runner Options

The `run_tests.sh` script provides several options:

```bash
# Smoke tests only (fastest - ~5 seconds)
./run_tests.sh smoke

# Unit tests only (~30 seconds)
./run_tests.sh unit

# Integration tests only (~2 minutes)
./run_tests.sh integration

# Quick tests (smoke + fast unit tests)
./run_tests.sh quick

# All tests with coverage report
./run_tests.sh coverage

# Watch mode (auto-rerun on file changes)
./run_tests.sh watch

# Clean test artifacts
./run_tests.sh clean
```

### Using Pytest Directly

```bash
# Run specific test file
pytest tests/unit/test_agent_user_manager.py

# Run specific test class
pytest tests/unit/test_agent_user_manager.py::TestAgentUserMapping

# Run specific test function
pytest tests/unit/test_agent_user_manager.py::TestAgentUserMapping::test_mapping_creation

# Run tests matching pattern
pytest -k "agent_creation"

# Run tests with specific markers
pytest -m unit
pytest -m "unit and not slow"
pytest -m "integration"

# Verbose output
pytest -v

# Show print statements
pytest -s

# Stop on first failure
pytest -x

# Run last failed tests
pytest --lf

# Parallel execution (requires pytest-xdist)
pytest -n auto
```

## Test Categories

### Smoke Tests (`@pytest.mark.smoke`)

**Purpose**: Quick validation that basic functionality works
**Duration**: ~5 seconds
**When to run**: Before every commit

Examples:
- Module imports work
- Dataclasses can be instantiated
- Basic configuration loads
- No syntax errors

```bash
./run_tests.sh smoke
```

### Unit Tests (`@pytest.mark.unit`)

**Purpose**: Test individual functions/methods in isolation
**Duration**: ~30 seconds
**When to run**: During development, before pull requests

Coverage areas:
- AgentUserManager methods
- Matrix client operations
- FastAPI endpoints
- Configuration handling
- Error handling

```bash
./run_tests.sh unit
```

### Integration Tests (`@pytest.mark.integration`)

**Purpose**: Test component interactions
**Duration**: ~2 minutes
**When to run**: Before merging, in CI/CD

Coverage areas:
- Agent discovery → user creation → room setup
- Multi-agent message routing
- Agent name updates
- Room persistence
- Concurrent operations

```bash
./run_tests.sh integration
```

### Other Markers

- `@pytest.mark.slow` - Tests that take >10 seconds
- `@pytest.mark.requires_matrix` - Requires running Matrix homeserver
- `@pytest.mark.requires_letta` - Requires running Letta instance

## Writing Tests

### Test Structure

Follow the Arrange-Act-Assert pattern:

```python
@pytest.mark.unit
class TestFeature:
    """Test description"""

    @pytest.mark.asyncio
    async def test_specific_behavior(self, mock_config):
        """Test a specific behavior"""
        # Arrange - Set up test data
        manager = AgentUserManager(mock_config)
        agent = {"id": "test-001", "name": "Test Agent"}

        # Act - Perform the operation
        result = await manager.process_agent(agent)

        # Assert - Verify the outcome
        assert result.success is True
        assert "test-001" in manager.mappings
```

### Using Fixtures

Fixtures are defined in `tests/conftest.py`:

```python
def test_with_fixtures(mock_config, mock_aiohttp_session, tmp_path):
    """Test using shared fixtures"""
    # mock_config - Mock configuration object
    # mock_aiohttp_session - Mock HTTP session
    # tmp_path - Temporary directory for file operations
    pass
```

### Mocking

Use mocks to isolate tests from external dependencies:

```python
from unittest.mock import Mock, AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mocks(mock_aiohttp_session):
    """Test with mocked HTTP responses"""
    # Mock response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"success": True})

    # Use in test
    mock_aiohttp_session.get = Mock(return_value=mock_response)
```

### Async Tests

Use `@pytest.mark.asyncio` for async functions:

```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test asynchronous operation"""
    result = await some_async_function()
    assert result is not None
```

### Parametrized Tests

Test multiple scenarios with one test function:

```python
@pytest.mark.parametrize("agent_id,expected_username", [
    ("agent-001", "@agent_001:matrix.test"),
    ("agent-abc-123", "@agent_abc_123:matrix.test"),
    ("agent_special", "@agent_special:matrix.test")
])
def test_username_generation(agent_id, expected_username):
    """Test username generation for various agent IDs"""
    result = generate_username(agent_id)
    assert result == expected_username
```

## Coverage Reports

### Generating Coverage

```bash
# HTML report (best for viewing)
./run_tests.sh coverage
open htmlcov/index.html

# Terminal report
pytest --cov=. --cov-report=term-missing

# XML report (for CI/CD)
pytest --cov=. --cov-report=xml
```

### Coverage Goals

- **Overall**: Target 80%+ coverage
- **Critical paths**: 95%+ coverage
  - Agent user creation
  - Message routing
  - Authentication
  - Room management
- **Acceptable lower coverage**:
  - Error handling branches
  - Logging statements
  - Type checking code

### Viewing Coverage

After running `./run_tests.sh coverage`, open `htmlcov/index.html` in a browser:

```bash
# macOS
open htmlcov/index.html

# Linux
xdg-open htmlcov/index.html

# Windows
start htmlcov/index.html
```

The HTML report shows:
- Overall coverage percentage
- File-by-file coverage
- Line-by-line coverage highlighting
- Uncovered lines

## CI/CD Integration

### GitHub Actions

Create `.github/workflows/tests.yml`:

```yaml
name: Tests

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run smoke tests
      run: ./run_tests.sh smoke

    - name: Run unit tests
      run: ./run_tests.sh unit

    - name: Run integration tests
      run: ./run_tests.sh integration

    - name: Generate coverage report
      run: pytest --cov=. --cov-report=xml --cov-report=html

    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        fail_ci_if_error: false

    - name: Upload coverage HTML
      uses: actions/upload-artifact@v3
      with:
        name: coverage-report
        path: htmlcov/
```

### GitLab CI

Create `.gitlab-ci.yml`:

```yaml
image: python:3.11

stages:
  - test
  - coverage

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/pip

before_script:
  - pip install -r requirements.txt

smoke_tests:
  stage: test
  script:
    - ./run_tests.sh smoke
  only:
    - branches

unit_tests:
  stage: test
  script:
    - ./run_tests.sh unit
  only:
    - branches

integration_tests:
  stage: test
  script:
    - ./run_tests.sh integration
  only:
    - branches

coverage:
  stage: coverage
  script:
    - ./run_tests.sh coverage
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
    paths:
      - htmlcov/
  only:
    - main
    - develop
```

### Pre-commit Hooks

Install pre-commit hook to run smoke tests:

```bash
# Create .git/hooks/pre-commit
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
echo "Running smoke tests..."
./run_tests.sh smoke

if [ $? -ne 0 ]; then
    echo "Smoke tests failed. Commit aborted."
    exit 1
fi
EOF

chmod +x .git/hooks/pre-commit
```

## Troubleshooting

### Common Issues

#### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'agent_user_manager'`

**Solution**: Ensure you're running tests from the project root:
```bash
cd /home/user/Letta-Matrix
pytest
```

#### Async Test Errors

**Problem**: `RuntimeWarning: coroutine was never awaited`

**Solution**: Add `@pytest.mark.asyncio` decorator:
```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_operation()
```

#### Fixture Not Found

**Problem**: `fixture 'mock_config' not found`

**Solution**: Fixtures are in `tests/conftest.py`. Ensure test structure is correct:
```
tests/
├── conftest.py      # ← Fixtures defined here
└── unit/
    └── test_file.py  # ← Tests using fixtures
```

#### Coverage Not Generated

**Problem**: Coverage report is empty or shows 0%

**Solution**: Ensure pytest-cov is installed and run from project root:
```bash
pip install pytest-cov
cd /home/user/Letta-Matrix
pytest --cov=.
```

#### Tests Hang or Timeout

**Problem**: Tests never complete

**Solution**:
1. Check for infinite loops in async code
2. Use timeout decorator:
```python
@pytest.mark.timeout(10)  # 10 second timeout
def test_something():
    pass
```

### Debug Mode

Run tests with debugging:

```bash
# Show print statements
pytest -s

# Verbose output
pytest -v

# Show local variables on failure
pytest -l

# Drop into debugger on failure
pytest --pdb

# Combined
pytest -vsl --pdb
```

### Test Isolation

If tests interfere with each other:

```bash
# Run tests in random order (requires pytest-randomly)
pip install pytest-randomly
pytest

# Run each test in separate process (slower but isolated)
pip install pytest-forked
pytest --forked
```

## Best Practices

### DO

✓ Write tests before refactoring
✓ Use descriptive test names
✓ Test one thing per test
✓ Use fixtures for common setup
✓ Mock external dependencies
✓ Run smoke tests frequently
✓ Aim for >80% coverage
✓ Keep tests fast (<100ms for unit tests)
✓ Use markers to categorize tests
✓ Document complex test scenarios

### DON'T

✗ Test implementation details
✗ Write flaky tests
✗ Skip writing tests for "simple" code
✗ Commit failing tests
✗ Make tests depend on each other
✗ Use sleep() instead of proper async handling
✗ Ignore test warnings
✗ Test external services directly
✗ Hardcode file paths or URLs
✗ Let coverage drop below 70%

## Continuous Improvement

### Adding New Tests

When adding new functionality:

1. Write smoke test first
2. Add unit tests for new functions
3. Add integration test for workflows
4. Update coverage to maintain >80%
5. Document any new fixtures

### Refactoring with Tests

When refactoring:

1. Run full test suite before starting
2. Keep tests passing (green) during refactor
3. Add tests for edge cases discovered
4. Update tests if behavior changes
5. Run coverage to ensure no regression

### Test Maintenance

Regular maintenance tasks:

- Remove obsolete tests
- Update mocks when APIs change
- Consolidate duplicate test code
- Speed up slow tests
- Fix flaky tests immediately
- Update documentation

## Resources

### Documentation

- [Pytest Documentation](https://docs.pytest.org/)
- [Pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [Pytest-cov](https://pytest-cov.readthedocs.io/)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)

### Related Files

- `pytest.ini` - Pytest configuration
- `tests/conftest.py` - Shared fixtures
- `requirements.txt` - Test dependencies
- `run_tests.sh` - Test runner script

### Getting Help

If you encounter issues:

1. Check this documentation
2. Review test examples in `tests/`
3. Check pytest documentation
4. Ask the team for help

---

**Last Updated**: 2025-01-14
**Maintained By**: Development Team
