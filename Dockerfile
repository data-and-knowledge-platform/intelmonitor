# One image for both services (docs/deploy.md): `site`, the web app built with the LLM features
# disabled and served with the API by intelmonitor.server.site, and `runner`, the weekly
# `im weekly --publish`. Deploy both with scripts/deploy.sh.

FROM node:22-slim AS web
WORKDIR /app/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN VITE_API_URL=/api VITE_LLM_OFF=1 pnpm exec vite build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY api/ ./
RUN uv sync --frozen --no-dev
COPY --from=web /app/web/dist /app/web/dist
COPY deploy/ /app/deploy/
ENV INTELMONITOR_DATA_DIR=/data
CMD ["/app/deploy/start.sh"]
