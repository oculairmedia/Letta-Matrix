# Contributing to Letta-Matrix

## Overview

Letta-Matrix is a Matrix integration for Letta AI agents, enabling multi-agent communication via the Matrix protocol. We welcome contributions that improve the codebase, fix bugs, add features, or enhance documentation.

## Table of Contents

- [Getting Started](#getting-started)
- [Code of Conduct](#code-of-conduct)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Code Review Guidelines](#code-review-guidelines)
- [Issue Reporting](#issue-reporting)
- [Testing Requirements](#testing-requirements)
- [Coding Standards](#coding-standards)

## Getting Started

Before contributing, please:

1. **Read the architecture documentation**: Review `/docs/architecture/OVERVIEW.md` to understand the system design
2. **Set up your development environment**: Follow `/docs/process/DEVELOPMENT.md`
3. **Run the test suite**: Ensure all tests pass (`pytest tests/`)
4. **Review existing issues**: Check if your contribution addresses an existing issue

## Code of Conduct

- Be respectful and constructive in all communications
- Focus on the technical merit of contributions
- Welcome newcomers and help them learn
- Assume good intentions from all contributors

## How to Contribute

### Types of Contributions

1. **Bug Fixes**: Fix identified bugs with test coverage
2. **Feature Development**: Add new features after discussion
3. **Documentation**: Improve or add documentation
4. **Testing**: Add test coverage or improve existing tests
5. **Refactoring**: Improve code quality and organization
6. **Performance**: Optimize existing functionality

### Before You Start

For significant changes:

1. **Open an issue first** to discuss the proposed change
2. **Get feedback** from maintainers before implementing
3. **Ensure alignment** with project goals and architecture

For minor changes (docs, small fixes):

1. Open a PR directly with a clear description

## Pull Request Process

### 1. Branch Naming

Use descriptive branch names:

```bash
# Feature branches
git checkout -b feature/add-message-queuing

# Bug fix branches
git checkout -b fix/duplicate-message-handling

# Refactoring branches
git checkout -b refactor/extract-space-manager

# Documentation branches
git checkout -b docs/update-contribution-guide
```

### 2. Commit Messages

Follow conventional commit format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `refactor`: Code restructuring without behavior change
- `test`: Adding or updating tests
- `docs`: Documentation changes
- `chore`: Maintenance tasks
- `perf`: Performance improvements

**Examples**:
```bash
feat(matrix): Add message queuing for reliability

Implements asyncio.Queue to buffer incoming messages and prevent
message loss during high load conditions.

Closes #123

---

fix(agent): Prevent duplicate message processing

Adds event deduplication check at the start of message_callback
to prevent processing the same Matrix event_id twice.

Fixes #456

---

refactor: Extract MatrixSpaceManager from AgentUserManager (Sprint 2)

Reduces AgentUserManager from 1,346 lines to 1,121 lines by
extracting space-related methods into dedicated class.

Part of refactoring plan Sprint 2.
```

### 3. Before Submitting

Ensure your PR:

- [ ] Includes tests for new functionality
- [ ] Passes all existing tests (`pytest tests/`)
- [ ] Updates relevant documentation
- [ ] Follows the coding standards
- [ ] Has a clear, descriptive commit message
- [ ] References related issues (if applicable)

### 4. Creating the Pull Request

```bash
# Push your branch
git push -u origin your-branch-name

# Create PR with descriptive title and body
```

**PR Template**:

```markdown
## Summary
Brief description of what this PR does

## Changes
- Bullet point list of changes
- Include both code and test changes

## Test Plan
- [ ] Tested manually
- [ ] Added unit tests
- [ ] Added integration tests
- [ ] All tests pass locally

## Related Issues
Closes #XXX
Relates to #YYY

## Screenshots (if applicable)
Include screenshots for UI changes

## Checklist
- [ ] Code follows project style guidelines
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
```

### 5. PR Size Guidelines

- **Small PRs** (preferred): < 400 lines changed
- **Medium PRs**: 400-1000 lines changed
- **Large PRs**: > 1000 lines (require extra justification)

For large changes, consider:
- Breaking into multiple PRs
- Creating a tracking issue with checkboxes
- Implementing in phases

## Code Review Guidelines

### For Contributors

When your PR is under review:

1. **Respond promptly** to reviewer feedback
2. **Ask questions** if feedback is unclear
3. **Make requested changes** or explain why not
4. **Be patient** - reviews may take time
5. **Update tests** when changing code

### For Reviewers

When reviewing PRs:

1. **Be constructive and kind** in feedback
2. **Focus on** code quality, correctness, tests, and documentation
3. **Ask questions** rather than making demands
4. **Approve** when satisfied or request changes clearly
5. **Test locally** for complex changes

### Review Checklist

- [ ] Code follows project architecture (see `/docs/architecture/`)
- [ ] Tests cover new functionality
- [ ] All tests pass
- [ ] Documentation is updated
- [ ] No security vulnerabilities introduced
- [ ] Performance impact is acceptable
- [ ] Breaking changes are documented
- [ ] Code is readable and maintainable

### Review Outcomes

- **Approve**: Ready to merge
- **Request Changes**: Issues that must be addressed
- **Comment**: Suggestions or questions (no blocking)

## Issue Reporting

### Bug Reports

When reporting a bug, include:

```markdown
## Bug Description
Clear description of the bug

## Steps to Reproduce
1. Step 1
2. Step 2
3. Step 3

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Environment
- OS: Linux/macOS/Windows
- Docker version: X.Y.Z
- Matrix Synapse version: X.Y.Z
- Letta version: X.Y.Z

## Logs
```
Relevant log output
```

## Additional Context
Screenshots, configuration, etc.
```

### Feature Requests

When requesting a feature:

```markdown
## Feature Description
Clear description of the feature

## Use Case
Why is this feature needed?

## Proposed Implementation
Suggested approach (optional)

## Alternatives Considered
Other approaches you've thought about

## Additional Context
Mockups, examples, references
```

### Questions

For questions:

1. Check existing documentation first
2. Search closed issues
3. Ask in discussions (if available)
4. Create issue with `question` label

## Testing Requirements

All contributions must include appropriate tests:

### Unit Tests

- **Required** for all new functions/methods
- **Location**: `tests/unit/`
- **Run**: `pytest tests/unit/`

Example:
```python
def test_generate_username():
    """Test username generation from agent ID"""
    manager = MatrixUserManager(...)
    username = manager.generate_username("TestAgent", "agent-123-456")
    assert username == "agent_123_456"
```

### Integration Tests

- **Required** for features involving multiple components
- **Location**: `tests/integration/`
- **Run**: `pytest tests/integration/`

### Smoke Tests

- **Required** for critical user workflows
- **Location**: `tests/integration/smoke/`
- **Run**: `pytest tests/integration/smoke/`

### Test Coverage

- Maintain **100% pass rate** on all tests
- Aim for **>80% code coverage** on new code
- Include both happy path and error cases

### Running Tests

```bash
# All tests
pytest tests/

# Specific test file
pytest tests/unit/test_agent_user_manager.py

# With coverage
pytest tests/ --cov=src --cov-report=html

# Verbose output
pytest tests/ -v

# Stop on first failure
pytest tests/ -x
```

## Coding Standards

### Python Style

- **PEP 8** compliance (enforced by tooling)
- **Type hints** for function parameters and returns
- **Docstrings** for public functions/classes
- **Async/await** for I/O operations

Example:
```python
async def create_matrix_user(
    self,
    username: str,
    password: str,
    display_name: str
) -> bool:
    """
    Create a Matrix user account.

    Args:
        username: Matrix username (without @, domain)
        password: User password
        display_name: Human-readable display name

    Returns:
        True if user created successfully, False otherwise

    Raises:
        MatrixAPIError: If API request fails
    """
    # Implementation
```

### Project Structure

Follow the established structure:

```
src/
├── core/          # Core business logic
├── matrix/        # Matrix client code
├── letta/         # Letta API integration
├── mcp/           # MCP server implementations
├── api/           # FastAPI endpoints
├── utils/         # Shared utilities
└── models/        # Data models
```

See `/docs/architecture/OVERVIEW.md` for detailed architecture.

### Error Handling

- **Use try/except** for expected errors
- **Log errors** with appropriate levels
- **Raise exceptions** with clear messages
- **Clean up resources** in finally blocks

Example:
```python
try:
    await client.room_send(room_id, message)
    logger.info(f"Sent message to {room_id}")
except MatrixAPIError as e:
    logger.error(f"Failed to send message: {e}")
    raise
finally:
    await client.close()
```

### Logging

- **Use structured logging** with context
- **Appropriate levels**: DEBUG, INFO, WARNING, ERROR
- **Include context**: room IDs, user IDs, agent IDs

Example:
```python
logger.info(
    "Processing message",
    extra={
        "room_id": room_id,
        "agent_id": agent_id,
        "event_id": event_id
    }
)
```

### Configuration

- **Environment variables** for secrets and deployment-specific config
- **Config classes** for application settings
- **Type validation** using Pydantic or dataclasses
- **Document** all configuration options

### Dependencies

- **Minimal dependencies**: Only add when necessary
- **Pin versions** in `requirements.txt`
- **Update carefully**: Test thoroughly after updates
- **Document** why each dependency is needed

## Development Workflow

### Typical Workflow

1. Create feature branch from `main`
2. Implement changes with tests
3. Run full test suite locally
4. Commit with conventional commit message
5. Push and create PR
6. Address review feedback
7. Merge after approval

### Sprint-Based Development

For major refactoring (see `/docs/process/DEVELOPMENT.md`):

1. Create sprint branch: `sprint-X-description`
2. Implement sprint goals
3. Ensure 100% test pass rate
4. Create PR with detailed summary
5. Merge to main after review

### Hotfix Workflow

For urgent production fixes:

1. Create hotfix branch from `main`
2. Implement minimal fix
3. Add test to prevent regression
4. Fast-track review
5. Deploy immediately after merge

## Additional Resources

- **Architecture**: `/docs/architecture/OVERVIEW.md`
- **Development Setup**: `/docs/process/DEVELOPMENT.md`
- **Testing Guide**: `/docs/operations/TESTING.md`
- **Best Practices**: `/docs/process/BEST_PRACTICES.md`
- **Changelog**: `/docs/process/CHANGELOG.md`
- **Troubleshooting**: `/docs/operations/TROUBLESHOOTING.md`

## Questions?

If you have questions about contributing:

1. Check the documentation in `/docs/`
2. Search existing issues
3. Create an issue with the `question` label
4. Tag relevant maintainers if urgent

## Thank You!

Thank you for contributing to Letta-Matrix! Your contributions help make multi-agent AI communication better for everyone.
