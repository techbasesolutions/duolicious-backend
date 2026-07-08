# syntax=docker.io/docker/dockerfile:1.7-labs
FROM python:3.11

ENV DUO_USE_VENV=false
ENV PYTHONUNBUFFERED=true

WORKDIR /app

# Dependencies FIRST so the pip layer caches on its own key
# (requirements.txt) and never re-runs for a code change.
COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

# See api.Dockerfile: force the code layer per-commit; the labs
# COPY --exclude cache key missed ADDED files.
ARG GIT_SHA=dev
RUN echo "build: ${GIT_SHA}" > /app/.build-sha

COPY \
  --exclude=test \
  --exclude=vm \
  . /app

CMD /app/cron.main.sh
