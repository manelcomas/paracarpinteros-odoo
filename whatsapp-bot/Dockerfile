FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# Fuentes DejaVu para que product_card.py pueda renderizar tildes/símbolo ₡
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        httpx \
        pydantic \
        python-multipart \
        pywebpush \
        cryptography \
        Pillow
COPY main.py .
COPY product_card.py .
COPY static /app/static
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
