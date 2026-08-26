# Build the console first, in a Node image, and copy only its output into the
# runtime image. Keeping Node out of the final layer avoids shipping a whole
# toolchain to run a directory of static files.
FROM node:22-slim AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --silent
COPY console/ ./
RUN npm run build

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

COPY --from=console /console/dist ./console/dist

# A container has no display - headed mode (this project's local default,
# so you can watch it work) would crash the launch outright here.
ENV HEADLESS=1

# Chromium's default /dev/shm in a container is 64MB - too small, crashes
# on real pages. Routing to /tmp instead of bumping shm size, since that's
# the one-line fix and doesn't depend on the host's Docker config.
ENV BROWSER_MCP_EXTRA_ARGS="--disable-dev-shm-usage"

# Agents, runs and browser profiles live here. Mount a volume over it so they
# survive a redeploy - a rebuild replaces the image, and anything written
# inside the container goes with it.
ENV AX_DATA_DIR=/data
VOLUME ["/data"]

ENV HOST=0.0.0.0
ENV API_PORT=8000
EXPOSE 8000

# Serves the HTTP API and the console together. AX_API_TOKEN must be supplied
# at run time; without it the API is unauthenticated, which is not acceptable
# on a public host.
CMD [".venv/bin/browser-mcp-api"]
