FROM python:3.11-slim

WORKDIR /app
COPY tools/platform_mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tools/platform_mcp_server/*.py .
COPY backend/core/artifact_contract.py ./artifact_contract.py

ENV PYTHONUNBUFFERED=1
EXPOSE 8081
CMD ["python", "run.py"]
