# Pinned multi-stage Debian (bookworm) image: the native OCR wheels used by the
# optional local provider are built against glibc and are not available on Alpine.
FROM python:3.11.13-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

# The default supports both local and managed OCR so the image matches the
# application's local default. Use `--build-arg EXTRAS=azure` for a smaller
# managed-only deployment.
ARG EXTRAS=ml,azure

# Dependency layer: changes only when the project metadata changes.
COPY pyproject.toml README.md ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && mkdir -p src/vision_server \
    && printf '__version__ = "0.0.0"\n' > src/vision_server/__init__.py \
    && /opt/venv/bin/pip install ".[${EXTRAS}]"

# Application layer.
COPY src ./src
RUN /opt/venv/bin/pip install --no-deps .

FROM python:3.11.13-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/vision \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/home/vision/.cache \
    PADDLE_HOME=/home/vision/.paddle \
    VISION_ENVIRONMENT=production \
    VISION_ASSET_ROOT=/tmp/vision-assets
WORKDIR /app

RUN groupadd --system --gid 10001 vision \
    && useradd --system --uid 10001 --gid vision --create-home --home-dir /home/vision vision
COPY --from=build /opt/venv /opt/venv

# The image runs unprivileged and supports a read-only root filesystem; mount
# tmpfs volumes for the two writable locations below (for example
# `docker run --read-only --tmpfs /tmp --tmpfs /home/vision/.cache`).
VOLUME ["/tmp", "/home/vision/.cache"]

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"]
CMD ["uvicorn", "vision_server.main:app", "--host", "0.0.0.0", "--port", "8080"]
