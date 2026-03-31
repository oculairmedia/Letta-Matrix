# Tuwunel OCI Image Compatibility Issue - RESOLVED

## Problem

Tuwunel images are built using Nix's `buildLayeredImage` which produces **OCI format images**. These images are incompatible with Docker 20.10.x:

```
Error: archive/tar: invalid tar header
unsupported manifest media type: application/vnd.oci.image.manifest.v1+json
```

**Root Cause:**
- Tuwunel uses OCI image format (not Docker format)
- Docker 20.10.24 has incomplete OCI support
- Requires Docker 23+ or Podman for full OCI compatibility
- Docker upgrade to 23+ blocked by LXC/runc incompatibility

**Current Docker Version:**
```
Docker version: 20.10.24+dfsg1
containerd: 1.6.20~ds1
```

## ✅ IMPLEMENTED SOLUTION: Custom Docker Build

We've implemented a custom Docker build pipeline that produces standard Docker images compatible with Docker 20.10.24.

### Implementation Details

**Repository**: https://github.com/oculairmedia/Letta-Matrix  
**Branch**: `feature/tuwunel-migration`  
**Custom Image**: `ghcr.io/oculairmedia/tuwunel-docker2010:latest`

**Files Created:**
1. `Dockerfile.tuwunel-docker2010` - Builds from Tuwunel source
2. `.github/workflows/docker-build-tuwunel.yml` - Automated CI/CD
3. Updated `docker-compose.tuwunel.yml` - Uses custom image

**Build Process:**
- GitHub Actions automatically builds on push to main/feature branches
- Uses official Rust 1.83 compiler
- Compiles Tuwunel from source
- Produces standard Docker image format
- Pushes to GHCR (GitHub Container Registry)

**Deployment:**
```bash
# Wait for build to complete
gh run watch --repo oculairmedia/Letta-Matrix

# Pull custom image
docker pull ghcr.io/oculairmedia/tuwunel-docker2010:latest

# Deploy
docker-compose -f docker-compose.tuwunel.yml up -d
```

### Build Status

Check current build: https://github.com/oculairmedia/Letta-Matrix/actions/workflows/docker-build-tuwunel.yml

## Alternative Solutions (Not Implemented)

### Option 1: Upgrade Docker (Recommended)

Upgrade to Docker 23+ for full OCI support:

```bash
# Check available versions
apt-cache policy docker-ce

# Upgrade Docker (if using Docker CE)
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io

# Or use Docker's install script
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

### Option 2: Use Podman

Podman has better OCI support:

```bash
sudo apt install podman
podman pull jevolk/tuwunel:latest
podman save jevolk/tuwunel:latest | docker load
```

### Option 3: Build from Source

Build Tuwunel as a standard Docker image:

```bash
git clone https://github.com/matrix-construct/tuwunel.git
cd tuwunel
docker build -f docker/Dockerfile -t local/tuwunel:latest .
```

Then update `docker-compose.tuwunel.yml`:
```yaml
tuwunel:
  image: local/tuwunel:latest
```

### Option 4: Stay on Synapse

Continue using Synapse until Docker is upgraded:

```bash
git checkout main
docker-compose up -d
```

## Technical Details

Tuwunel's OCI images are built using Nix:
- **Format**: OCI Image (not Docker Image)
- **Builder**: `buildLayeredImage` 
- **Manifest**: `application/vnd.oci.image.manifest.v1+json`
- **Compatibility**: Docker 23+, Podman, containerd 1.7+

From Tuwunel docs:
> "The OCI images are OS-less with only a very minimal environment of the tini init system, CA certificates, and the tuwunel binary."

## Status

- **Date Identified**: November 13, 2025
- **Issue**: Docker 20.10.x OCI incompatibility
- **Not a Registry Issue**: Images are correct, format incompatible
- **Branch Status**: `feature/tuwunel-migration` ready, requires Docker upgrade

## Resolution Path

**Immediate**: Use Option 3 (build from source) or Option 4 (stay on Synapse)  
**Long-term**: Upgrade Docker to 23+ for native OCI support

Once Docker is upgraded or built from source:
```bash
git checkout feature/tuwunel-migration
./deploy-tuwunel.sh
```

## References

- [Tuwunel OCI Image Docs](https://matrix-construct.github.io/tuwunel/deploying/docker.html#nix-build)
- [OCI Image Spec](https://github.com/opencontainers/image-spec)
- [Docker OCI Support](https://docs.docker.com/engine/release-notes/)
