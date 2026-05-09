FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema (Pillow necesita libs de imagen)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev zlib1g-dev libfreetype6-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python
RUN pip install --no-cache-dir \
    openpyxl \
    Pillow \
    xlsxwriter \
    requests \
    beautifulsoup4

# Código de la app
COPY servidor.py /app/
COPY calculadora.html /app/
COPY logo.png /app/

# Datos persistentes en volumen
ENV DB_PATH=/data/pedidos.db
ENV PORT=8001

EXPOSE 8001
CMD ["python", "-u", "servidor.py"]
