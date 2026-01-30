FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app

RUN pip install --upgrade pip \
    && pip install -e /app \
    && pip install -e /app/example_project/outer_rim_logistics[redis]

WORKDIR /app/example_project/outer_rim_logistics
RUN chmod +x /app/example_project/outer_rim_logistics/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/example_project/outer_rim_logistics/docker/entrypoint.sh"]
