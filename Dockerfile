FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scheduler/ ./scheduler/
COPY scripts/ ./scripts/
COPY docker/entrypoint.sh ./entrypoint.sh
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser /app \
    && chmod +x /app/entrypoint.sh
USER appuser
ENTRYPOINT ["/app/entrypoint.sh"]
