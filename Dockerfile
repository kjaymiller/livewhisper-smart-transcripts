FROM python:3.13-slim

WORKDIR /app

# Install only essential system deps
RUN apt-get update && apt-get install -y \
  libpq5 \
  build-essential \
  curl \
  git \
  && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy app code
COPY app/ /app/app/
COPY pyproject.toml ./
COPY README.md ./

# We removed uv.lock, so let's just create a new one or don't use --frozen
RUN uv lock && uv sync

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# Expose port
EXPOSE 8000

# Start server
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
