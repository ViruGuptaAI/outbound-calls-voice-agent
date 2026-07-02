# ── Outbound Calling Agents — production image ──────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/server

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt hypercorn>=0.16

# App code
COPY server/ ./server/

# Seed the local SQLite CRM database into the image
WORKDIR /app/server
RUN python seed_db.py

EXPOSE 8000

# Hypercorn ASGI server — WebSocket-capable, single worker (in-memory session state)
CMD ["hypercorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "1"]
