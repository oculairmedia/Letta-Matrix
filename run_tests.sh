#!/bin/bash
# Test runner script for Letta-Matrix integration

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored message
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Print header
print_header() {
    echo ""
    print_message "$BLUE" "=================================================="
    print_message "$BLUE" "$1"
    print_message "$BLUE" "=================================================="
}

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    print_message "$RED" "Error: pytest is not installed"
    print_message "$YELLOW" "Install test dependencies with: pip install -r requirements.txt"
    exit 1
fi

# Default to running all tests
TEST_TYPE="${1:-all}"

case "$TEST_TYPE" in
    smoke)
        print_header "Running Smoke Tests"
        pytest tests/test_smoke.py -v -m smoke
        ;;

    unit)
        print_header "Running Unit Tests"
        pytest tests/unit/ -v -m unit
        ;;

    integration)
        print_header "Running Integration Tests"
        pytest tests/integration/ -v -m integration
        ;;

    coverage)
        print_header "Running Tests with Coverage"
        pytest --cov=. --cov-report=html --cov-report=term-missing --cov-report=xml
        print_message "$GREEN" "Coverage report generated in htmlcov/index.html"
        ;;

    quick)
        print_header "Running Quick Tests (Smoke + Fast Unit)"
        pytest tests/test_smoke.py tests/unit/ -v -m "smoke or unit" -k "not slow"
        ;;

    all)
        print_header "Running All Tests"
        pytest tests/ -v
        ;;

    watch)
        print_header "Running Tests in Watch Mode"
        print_message "$YELLOW" "Note: Install pytest-watch with: pip install pytest-watch"
        ptw tests/ -- -v
        ;;

    clean)
        print_header "Cleaning Test Artifacts"
        rm -rf .pytest_cache
        rm -rf htmlcov
        rm -rf .coverage
        rm -rf coverage.xml
        rm -rf tests/__pycache__
        rm -rf tests/unit/__pycache__
        rm -rf tests/integration/__pycache__
        find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find . -type f -name "*.pyc" -delete 2>/dev/null || true
        print_message "$GREEN" "Test artifacts cleaned"
        ;;

    help)
        echo "Usage: ./run_tests.sh [option]"
        echo ""
        echo "Options:"
        echo "  smoke       - Run smoke tests only (fast)"
        echo "  unit        - Run unit tests only"
        echo "  integration - Run integration tests only"
        echo "  coverage    - Run all tests with coverage report"
        echo "  quick       - Run smoke tests + fast unit tests"
        echo "  all         - Run all tests (default)"
        echo "  watch       - Run tests in watch mode (auto-rerun on changes)"
        echo "  clean       - Clean test artifacts and cache"
        echo "  help        - Show this help message"
        echo ""
        echo "Examples:"
        echo "  ./run_tests.sh smoke"
        echo "  ./run_tests.sh coverage"
        echo "  ./run_tests.sh unit"
        ;;

    *)
        print_message "$RED" "Unknown option: $TEST_TYPE"
        print_message "$YELLOW" "Run './run_tests.sh help' for usage information"
        exit 1
        ;;
esac

# Print summary if tests passed
if [ $? -eq 0 ]; then
    echo ""
    print_message "$GREEN" "✓ Tests completed successfully!"
else
    echo ""
    print_message "$RED" "✗ Tests failed!"
    exit 1
fi
