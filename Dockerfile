FROM python:3.11-slim

WORKDIR /app

# System deps for chromadb / onnxruntime
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

ENV PARLIAMENT_MEMORY_DIR=/data/memory
ENV PARLIAMENT_AUDIT_DB=/data/audit.db
ENV PARLIAMENT_ENABLE_RED_TEAM=true
ENV PARLIAMENT_ENABLE_SEMANTIC_MEM=true
ENV PARLIAMENT_MAX_ROUNDS=3

VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "parliament_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
