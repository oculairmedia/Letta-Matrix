# CI/CD Setup Documentation

## Overview

This project uses GitHub Actions for continuous integration and deployment. The CI/CD pipeline automatically builds, tests, and publishes Docker images for all Letta-Matrix components.

## Workflows

### 1. Docker Build (`docker-build.yml`)

**Triggers:**
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- New version tags (`v*`)
- Manual workflow dispatch

**What it does:**
1. **Tests** - Runs unit tests with pytest
2. **Builds** - Creates Docker images for:
   - `matrix-client` - Main Matrix bot client
   - `matrix-api` - FastAPI service
   - `mcp-server` - MCP HTTP server
3. **Publishes** - Pushes images to GitHub Container Registry (ghcr.io)

**Image Tags:**
- `latest` - Latest build from main branch
- `main-<sha>` - Specific commit from main
- `develop` - Latest build from develop branch
- `v1.2.3` - Semantic version tags
- `pr-123` - Pull request builds

**Features:**
- Multi-platform builds (linux/amd64, linux/arm64)
- Docker layer caching for faster builds
- Automatic tagging based on git ref
- Build summary in GitHub Actions UI

### 2. Security Scan (`docker-security-scan.yml`)

**Triggers:**
- Daily at 2 AM UTC (scheduled)
- Pull requests modifying Dockerfiles or requirements.txt
- Manual workflow dispatch

**What it does:**
1. Builds each Docker image
2. Scans for vulnerabilities using Trivy
3. Uploads results to GitHub Security tab
4. Fails on CRITICAL or HIGH severity issues
5. Reviews dependencies for known vulnerabilities

**Security Checks:**
- CVE scanning for OS packages
- Python dependency vulnerability scanning
- License compliance checking
- Dependency review on PRs

### 3. Lint and Code Quality (`lint.yml`)

**Triggers:**
- Push to main or develop
- Pull requests

**What it does:**
1. **Python Linting:**
   - Black (code formatting)
   - isort (import sorting)
   - Flake8 (style guide)
   - MyPy (type checking)

2. **Docker Linting:**
   - Hadolint (Dockerfile best practices)

3. **YAML Linting:**
   - yamllint (workflow validation)

### 4. Release (`release.yml`)

**Triggers:**
- Push of version tags (`v*`)

**What it does:**
1. Creates GitHub Release with changelog
2. Triggers Docker image build with version tag
3. Marks pre-releases (alpha, beta, rc)
4. Generates release notes automatically

## Using Pre-Built Images

### Pull from GitHub Container Registry

```bash
# Login to GHCR (requires GitHub personal access token)
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Pull specific image
docker pull ghcr.io/oculairmedia/letta-matrix-client:latest
docker pull ghcr.io/oculairmedia/letta-matrix-api:latest
docker pull ghcr.io/oculairmedia/letta-matrix-mcp:latest

# Pull specific version
docker pull ghcr.io/oculairmedia/letta-matrix-client:v1.0.0
```

### Using with Docker Compose

**Production Deployment:**
```bash
# Copy production compose file
cp docker-compose.prod.yml docker-compose.yml

# Set image tag (optional, defaults to 'latest')
export IMAGE_TAG=v1.0.0
export GITHUB_REPOSITORY_OWNER=oculairmedia

# Pull images
docker-compose pull

# Start services
docker-compose up -d
```

**Development (build locally):**
```bash
# Use original docker-compose.yml
docker-compose up -d --build
```

## Image Naming Convention

```
ghcr.io/OWNER/letta-matrix-COMPONENT:TAG
```

**Examples:**
- `ghcr.io/oculairmedia/letta-matrix-client:latest`
- `ghcr.io/oculairmedia/letta-matrix-api:v1.2.3`
- `ghcr.io/oculairmedia/letta-matrix-mcp:main-abc123`

## Setting Up Secrets

### Required GitHub Secrets

1. **GITHUB_TOKEN** (automatic)
   - Used for: Pushing to GHCR, creating releases
   - No setup needed - provided automatically

2. **CODECOV_TOKEN** (optional)
   - Used for: Uploading code coverage reports
   - Get from: https://codecov.io
   - Add in: Repository Settings → Secrets and variables → Actions

### Making Images Public

By default, images are private. To make them public:

1. Go to https://github.com/orgs/oculairmedia/packages
2. Find your package (e.g., letta-matrix-client)
3. Click "Package settings"
4. Scroll to "Danger Zone"
5. Click "Change visibility" → "Public"

## Versioning Strategy

### Semantic Versioning

Use semantic versioning for releases:
- **Major** (v1.0.0): Breaking changes
- **Minor** (v1.1.0): New features, backward compatible
- **Patch** (v1.1.1): Bug fixes

### Creating a Release

```bash
# Create and push a version tag
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# GitHub Actions will:
# 1. Run tests
# 2. Build images
# 3. Push images with tags: v1.0.0, v1.0, v1, latest
# 4. Create GitHub Release
```

### Pre-release Versions

```bash
# Alpha
git tag v1.0.0-alpha.1
git push origin v1.0.0-alpha.1

# Beta
git tag v1.0.0-beta.1
git push origin v1.0.0-beta.1

# Release Candidate
git tag v1.0.0-rc.1
git push origin v1.0.0-rc.1
```

## Build Optimization

### Docker Layer Caching

The workflows use GitHub Actions cache for Docker layers:
- Speeds up subsequent builds
- Shares cache between jobs
- Automatic cache invalidation

### Multi-Stage Builds

Consider adding multi-stage builds to Dockerfiles:
```dockerfile
# Build stage
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# Runtime stage
FROM python:3.11-slim
COPY --from=builder /root/.local /root/.local
COPY . .
CMD ["python", "app.py"]
```

### Build Context Optimization

Use `.dockerignore` to exclude unnecessary files:
- Git history
- Documentation
- Test files
- Virtual environments

## Monitoring Builds

### GitHub Actions Dashboard

1. Go to repository → Actions tab
2. View workflow runs
3. Click on a run to see details
4. Download artifacts and logs

### Build Status Badges

Add to README.md:
```markdown
![Build Status](https://github.com/oculairmedia/Letta-Matrix/workflows/Build%20and%20Push%20Docker%20Images/badge.svg)
![Security Scan](https://github.com/oculairmedia/Letta-Matrix/workflows/Docker%20Security%20Scan/badge.svg)
```

### Notifications

Configure notifications in:
- Repository Settings → Notifications
- Personal Settings → Notifications
- GitHub Mobile App

## Troubleshooting

### Build Fails on Dependency Installation

**Problem:** `pip install` fails
**Solution:**
1. Check requirements.txt for version conflicts
2. Update base image version
3. Add system dependencies to Dockerfile

### Image Push Fails

**Problem:** Permission denied when pushing to GHCR
**Solution:**
1. Check GITHUB_TOKEN has `packages:write` permission
2. Verify repository settings allow Actions
3. Check if package already exists and is private

### Tests Fail in CI but Pass Locally

**Problem:** Tests pass locally but fail in GitHub Actions
**Solution:**
1. Check Python version consistency
2. Verify environment variables
3. Check for filesystem path differences
4. Review test isolation (temp files, databases)

### Multi-Platform Build Slow

**Problem:** ARM64 build takes too long
**Solution:**
1. Use Docker buildx with QEMU caching
2. Consider native ARM64 runners (GitHub-hosted or self-hosted)
3. Skip ARM64 for development builds

## Advanced Configuration

### Custom Runners

For faster builds, use self-hosted runners:

```yaml
jobs:
  build:
    runs-on: self-hosted
    # ... rest of job
```

### Matrix Builds

Build multiple Python versions:

```yaml
jobs:
  test:
    strategy:
      matrix:
        python-version: ['3.9', '3.10', '3.11']
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
```

### Conditional Steps

Skip steps based on conditions:

```yaml
- name: Push to production
  if: github.ref == 'refs/heads/main'
  run: docker push production-image
```

## Best Practices

1. **Always run tests before building images**
2. **Use semantic versioning for releases**
3. **Keep Dockerfiles optimized** (layer caching, multi-stage)
4. **Monitor security scans** regularly
5. **Document breaking changes** in releases
6. **Use tagged images in production** (not `latest`)
7. **Test images before deploying** to production
8. **Keep dependencies updated** (Dependabot)

## Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker Build Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Semantic Versioning](https://semver.org/)
- [Trivy Security Scanner](https://github.com/aquasecurity/trivy)
