# syntax=docker/dockerfile:1
# nicheclock: self-hosted niche trend radar. Build: docker build -t nicheclock .
# Optional extras: --build-arg EXTRAS=gsc,mcp,onboard (onboard adds scikit-learn, ~150 MB, same results).
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build
ARG EXTRAS=gsc,mcp
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY nicheclock ./nicheclock
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ".[${EXTRAS}]"

FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="nicheclock" \
      org.opencontainers.image.description="Self-hosted niche trend radar" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
# One state directory: nicheclock.yaml, data/ (SQLite, caches), out/ (the reports), secrets/ (Search Console).
# `nicheclock init` writes data_dir: ./data and out_dir: ./out, which resolve inside /nicheclock.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/nicheclock/.cache \
    NICHECLOCK_CONFIG=/nicheclock/nicheclock.yaml \
    NICHECLOCK_CONFIG_DIR=/nicheclock/secrets \
    NICHECLOCK_INTERVAL_HOURS=24 \
    NICHECLOCK_PORT=8080
COPY --from=build /opt/venv /opt/venv
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin nicheclock \
 && mkdir -p /nicheclock && chown nicheclock:nicheclock /nicheclock \
 && chmod 0755 /usr/local/bin/docker-entrypoint.sh
USER nicheclock
WORKDIR /nicheclock
VOLUME /nicheclock
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD ["docker-entrypoint.sh", "healthcheck"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["start"]
