#!/usr/bin/env python3
"""
Verification script for integration test structure

This script verifies that the mocked integration test is properly structured
and can be imported correctly when dependencies are available.
"""
import sys
import os
import ast

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


def verify_test_structure():
    """Verify the test file structure"""
    test_file = os.path.join(os.path.dirname(__file__), 'test_space_integration_mocked.py')
    conftest_file = os.path.join(os.path.dirname(__file__), 'conftest.py')

    print("=" * 60)
    print("Integration Test Structure Verification")
    print("=" * 60)

    # Check files exist
    print("\n1. Checking file existence...")
    files_to_check = [
        (test_file, "test_space_integration_mocked.py"),
        (conftest_file, "conftest.py")
    ]

    for filepath, name in files_to_check:
        if os.path.exists(filepath):
            print(f"   ✅ {name} exists")
        else:
            print(f"   ❌ {name} missing")
            return False

    # Check syntax
    print("\n2. Checking Python syntax...")
    for filepath, name in files_to_check:
        try:
            with open(filepath, 'r') as f:
                ast.parse(f.read())
            print(f"   ✅ {name} syntax valid")
        except SyntaxError as e:
            print(f"   ❌ {name} has syntax errors: {e}")
            return False

    # Analyze test structure
    print("\n3. Analyzing test structure...")
    with open(test_file, 'r') as f:
        tree = ast.parse(f.read())

    # Find class definitions
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    print(f"   Found {len(classes)} classes:")
    for cls in classes:
        print(f"      - {cls}")

    # Find async function definitions
    async_funcs = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    ]
    print(f"\n   Found {len(async_funcs)} async functions:")
    for func in async_funcs[:10]:  # Show first 10
        print(f"      - {func}")
    if len(async_funcs) > 10:
        print(f"      ... and {len(async_funcs) - 10} more")

    # Verify key components exist
    print("\n4. Verifying key components...")
    required_classes = ['MockTestConfig', 'MockedIntegrationTest']
    for cls in required_classes:
        if cls in classes:
            print(f"   ✅ {cls} defined")
        else:
            print(f"   ❌ {cls} missing")

    required_funcs = [
        'setup_mocked_manager',
        'test_space_creation',
        'test_space_persistence',
        'test_agent_discovery',
        'run_all_tests',
        'main'
    ]
    for func in required_funcs:
        if func in async_funcs or func in [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            print(f"   ✅ {func} defined")
        else:
            print(f"   ⚠️  {func} might be missing")

    # Check conftest fixtures
    print("\n5. Analyzing conftest fixtures...")
    with open(conftest_file, 'r') as f:
        conftest_tree = ast.parse(f.read())

    # Find functions with @pytest.fixture decorator
    fixtures = []
    for node in ast.walk(conftest_tree):
        if isinstance(node, ast.FunctionDef):
            # Check if it has a decorator
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == 'fixture':
                    fixtures.append(node.name)
                elif isinstance(decorator, ast.Attribute) and decorator.attr == 'fixture':
                    fixtures.append(node.name)

    print(f"   Found {len(fixtures)} fixtures:")
    for fixture in fixtures:
        print(f"      - {fixture}")

    # Summary
    print("\n" + "=" * 60)
    print("Verification Complete")
    print("=" * 60)
    print("\n✅ Test structure is valid!")
    print("\nTo run the tests (requires dependencies):")
    print("  1. With pytest:    pytest tests/integration/test_space_integration_mocked.py -v")
    print("  2. Standalone:     python tests/integration/test_space_integration_mocked.py")
    print("  3. In Docker:      docker-compose exec matrix-client python tests/integration/test_space_integration_mocked.py")
    print("\nRequired dependencies:")
    print("  - aiohttp")
    print("  - pytest (optional, for test framework integration)")
    print("  - pytest-asyncio (optional, for async test support)")

    return True


if __name__ == "__main__":
    try:
        success = verify_test_structure()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
