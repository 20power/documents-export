FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY threshold_service_fastapi.py ./               # original service
COPY service_ext.py ./                              # extension router (/health, /meta)
COPY qr_multihead_outputs ./qr_multihead_outputs    # models + meta.json (mounted as volume in prod)
COPY config_qr.yaml ./config_qr.yaml
COPY requirements-service.txt ./requirements-service.txt
RUN pip install --no-cache-dir -r requirements-service.txt
ENV META_PATH=./qr_multihead_outputs/meta.json
EXPOSE 8000
# Run the extended app so extra routes are available
CMD ["uvicorn", "service_ext:app", "--host", "0.0.0.0", "--port", "8000"]
