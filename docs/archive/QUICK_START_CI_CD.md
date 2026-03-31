# Quick Start: CI/CD & Docker Images

## 🚀 Quick Commands

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
docker-compose -f docker-compose.prod.yml pull

# Start services
docker-compose -f docker-compose.prod.yml up -d

# Use specific version
IMAGE_TAG=v1.0.0 docker-compose -f docker-compose.prod.yml up -d
```

## 📦 Available Images

All images are published to GitHub Container Registry (ghcr.io):

```
ghcr.io/oculairmedia/letta-matrix-client:latest
ghcr.io/oculairmedia/letta-matrix-api:latest
ghcr.io/oculairmedia/letta-matrix-mcp:latest
```

### Image Tags

| Tag | Description | Example |
|-----|-------------|---------|
| `latest` | Latest build from main | `latest` |
| `main-<sha>` | Specific commit | `main-abc123` |
| `develop` | Latest develop branch | `develop` |
| `v*` | Release version | `v1.0.0`, `v1.2.3` |
| `pr-*` | Pull request build | `pr-42` |

## 🏷️ Creating Releases

### Standard Release
```bash
# Create and push tag
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# CI will automatically:
# 1. Run tests
# 2. Build images
# 3. Tag as: v1.0.0, v1.0, v1, latest
# 4. Create GitHub Release
```

### Pre-Release
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

## 🧪 Running Tests

### Locally
```bash
# Install test dependencies
pip install -r test_requirements.txt

# Run unit tests
pytest test_space_management.py -v

# Run with coverage
pytest test_space_management.py --cov=agent_user_manager --cov-report=html

# View coverage report
open htmlcov/index.html
```

### In CI
Tests run automatically on:
- Every push to main/develop
- Every pull request
- Before building Docker images

## 🔍 Security Scans

### Manual Scan
```bash
# Build image
docker build -f Dockerfile.matrix-client -t scan-target .

# Scan with Trivy
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image scan-target:latest
```

### Automated Scans
- Daily at 2 AM UTC
- On Dockerfile or requirements.txt changes
- Results in GitHub Security tab

## 🔧 Common Tasks

### Update Dependencies
```bash
# Update requirements.txt
pip install --upgrade package-name
pip freeze > requirements.txt

# Rebuild images
docker-compose build matrix-client

# Test locally
docker-compose up -d matrix-client
```

### Debug Build Issues
```bash
# Build with verbose output
docker build -f Dockerfile.matrix-client --progress=plain --no-cache .

# Check build context size
docker build -f Dockerfile.matrix-client -t test . 2>&1 | grep "Sending build context"

# Verify .dockerignore is working
tar -czf - . --exclude-from=.dockerignore | tar -tz
```

### View Workflow Status
```bash
# Using GitHub CLI
gh workflow list
gh run list --workflow="Build and Push Docker Images"
gh run view <run-id>

# View logs
gh run view <run-id> --log
```

## 📊 Monitoring

### Check Image Size
```bash
docker images | grep letta-matrix
```

### Health Checks
```bash
# Matrix client (doesn't have HTTP endpoint)
docker exec matrix-client python -c "print('OK')"

# Matrix API
curl http://localhost:8004/health

# MCP Server
curl http://localhost:8016/health
```

### Resource Usage
```bash
docker stats matrix-client matrix-api mcp-server
```

## 🔐 Permissions

### Make Images Public
1. Go to https://github.com/orgs/oculairmedia/packages
2. Find package (letta-matrix-client, etc.)
3. Settings → Change visibility → Public

### GitHub Token Scopes
For pushing images, token needs:
- `write:packages`
- `read:packages`
- `delete:packages` (optional, for cleanup)

## 🐛 Troubleshooting

### Issue: Image Push Fails
**Solution:**
```bash
# Re-login to GHCR
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Verify token has packages:write permission
gh auth status
```

### Issue: Tests Fail in CI
**Solution:**
```bash
# Run tests with same Python version as CI
docker run -v $(pwd):/app -w /app python:3.11-slim bash -c \
  "pip install -r requirements.txt -r test_requirements.txt && pytest"
```

### Issue: Build is Slow
**Solutions:**
1. Check .dockerignore is properly excluding files
2. Use layer caching: `docker build --cache-from ghcr.io/...`
3. Multi-stage builds to reduce image size
4. Reduce number of RUN commands in Dockerfile

### Issue: Security Scan Fails
**Solution:**
```bash
# Update base image
FROM python:3.11-slim  # Use latest patch version

# Update dependencies
pip install --upgrade package-name

# Check for known vulnerabilities
pip install safety
safety check
```

## 📈 Best Practices

1. **Always tag releases** with semantic versions
2. **Test locally** before pushing
3. **Use specific tags** in production (not `latest`)
4. **Keep dependencies updated** (Dependabot)
5. **Monitor security scans** regularly
6. **Document breaking changes** in releases
7. **Use .dockerignore** to reduce build context
8. **Layer Docker builds** efficiently

## 📚 Documentation Links

- Full CI/CD Setup: [CI_CD_SETUP.md](CI_CD_SETUP.md)
- Matrix Bridge Docs: [CLAUDE.md](CLAUDE.md)
- Test Guide: [TEST_README.md](TEST_README.md)
- GitHub Actions: https://docs.github.com/en/actions
- Docker Best Practices: https://docs.docker.com/develop/dev-best-practices/

## 🆘 Getting Help

1. Check workflow logs in GitHub Actions tab
2. Review [CI_CD_SETUP.md](CI_CD_SETUP.md) for detailed docs
3. Search GitHub Issues
4. Ask in project discussions
