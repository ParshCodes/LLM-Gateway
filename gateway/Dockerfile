FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the gateway package so `import gateway` resolves correctly
COPY gateway/ ./gateway/

EXPOSE 8000

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
