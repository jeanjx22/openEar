#!/usr/bin/env bash
# openEar deploy script
#
# Usage: ./scripts/deploy.sh
#
# This script:
# 1. Tags the current commit SHA for rollback
# 2. Pulls the latest code
# 3. Rebuilds and restarts the Docker container
# 4. Verifies startup via log inspection
# 5. Prints rollback instructions if verification fails

set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
LOG_WAIT_SECONDS=15
SUCCESS_PATTERN="openEar is running"

echo "=== openEar Deploy ==="
echo "$(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Step 1: Tag current state for rollback
PREV_SHA=$(git rev-parse HEAD)
echo "Previous SHA: ${PREV_SHA}"
echo ""

# Step 2: Pull latest code
echo "Pulling latest code..."
git pull
NEW_SHA=$(git rev-parse HEAD)
echo "New SHA: ${NEW_SHA}"

if [ "${PREV_SHA}" = "${NEW_SHA}" ]; then
    echo "No new changes. Rebuilding anyway..."
fi
echo ""

# Step 3: Rebuild and restart
echo "Building and starting container..."
docker compose -f "${COMPOSE_FILE}" up -d --build
echo ""

# Step 4: Wait and verify startup
echo "Waiting ${LOG_WAIT_SECONDS}s for startup..."
sleep "${LOG_WAIT_SECONDS}"

echo "Checking logs..."
LOGS=$(docker compose -f "${COMPOSE_FILE}" logs --tail=30 2>&1)
echo "${LOGS}"
echo ""

if echo "${LOGS}" | grep -q "${SUCCESS_PATTERN}"; then
    echo "=== Deploy successful! ==="
else
    echo "=== WARNING: Success pattern not found in logs ==="
    echo ""
    echo "The container may still be starting. Check with:"
    echo "  docker compose logs -f --tail=50"
    echo ""
    echo "To rollback:"
    echo "  git checkout ${PREV_SHA}"
    echo "  docker compose up -d --build"
fi
