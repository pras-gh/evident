# syntax=docker/dockerfile:1
#
# The backend: the API and every worker, in one image. Each Railway service
# runs it in a role (tools/serve.sh): `api` serves, `sync` keeps the S&P 500
# current, and `migrate` is the API's pre-deploy command.
#
#   docker build -t parallx-backend .
#   docker run --rm -p 8080:8080 --env-file .env -e APP_ENV=development parallx-backend
#
# The web app is not in here: it deploys to Vercel from apps/web.

# bookworm, not the newer default: Playwright's browser dependencies are
# packaged for it
FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them. Chromium is for
# the render worker (page images and paragraph boxes); a filing it can't
# render shows as text instead.
COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY config ./config
COPY db ./db
COPY packages ./packages
COPY apps/api ./apps/api
COPY workers ./workers
COPY tools ./tools

ENV PYTHONPATH=/app/packages/db:/app/packages/parser:/app/packages/memory:/app/packages/retrieval:/app/packages/graph:/app/packages/ai:/app/apps:/app \
    APP_ENV=production \
    PORT=8080

# Never root at run time. data/ is only scratch: in production filings live
# in the bucket (FILING_STORE=s3://...).
RUN useradd --create-home --uid 10001 parallx \
 && mkdir -p /app/data \
 && chown parallx /app/data
USER parallx

EXPOSE 8080
ENTRYPOINT ["sh", "tools/serve.sh"]
CMD ["api"]
