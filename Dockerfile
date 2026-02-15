FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock* ./
COPY src/ src/

# Install dependencies
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim

# Create non-root user
RUN groupadd -r lawmcp && useradd -r -g lawmcp -d /app -s /sbin/nologin lawmcp

WORKDIR /app

# Copy installed packages and app code
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Set environment
ENV PATH="/app/.venv/bin:$PATH"
ENV LAW_MCP_TRANSPORT=streamable-http
ENV LAW_MCP_HOST=0.0.0.0
ENV LAW_MCP_PORT=7683

# Switch to non-root user
USER lawmcp

EXPOSE 7683

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:7683/health', timeout=5)" || exit 1

ENTRYPOINT ["python", "-m", "law_scrapper_mcp"]
