FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY requirements/base.txt /app/requirements/base.txt
COPY requirements/production.txt /app/requirements/production.txt

RUN groupadd -g 1000 appuser \
    && useradd -u 1000 -g 1000 -m -d /home/appuser appuser

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r /app/requirements/production.txt

# Copy application inputs explicitly; runtime credentials stay on the host.
COPY README.md LICENSE /app/
COPY src/ /app/src/
COPY example_project/outer_rim_logistics/pyproject.toml \
    example_project/outer_rim_logistics/README.md \
    example_project/outer_rim_logistics/__init__.py \
    example_project/outer_rim_logistics/manage.py \
    /app/example_project/outer_rim_logistics/
COPY example_project/outer_rim_logistics/orl/ /app/example_project/outer_rim_logistics/orl/
COPY example_project/outer_rim_logistics/crew/ /app/example_project/outer_rim_logistics/crew/
COPY example_project/outer_rim_logistics/maintenance/ /app/example_project/outer_rim_logistics/maintenance/
COPY example_project/outer_rim_logistics/mission/ /app/example_project/outer_rim_logistics/mission/
COPY example_project/outer_rim_logistics/supply/ /app/example_project/outer_rim_logistics/supply/
COPY example_project/outer_rim_logistics/docker/entrypoint.sh \
    example_project/outer_rim_logistics/docker/entrypoint-celery.sh \
    /app/example_project/outer_rim_logistics/docker/

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e /app \
    && pip install -e /app/example_project/outer_rim_logistics[redis]

WORKDIR /app/example_project/outer_rim_logistics
RUN chmod +x /app/example_project/outer_rim_logistics/docker/entrypoint.sh \
    /app/example_project/outer_rim_logistics/docker/entrypoint-celery.sh

EXPOSE 8000

USER appuser

ENTRYPOINT ["/app/example_project/outer_rim_logistics/docker/entrypoint.sh"]
