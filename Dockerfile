FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
COPY fixtures ./fixtures
USER appuser
ENV PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
