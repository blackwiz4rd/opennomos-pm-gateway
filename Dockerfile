FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ARG PM_GATEWAY_VERSION=0.0.0

ENV PM_GATEWAY_HOST=0.0.0.0 \
    PM_GATEWAY_PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=${PM_GATEWAY_VERSION}

WORKDIR /app

# Patch base-image packages in place so the pinned base digest does not keep
# stale OS packages (e.g. apt/libapt-pkg CVE-2026-109518) that Trivy flags.
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && rm -rf /var/lib/apt/lists/*

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
