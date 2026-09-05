# Reflow API. Runs as a normal long-lived process, not a serverless
# function: the audit log and idempotency ledger are SQLite files that must
# persist between requests, so the container needs a mounted disk.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Mount a persistent volume here and point DATABASE_URL at it.
RUN mkdir -p /data
ENV DATABASE_URL=sqlite:////data/recovery.db

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
