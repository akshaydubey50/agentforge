#!/bin/bash
# Docker Quick Start Script

echo "======================================"
echo "  RAG Pipeline - Docker Setup"
echo "======================================"
echo ""

# Check Docker
echo "[1] Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker not installed"
    echo "Download from: https://www.docker.com/products/docker-desktop"
    exit 1
fi
echo "✓ Docker found: $(docker --version)"
echo ""

# Check docker-compose
echo "[2] Checking Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    echo "ERROR: Docker Compose not installed"
    exit 1
fi
echo "✓ Docker Compose found: $(docker-compose --version)"
echo ""

# Check .env
echo "[3] Checking .env file..."
if [ ! -f .env ]; then
    echo "ERROR: .env file not found"
    echo "Create .env with: OPENAI_API_KEY=sk-..."
    exit 1
fi
echo "✓ .env file exists"
echo ""

# Build
echo "[4] Building Docker images..."
docker-compose build
echo "✓ Build complete"
echo ""

# Start
echo "[5] Starting services..."
echo "    - Seed service (indexes data)"
echo "    - API service (port 8000)"
echo "    - Dashboard service (port 8501)"
echo ""
docker-compose up

echo ""
echo "======================================"
echo "  Services Started!"
echo "======================================"
echo ""
echo "API:       http://localhost:8000"
echo "Dashboard: http://localhost:8501"
echo ""
echo "To stop: Press Ctrl+C"
echo ""
