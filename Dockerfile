FROM python:3.12-slim-bookworm

# Chromium runs in visible (non-headless) mode inside a virtual X display, because
# apartments.com blocks headless browsers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium xvfb xauth fonts-liberation tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home hunter && mkdir -p /app/data && chown hunter /app/data
USER hunter

ENV PATH=/opt/venv/bin:$PATH \
    BROWSER_EXECUTABLE=chromium \
    BROWSER_HEADLESS=false \
    BROWSER_EXTRA_ARGS="--no-sandbox --disable-dev-shm-usage" \
    APARTMENT_HUNTER_CONFIG=/app/config.toml

VOLUME ["/app/data"]
ENTRYPOINT ["tini", "--", "xvfb-run", "--auto-servernum", "--server-args=-screen 0 1366x900x24", "apartment-hunter"]
