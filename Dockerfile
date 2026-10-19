# TradeCore MVP — Dockerfile
# Requirements: RNF-02

# Misma imagen base que CI (tests.yml): Python exacto 3.11.16.
FROM python:3.11.16-slim

WORKDIR /app

# Install dependencies first for layer caching.
# Imagen reproducible: instala EXACTAMENTE las versiones congeladas que valida CI (requirements-ci.txt).
COPY requirements-ci.txt .
RUN pip install --no-cache-dir -r requirements-ci.txt

# Copy application source and dashboard
COPY src/ ./src/
COPY dashboard/ ./dashboard/

EXPOSE 8000

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
