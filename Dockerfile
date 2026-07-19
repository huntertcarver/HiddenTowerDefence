FROM node:22-alpine AS frontend

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
COPY --from=frontend /src/app/static/game ./app/static/game
COPY fixtures ./fixtures
USER appuser
ENV PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
