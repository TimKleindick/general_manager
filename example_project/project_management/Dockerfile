FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl npm \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY requirements/base.txt /app/requirements/base.txt
COPY requirements/production.txt /app/requirements/production.txt

RUN groupadd -g 1000 appuser \
    && useradd -u 1000 -g 1000 -m -d /home/appuser appuser

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r /app/requirements/production.txt

COPY . /app

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e /app

WORKDIR /app/example_project/project_management/frontend
RUN npm ci && npm run build

WORKDIR /app/example_project/project_management
RUN chmod +x \
    /app/example_project/project_management/docker/entrypoint.sh \
    /app/example_project/project_management/docker/entrypoint-celery.sh \
    /app/example_project/project_management/docker/init-entrypoint.sh

EXPOSE 8000

USER appuser

ENTRYPOINT ["/app/example_project/project_management/docker/entrypoint.sh"]
