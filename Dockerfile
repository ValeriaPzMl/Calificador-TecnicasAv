# Imagen para publicar el Calificador (Render). Dos etapas:
#   1. compila la web de React (web/dist) con Node;
#   2. Python con la API, que sirve esa web y la API en el mismo dominio.
# Local: podman build -t calificador . && podman run --env-file .env -p 8000:8000 calificador

FROM docker.io/library/node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM docker.io/library/python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# En el servidor no hay pantalla: OpenCV "headless" (sin ventanas ni libGL)
COPY requirements.txt .
RUN sed 's/^opencv-contrib-python\b/opencv-contrib-python-headless/' requirements.txt \
        | grep -v '^pytest' > requirements-server.txt \
    && pip install -r requirements-server.txt
COPY api/ api/
COPY cli/ cli/
COPY core/ core/
COPY models/ models/
COPY storage/ storage/
COPY --from=web /web/dist web/dist
RUN useradd --create-home calificador && mkdir -p data/processed && chown -R calificador data
USER calificador
EXPOSE 8000
# Un solo proceso: los escaneos en vivo viven en memoria. Render pone $PORT
# y el HTTPS; --proxy-headers para que la app sepa que es https.
CMD ["sh", "-c", "exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
