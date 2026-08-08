FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY src/ src/
COPY scripts/ scripts/

RUN pip install --no-cache-dir -e .

EXPOSE 8000
# Shell form (not exec form) so $PORT expands -- Railway injects a dynamic
# PORT the app must bind to; the ${PORT:-8000} fallback keeps this identical
# to before for local docker-compose, where `api` relies on this default CMD
# and no PORT env var is set (worker/rag-api/etc already override `command:`
# explicitly, so they're unaffected either way).
CMD uvicorn agentsys.main:app --host 0.0.0.0 --port ${PORT:-8000}
