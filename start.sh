#!/usr/bin/env bash
# Law Scrapper MCP v2.0 — startup script (Linux/macOS)
#
# Usage:
#   ./start.sh              # STDIO transport (default)
#   ./start.sh http         # HTTP transport on port 7683
#   ./start.sh docker       # Docker Compose

set -e

TRANSPORT="${1:-stdio}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: 'uv' is not installed. Install it from https://docs.astral.sh/uv/"
    exit 1
fi

# Install dependencies
echo "Installing dependencies..."
uv sync --quiet

case "$TRANSPORT" in
    stdio)
        echo "Starting Law Scrapper MCP (STDIO transport)..."
        uv run python -m law_scrapper_mcp
        ;;
    http)
        echo "Starting Law Scrapper MCP (HTTP transport on port 7683)..."
        LAW_MCP_TRANSPORT=streamable-http uv run python -m law_scrapper_mcp
        ;;
    docker)
        if ! command -v docker &> /dev/null; then
            echo "Error: 'docker' is not installed."
            exit 1
        fi
        echo "Starting Law Scrapper MCP (Docker)..."
        docker compose up --build
        ;;
    *)
        echo "Usage: $0 [stdio|http|docker]"
        exit 1
        ;;
esac
