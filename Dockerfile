FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scheduler/ ./scheduler/
COPY scripts/ ./scripts/
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser /app
USER appuser
CMD ["python", "-m", "scheduler.main"]
