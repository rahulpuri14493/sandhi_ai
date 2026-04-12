FROM python:3.11-slim

WORKDIR /app
COPY tools/platform_mcp_server/requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt
COPY tools/platform_mcp_server/*.py .
COPY backend/core/artifact_contract.py ./artifact_contract.py

ENV PYTHONUNBUFFERED=1
EXPOSE 8081
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
