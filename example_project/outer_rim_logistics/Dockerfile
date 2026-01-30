FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY requirements/base.txt /app/requirements/base.txt
COPY requirements/production.txt /app/requirements/production.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r /app/requirements/production.txt

COPY . /app

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e /app \
    && pip install -e /app/example_project/outer_rim_logistics[redis]

WORKDIR /app/example_project/outer_rim_logistics
RUN chmod +x /app/example_project/outer_rim_logistics/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/example_project/outer_rim_logistics/docker/entrypoint.sh"]
