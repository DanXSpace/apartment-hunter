FROM python:3.12-slim-bookworm

# The Discord bot. No browser: it only makes outbound connections (Discord, Zillow, Craigslist).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
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
    DATA_DIR=/app/data

VOLUME ["/app/data"]
ENTRYPOINT ["tini", "--", "apartment-hunter"]
