# TradeCore MVP — Dockerfile
# Requirements: RNF-02

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and dashboard
COPY src/ ./src/
COPY dashboard/ ./dashboard/

EXPOSE 8000

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
