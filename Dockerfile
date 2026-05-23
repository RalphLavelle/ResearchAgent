# Combined Research Agent deploy: Angular UI + Python scheduler on one $5/mo container.
# Both share /app/data at runtime (nginx serves it; the agent writes to it).

FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
COPY topics/ /build/topics/
RUN npm run build:deploy

FROM python:3.13-slim-bookworm
RUN apt-get update \
  && apt-get install -y --no-install-recommends nginx \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY topics/ ./topics/
COPY deploy/ ./deploy/
RUN pip install --no-cache-dir -e .

COPY --from=web-build /build/web/dist/web/browser ./web
RUN mkdir -p /app/data && chmod +x /app/deploy/start.sh

ENV DATA_DIR=/app/data \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["/app/deploy/start.sh"]
