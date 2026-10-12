FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

# Install the package and its dependencies
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e "."

RUN useradd -m -u 1001 voiceos
USER voiceos

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.services.web_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
