# ClamAV scanner worker (T59 early version). Build in CI or locally:
#   docker build --platform linux/amd64 -t securecare-scanner:dev .
# --platform matters on Apple Silicon: the ECS task definition uses X86_64.
#
# Signature decision: the virus database is BAKED INTO THE IMAGE at build time
# (freshclam runs here, where the internet exists). The running task has no
# internet and no NAT, so it cannot update itself. The image must be rebuilt
# and redeployed on a schedule (weekly minimum, see README).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends clamav clamav-daemon clamav-freshclam ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 # Stop the packaged services' own config from being used; we write our own.
 && rm -f /etc/clamav/clamd.conf \
 && mkdir -p /var/lib/clamav \
 && freshclam --foreground --stdout \
 && chown -R root:root /var/lib/clamav && chmod -R a+rX /var/lib/clamav \
 && echo "signatures baked: $(date -u +%Y-%m-%dT%H:%M:%SZ)" > /etc/signatures-built-at

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=root:root scanner ./scanner

# The task definition mounts a writable volume at /tmp (root FS is read-only).
# Fargate creates that volume owned by root, so uid 10001 gets "Permission denied" unless the
# image declares /tmp as a VOLUME and owns it: Fargate then copies this ownership onto the mount.
RUN chown 10001:10001 /tmp && chmod 1777 /tmp
VOLUME ["/tmp"]

# Same non-root uid as the records service. No home, no login shell.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin scanner
USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD ["python", "-m", "scanner.healthcheck"]

CMD ["python", "-m", "scanner.main"]
