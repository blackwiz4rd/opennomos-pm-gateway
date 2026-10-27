FROM python:3.12-slim

ENV PM_GATEWAY_HOST=0.0.0.0 \
    PM_GATEWAY_PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir . \
    && adduser --disabled-password --gecos "" --uid 10001 gateway

USER gateway

EXPOSE 8080

CMD ["pm-gateway"]
