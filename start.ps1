# Law Scrapper MCP v2.0 — startup script (Windows PowerShell)
#
# Usage:
#   .\start.ps1              # STDIO transport (default)
#   .\start.ps1 http         # HTTP transport on port 7683
#   .\start.ps1 docker       # Docker Compose

param(
    [ValidateSet("stdio", "http", "docker")]
    [string]$Transport = "stdio"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Check if uv is installed
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "Error: 'uv' is not installed. Install it from https://docs.astral.sh/uv/"
    exit 1
}

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Cyan
uv sync --quiet

switch ($Transport) {
    "stdio" {
        Write-Host "Starting Law Scrapper MCP (STDIO transport)..." -ForegroundColor Green
        uv run python -m law_scrapper_mcp
    }
    "http" {
        Write-Host "Starting Law Scrapper MCP (HTTP transport on port 7683)..." -ForegroundColor Green
        $env:LAW_MCP_TRANSPORT = "streamable-http"
        uv run python -m law_scrapper_mcp
    }
    "docker" {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Write-Error "Error: 'docker' is not installed."
            exit 1
        }
        Write-Host "Starting Law Scrapper MCP (Docker)..." -ForegroundColor Green
        docker compose up --build
    }
}
