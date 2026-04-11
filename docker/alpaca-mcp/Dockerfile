FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir alpaca-mcp-server
EXPOSE 8000
CMD ["alpaca-mcp-server", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
