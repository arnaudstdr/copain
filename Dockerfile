# --- Stage 1 : build du front React (Vite) ------------------------------------
# Node n'existe que dans ce stage : l'image finale ne contient que le résultat
# (frontend/dist), copié au stage 2. Build reproductible via npm ci + lockfile.
FROM node:24-alpine AS frontend-build

WORKDIR /app/frontend

# Couche deps mise en cache tant que package.json / lockfile ne changent pas.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- Stage 2 : runtime Python -------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
    && rm -rf /var/lib/apt/lists/*

# uv (binaire statique) : sert uniquement à installer le venv depuis le lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

# Dépendances figées : lock (dépendances directes + transitives), sans le
# groupe dev, sans installer le projet lui-même. Reproductible (--frozen).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY bot/ ./bot/

# Build React issu du stage node : unique interface web, servie par FastAPI via
# SPAStaticFiles (bot.api.FRONTEND_DIST = /app/frontend/dist). Absent → `/` 404.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown -R app:app /app

USER app

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "bot.main"]
