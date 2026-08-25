FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY README.md ./

RUN uv sync --no-editable

# Installs Chromium plus every OS-level library it needs (libgbm, libnss3,
# libatk-bridge2.0, fonts, ...) - patchright ships this the same way
# Playwright does, no need to hand-list apt packages ourselves.
RUN .venv/bin/python -m patchright install --with-deps chromium

# A container has no display - headed mode (this project's local default,
# so you can watch it work) would crash the launch outright here.
ENV HEADLESS=1

# Chromium's default /dev/shm in a container is 64MB - too small, crashes
# on real pages. Routing to /tmp instead of bumping shm size, since that's
# the one-line fix and doesn't depend on the host's Docker config.
ENV BROWSER_MCP_EXTRA_ARGS="--disable-dev-shm-usage"

ENV MCP_TRANSPORT=streamable-http
ENV HOST=0.0.0.0
ENV PORT=8000
EXPOSE 8000

CMD [".venv/bin/browser-mcp"]
