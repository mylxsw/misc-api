FROM python:3.11-slim

# Avoid writing .pyc files, force stdout/stderr to flush immediately, and set venv path.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:/root/.local/bin:${PATH}"

WORKDIR /app

# Install uv (Python packaging manager).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy dependency definitions first to leverage Docker layer caching.
COPY pyproject.toml uv.lock* ./

# Create virtual environment and install dependencies with uv.
RUN uv venv /app/.venv && \
    uv sync --no-dev

# Copy the full application source (future files included).
COPY . .
RUN uv sync --no-dev

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# ASGI workers serve Flask REST routes and Streamable HTTP MCP at /mcp.
# Flask still runs in a thread pool (a2wsgi), so slow provider calls do not
# block MCP session handling. Prefer the async image endpoint for long jobs.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--timeout", "600", "asgi:app"]
