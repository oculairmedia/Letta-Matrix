#!/bin/bash
set -e

echo "========================================"
echo "Tuwunel Matrix Homeserver Deployment"
echo "========================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if we should stop existing services
if [ "$(docker ps -q -f name=matrix-synapse-deployment)" ]; then
    echo -e "${YELLOW}Existing Matrix services detected.${NC}"
    read -p "Stop and remove existing Synapse deployment? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}Stopping existing services...${NC}"
        docker-compose down -v
        echo -e "${GREEN}✓ Services stopped${NC}"
    fi
fi

# Backup existing data
if [ -d "./matrix_client_data" ] && [ "$(ls -A ./matrix_client_data)" ]; then
    echo -e "${YELLOW}Backing up matrix_client_data...${NC}"
    BACKUP_DIR="matrix_client_data.backup.$(date +%Y%m%d_%H%M%S)"
    cp -r ./matrix_client_data "$BACKUP_DIR"
    echo -e "${GREEN}✓ Backup created: $BACKUP_DIR${NC}"
fi

# Clean up for fresh start
read -p "Clean all existing data for fresh start? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Cleaning old data...${NC}"
    rm -rf ./synapse-data/* 2>/dev/null || true
    rm -rf ./postgres-data/* 2>/dev/null || true
    rm -rf ./matrix_store/* 2>/dev/null || true
    echo '{}' > ./matrix_client_data/agent_user_mappings.json 2>/dev/null || true
    echo '{}' > ./matrix_client_data/letta_space_config.json 2>/dev/null || true
    echo -e "${GREEN}✓ Old data cleaned${NC}"
fi

# Create tuwunel data directory
echo -e "${GREEN}Creating Tuwunel data directory...${NC}"
mkdir -p ./tuwunel-data
chmod 777 ./tuwunel-data  # Tuwunel needs write access
echo -e "${GREEN}✓ Data directory created${NC}"

# Setup environment
if [ ! -f ".env" ] || [ ! -s ".env" ]; then
    echo -e "${GREEN}Setting up environment variables...${NC}"
    cp .env.tuwunel .env
    echo -e "${GREEN}✓ Environment configured${NC}"
else
    echo -e "${YELLOW}⚠ .env file exists, keeping current configuration${NC}"
    echo -e "${YELLOW}  If you want to use Tuwunel config, run: cp .env.tuwunel .env${NC}"
fi

# Pull images
echo -e "${GREEN}Pulling Docker images...${NC}"
docker-compose -f docker-compose.tuwunel.yml pull

# Build custom images
echo -e "${GREEN}Building custom integration images...${NC}"
docker-compose -f docker-compose.tuwunel.yml build

# Start services
echo -e "${GREEN}Starting Tuwunel stack...${NC}"
docker-compose -f docker-compose.tuwunel.yml up -d

# Wait for Tuwunel to start
echo -e "${GREEN}Waiting for Tuwunel to start...${NC}"
sleep 5

# Check health
echo ""
echo -e "${GREEN}Checking service health...${NC}"
docker-compose -f docker-compose.tuwunel.yml ps

echo ""
echo "========================================"
echo -e "${GREEN}Deployment Complete!${NC}"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Access Element at http://matrix.oculair.ca"
echo "2. Register admin user (first registration = admin)"
echo "3. Check Tuwunel logs: docker-compose -f docker-compose.tuwunel.yml logs -f tuwunel"
echo "4. Check agent sync: docker-compose -f docker-compose.tuwunel.yml logs -f matrix-client"
echo ""
echo "Tuwunel endpoints:"
echo "  - Client API: http://localhost:6167"
echo "  - Element Web: http://localhost:8008"
echo "  - Matrix API: http://localhost:8004"
echo "  - MCP Server: http://localhost:8016"
echo "  - Letta Agent MCP: http://localhost:8017"
echo ""
echo "View logs: docker-compose -f docker-compose.tuwunel.yml logs -f"
echo "Stop services: docker-compose -f docker-compose.tuwunel.yml down"
echo ""
