# CI/CD Guide

Continuous Integration and Deployment workflows for the Letta-Matrix integration.

## Quick Start

### Development (Build Locally)

```bash
# Start all services and build images
docker-compose up -d --build

# Rebuild specific service
docker-compose up -d --build matrix-client

# View logs
docker-compose logs -f matrix-client
```

### Production (Use Pre-Built Images)

```bash
# Login to GitHub Container Registry
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Pull latest images
docker-compose pull

# Start services
docker-compose up -d

# Use specific version
IMAGE_TAG=v1.0.0 docker-compose up -d
```

## Available Images

All images are published to GitHub Container Registry (ghcr.io):

```
ghcr.io/oculairmedia/letta-matrix-client:latest
ghcr.io/oculairmedia/letta-matrix-api:latest
ghcr.io/oculairmedia/letta-matrix-mcp:latest
```

### Image Tags

| Tag | Description | Example | Use Case |
|-----|-------------|---------|----------|
| `latest` | Latest build from main | `latest` | Production stable |
| `main-<sha>` | Specific commit from main | `main-abc123` | Rollback/pinning |
| `develop` | Latest develop branch | `develop` | Staging/testing |
| `v*` | Semantic version release | `v1.0.0`, `v1.2.3` | Production releases |
| `pr-*` | Pull request build | `pr-42` | PR testing |

## Workflows

### 1. Docker Build Workflow

**File**: `.github/workflows/docker-build.yml`

**Triggers**:
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- New version tags (`v*`)
- Manual workflow dispatch

**What It Does**:
1. **Tests** - Runs pytest unit tests
2. **Builds** - Creates multi-platform Docker images
3. **Publishes** - Pushes to GitHub Container Registry

**Components Built**:
- `matrix-client` - Main Matrix bot client
- `matrix-api` - FastAPI service
- `mcp-server` - MCP HTTP/WebSocket server

**Features**:
- Multi-platform builds (linux/amd64, linux/arm64)
- Docker layer caching for faster builds
- Automatic tagging based on git ref
- Build summary in GitHub Actions UI

**Example Run**:
```bash
# Triggered by: git push origin main
# Workflow:
#   1. Checkout code
#   2. Set up Python 3.11
#   3. Run pytest tests
#   4. Build matrix-client image
#   5. Tag: ghcr.io/oculairmedia/letta-matrix-client:latest
#   6. Tag: ghcr.io/oculairmedia/letta-matrix-client:main-abc123
#   7. Push to registry
#   8. Repeat for matrix-api and mcp-server
```

### 2. Security Scan Workflow

**File**: `.github/workflows/docker-security-scan.yml`

**Triggers**:
- Daily at 2 AM UTC (scheduled)
- Pull requests modifying Dockerfiles or requirements.txt
- Manual workflow dispatch

**What It Does**:
1. Builds each Docker image
2. Scans for vulnerabilities using Trivy
3. Uploads results to GitHub Security tab
4. Fails on CRITICAL or HIGH severity issues
5. Reviews dependencies for known vulnerabilities

**Security Checks**:
- CVE scanning for OS packages
- Python dependency vulnerability scanning
- License compliance checking
- Dependency review on PRs

**Example Output**:
```
Running Trivy scan on letta-matrix-client...
✓ No CRITICAL vulnerabilities found
✓ No HIGH vulnerabilities found
⚠ 3 MEDIUM vulnerabilities found
ℹ 12 LOW vulnerabilities found
```

### 3. Lint and Code Quality Workflow

**File**: `.github/workflows/lint.yml`

**Triggers**:
- Push to main or develop
- Pull requests

**What It Does**:

**Python Linting**:
- Black (code formatting)
- isort (import sorting)
- Flake8 (style guide enforcement)
- MyPy (type checking)

**Docker Linting**:
- Hadolint (Dockerfile best practices)

**YAML Linting**:
- yamllint (workflow validation)

### 4. Release Workflow

**File**: `.github/workflows/release.yml`

**Triggers**:
- Push of version tags (`v*`)

**What It Does**:
1. Creates GitHub Release with changelog
2. Triggers Docker image build with version tag
3. Marks pre-releases (alpha, beta, rc)
4. Generates release notes automatically

**Example**:
```bash
# Create release
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# Workflow creates:
# - GitHub Release: v1.0.0
# - Docker tags: v1.0.0, v1.0, v1, latest
# - Release notes from commits
```

## Creating Releases

### Semantic Versioning

Use semantic versioning for all releases:
- **Major** (v1.0.0): Breaking changes
- **Minor** (v1.1.0): New features, backward compatible
- **Patch** (v1.1.1): Bug fixes

### Standard Release

```bash
# Create and push a version tag
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# GitHub Actions will automatically:
# 1. Run tests
# 2. Build images for linux/amd64 and linux/arm64
# 3. Push images with tags: v1.0.0, v1.0, v1, latest
# 4. Create GitHub Release with changelog
# 5. Generate release notes
```

### Pre-Release Versions

```bash
# Alpha release
git tag -a v1.0.0-alpha.1 -m "Alpha release 1"
git push origin v1.0.0-alpha.1

# Beta release
git tag -a v1.0.0-beta.1 -m "Beta release 1"
git push origin v1.0.0-beta.1

# Release Candidate
git tag -a v1.0.0-rc.1 -m "Release candidate 1"
git push origin v1.0.0-rc.1

# Pre-releases are marked as "Pre-release" in GitHub
# Not tagged as "latest" in Docker registry
```

## Using Pre-Built Images

### Pull from GitHub Container Registry

```bash
# Login (requires GitHub personal access token)
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Pull specific images
docker pull ghcr.io/oculairmedia/letta-matrix-client:latest
docker pull ghcr.io/oculairmedia/letta-matrix-api:latest
docker pull ghcr.io/oculairmedia/letta-matrix-mcp:latest

# Pull specific version
docker pull ghcr.io/oculairmedia/letta-matrix-client:v1.0.0

# Pull development version
docker pull ghcr.io/oculairmedia/letta-matrix-client:develop
```

### Using with Docker Compose

**Production Deployment**:
```bash
# Images are specified in docker-compose.yml
# services:
#   matrix-client:
#     image: ghcr.io/oculairmedia/letta-matrix-client:latest

# Pull images
docker-compose pull

# Start services
docker-compose up -d

# Use specific version
IMAGE_TAG=v1.0.0 docker-compose up -d
```

**Development (build locally)**:
```bash
# Build from source
docker-compose build

# Build and start
docker-compose up -d --build
```

## Image Naming Convention

```
ghcr.io/OWNER/letta-matrix-COMPONENT:TAG
```

**Examples**:
- `ghcr.io/oculairmedia/letta-matrix-client:latest`
- `ghcr.io/oculairmedia/letta-matrix-api:v1.2.3`
- `ghcr.io/oculairmedia/letta-matrix-mcp:main-abc123`
- `ghcr.io/oculairmedia/letta-matrix-client:develop`

## GitHub Secrets Configuration

### Required Secrets

**GITHUB_TOKEN** (automatic):
- Used for: Pushing to GHCR, creating releases
- No setup needed - provided automatically by GitHub Actions
- Permissions: `packages:write`, `contents:write`

**CODECOV_TOKEN** (optional):
- Used for: Uploading code coverage reports
- Get from: https://codecov.io
- Add in: Repository Settings → Secrets and variables → Actions

### Making Images Public

By default, GHCR images are private. To make them public:

1. Go to https://github.com/orgs/oculairmedia/packages
2. Find your package (e.g., letta-matrix-client)
3. Click "Package settings"
4. Scroll to "Danger Zone"
5. Click "Change visibility" → "Public"
6. Confirm the change

## Build Optimization

### Docker Layer Caching

The workflows use GitHub Actions cache for Docker layers:
- Speeds up subsequent builds by 60-80%
- Shares cache between jobs
- Automatic cache invalidation on changes
- Max cache size: 10GB

**Configuration**:
```yaml
- name: Set up Docker Buildx
  uses: docker/setup-buildx-action@v2

- name: Cache Docker layers
  uses: actions/cache@v3
  with:
    path: /tmp/.buildx-cache
    key: ${{ runner.os }}-buildx-${{ github.sha }}
    restore-keys: |
      ${{ runner.os }}-buildx-
```

### Multi-Stage Builds

Optimize Dockerfiles with multi-stage builds:

```dockerfile
# Build stage
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.11-slim
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
CMD ["python", "app.py"]
```

**Benefits**:
- Smaller final images (50-70% reduction)
- Faster deployment
- Better security (no build tools in production)

### Build Context Optimization

Use `.dockerignore` to exclude unnecessary files:

```
# .dockerignore
.git
.github
docs/
tests/
*.md
__pycache__
*.pyc
.pytest_cache
htmlcov/
.coverage
*.log
```

**Impact**: Reduces build context from ~100MB to ~10MB

## Monitoring Builds

### GitHub Actions Dashboard

```bash
# Using GitHub CLI
gh workflow list
gh run list --workflow="Build and Push Docker Images"
gh run view <run-id>
gh run view <run-id> --log

# View latest run
gh run view --workflow="Build and Push Docker Images"
```

### Build Status Badges

Add to README.md:

```markdown
![Build Status](https://github.com/oculairmedia/Letta-Matrix/workflows/Build%20and%20Push%20Docker%20Images/badge.svg)
![Security Scan](https://github.com/oculairmedia/Letta-Matrix/workflows/Docker%20Security%20Scan/badge.svg)
![Tests](https://github.com/oculairmedia/Letta-Matrix/workflows/Tests/badge.svg)
```

### Notifications

Configure in:
- **Repository Settings** → Notifications
- **Personal Settings** → Notifications
- **GitHub Mobile App** for real-time alerts

Notification options:
- Email on workflow failure
- Slack integration
- Discord webhooks
- Custom webhooks

## Troubleshooting

### Build Fails on Dependency Installation

**Problem**: `pip install` fails with version conflicts

**Solutions**:
```bash
# 1. Check requirements.txt for conflicts
pip-compile --resolver=backtracking requirements.in

# 2. Update base image
FROM python:3.11-slim  # Use latest patch version

# 3. Add system dependencies to Dockerfile
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
```

### Image Push Fails

**Problem**: Permission denied when pushing to GHCR

**Solutions**:
```bash
# 1. Verify GITHUB_TOKEN permissions
# Settings → Actions → General → Workflow permissions
# Select: "Read and write permissions"

# 2. Check package visibility
# If package exists and is private, make it public first

# 3. Re-authenticate
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

### Tests Fail in CI but Pass Locally

**Problem**: Tests pass on local machine but fail in GitHub Actions

**Solutions**:
```bash
# 1. Test with same Python version as CI
docker run -v $(pwd):/app -w /app python:3.11-slim bash -c \
  "pip install -r requirements.txt && pytest"

# 2. Check environment variables
# CI may have different defaults than local

# 3. Review test isolation
# Tests may depend on local filesystem state

# 4. Check timezone/locale differences
# Use UTC in tests or configure CI timezone
```

### Multi-Platform Build Slow

**Problem**: ARM64 build takes 10+ minutes

**Solutions**:
```bash
# 1. Enable QEMU caching
docker run --privileged --rm tonistiigi/binfmt --install all

# 2. Skip ARM64 for development builds
docker buildx build --platform linux/amd64 -t test .

# 3. Use native ARM64 runner (GitHub-hosted or self-hosted)
# .github/workflows/docker-build.yml:
runs-on: [ubuntu-latest, ARM64]
```

### Security Scan Fails

**Problem**: Trivy finds CRITICAL or HIGH vulnerabilities

**Solutions**:
```bash
# 1. Update base image
FROM python:3.11-slim  # Latest patch includes security fixes

# 2. Update vulnerable dependencies
pip install --upgrade vulnerable-package

# 3. Check for known vulnerabilities
pip install safety
safety check

# 4. Review CVE details
# GitHub Security tab → View details
# Some CVEs may not apply to your use case
```

## Advanced Configuration

### Custom Runners

For faster builds, use self-hosted runners:

```yaml
# .github/workflows/docker-build.yml
jobs:
  build:
    runs-on: self-hosted
    # Ensure runner has Docker and buildx installed
```

**Benefits**:
- 3-5x faster builds
- No concurrent job limits
- Custom hardware (more RAM, CPU)
- Persistent cache

### Matrix Builds

Build multiple Python versions:

```yaml
jobs:
  test:
    strategy:
      matrix:
        python-version: ['3.9', '3.10', '3.11', '3.12']
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pytest
```

### Conditional Steps

Skip steps based on conditions:

```yaml
- name: Push to production
  if: github.ref == 'refs/heads/main'
  run: docker push production-image

- name: Push to staging
  if: github.ref == 'refs/heads/develop'
  run: docker push staging-image

- name: Comment on PR
  if: github.event_name == 'pull_request'
  uses: actions/github-script@v6
  with:
    script: |
      github.rest.issues.createComment({
        issue_number: context.issue.number,
        owner: context.repo.owner,
        repo: context.repo.repo,
        body: '✅ Docker images built successfully!'
      })
```

## Best Practices

### CI/CD Workflow
1. **Always run tests before building images**
2. **Use semantic versioning for releases**
3. **Tag production images with specific versions, not `latest`**
4. **Monitor security scans daily**
5. **Keep dependencies updated** (use Dependabot)

### Image Management
1. **Keep Dockerfiles optimized** (multi-stage, layer caching)
2. **Use .dockerignore** to reduce build context
3. **Pin base image versions** for reproducibility
4. **Scan images before deploying** to production
5. **Clean up old images** regularly

### Release Process
1. **Test thoroughly** before creating release tags
2. **Document breaking changes** in release notes
3. **Use pre-releases** (alpha, beta, rc) for testing
4. **Maintain changelog** for all releases
5. **Communicate** major changes to users

### Security
1. **Never commit secrets** to repository
2. **Use GitHub Secrets** for credentials
3. **Scan dependencies** for vulnerabilities
4. **Keep base images updated** with security patches
5. **Review and rotate** access tokens regularly

## Monitoring and Metrics

### Key Metrics

Track these metrics for CI/CD health:

**Build Performance**:
- Average build time
- Build success rate
- Cache hit rate
- Build frequency

**Deployment Performance**:
- Time to production
- Deployment frequency
- Rollback rate
- Mean time to recovery (MTTR)

**Quality Metrics**:
- Test pass rate
- Code coverage
- Security scan results
- Number of critical vulnerabilities

### Dashboard Example

```bash
# Last 10 workflow runs
gh run list --limit 10

# Success rate
gh run list --limit 100 --json conclusion \
  --jq '[.[] | select(.conclusion == "success")] | length'

# Average duration
gh run list --limit 50 --json durationMs \
  --jq '[.[].durationMs] | add / length / 1000 / 60'
```

## Resources

### Documentation
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker Build Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Semantic Versioning](https://semver.org/)
- [Trivy Security Scanner](https://github.com/aquasecurity/trivy)

### Related Documentation
- **Deployment Guide**: docs/operations/DEPLOYMENT.md
- **Testing Guide**: docs/operations/TESTING.md
- **Troubleshooting**: docs/operations/TROUBLESHOOTING.md

---

**Last Updated**: 2025-01-17
**Version**: 1.0
**Maintainers**: OculairMedia Development Team
