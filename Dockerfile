FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY mvp-web/package.json mvp-web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY mvp-web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 PORT=10000 NODE_EXECUTABLE=/usr/local/bin/node FRONTEND_DIR=/app/mvp-web/dist/client
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/npm /opt/npm
COPY requirements-bazi.txt requirements-validation.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt && pip install --no-cache-dir --no-deps dashaflow==1.1.0 PyJHora==4.8.6
COPY package.json ./
RUN node /opt/npm/bin/npm-cli.js install --omit=dev --ignore-scripts --no-audit --no-fund
COPY src/ src/
COPY schemas/ schemas/
COPY prompts/ prompts/
COPY LICENSE NOTICE.md README.md ./
COPY skill-packs/v1/ skill-packs/v1/
COPY scripts/prepare_deploy_runtime.py scripts/prepare_deploy_runtime.py
RUN python scripts/prepare_deploy_runtime.py
COPY --from=frontend /build/dist/client/ mvp-web/dist/client/
RUN useradd --uid 10001 --create-home app && mkdir -p /app/data/local-audit && chown -R app:app /app/data
USER app
CMD ["python", "-m", "src.mvp.local_server", "--host", "0.0.0.0"]
