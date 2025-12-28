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

EXPOSE 8000

# Run with production-grade WSGI server.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--timeout", "600", "server:create_app()"]
