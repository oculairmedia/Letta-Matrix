# Tuwunel Image Corruption Issue

## Problem

Both Tuwunel Docker images are currently corrupted on the registries:

```
Error: archive/tar: invalid tar header
```

**Affected images:**
- `ghcr.io/matrix-construct/tuwunel:latest`
- `ghcr.io/matrix-construct/tuwunel:main`
- `jevolk/tuwunel:latest`
- `jevolk/tuwunel:main`

## Workarounds

### Option 1: Wait for Registry Fix

The Tuwunel maintainers need to rebuild and push fresh images. This is likely a temporary registry issue.

- **GitHub Issue**: Report at https://github.com/matrix-construct/tuwunel/issues
- **Community**: Ask in #tuwunel:matrix.org

### Option 2: Build from Source

Build Tuwunel locally from source:

```bash
git clone https://github.com/matrix-construct/tuwunel.git
cd tuwunel
docker build -t local/tuwunel:latest .
```

Then update `docker-compose.tuwunel.yml`:
```yaml
tuwunel:
  image: local/tuwunel:latest
```

### Option 3: Use Synapse (Current Setup)

Continue using the existing Synapse deployment while waiting for Tuwunel images to be fixed.

```bash
git checkout main
docker-compose up -d
```

## Status

- **Date Detected**: November 13, 2025
- **Registries Affected**: Both GHCR and Docker Hub
- **Workaround**: Build from source or wait for fix
- **Branch Status**: `feature/tuwunel-migration` ready but blocked by image issue

## Resolution

Once the images are fixed on the registry, deployment can proceed:

```bash
git checkout feature/tuwunel-migration
./deploy-tuwunel.sh
```
