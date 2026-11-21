# SecureCare records service (T09). Runs as a non-root user and needs no
# writable filesystem, so ECS can set readonlyRootFilesystem (T55).

FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
# Pull in Debian security fixes that are newer than the base image (found by Trivy on 2026-11-11: zlib1g).
# Runs as root before USER is set. Trade-off: the image changes whenever Debian ships a fix, which is the point.
RUN apt-get update \
 && apt-get -y --no-install-recommends upgrade \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:${PATH}"

# Fixed numeric UID/GID so ECS and Kubernetes can verify "non-root" by number.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /install /usr/local
WORKDIR /srv
COPY --chown=root:root app ./app

USER 10001:10001
EXPOSE 8080

# No curl in the image: the health check uses Python itself.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
