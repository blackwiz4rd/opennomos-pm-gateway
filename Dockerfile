FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PM_GATEWAY_HOST=0.0.0.0 \
    PM_GATEWAY_PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir '.[server]' \
    && python -m pip uninstall -y pip setuptools wheel \
    && adduser --disabled-password --gecos "" --uid 10001 gateway

USER gateway

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.getenv('PM_GATEWAY_PORT', '8080'), timeout=2)"

CMD ["pm-gateway"]
