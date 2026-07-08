# syntax=docker.io/docker/dockerfile:1.7-labs
FROM python:3.11

ENV DUO_USE_VENV=false
ENV PYTHONUNBUFFERED=true

WORKDIR /app

# Dependencies FIRST so the expensive apt/pip/spacy layer caches on its own
# key (requirements.txt) and never re-runs for a code change.
COPY requirements.txt /app/requirements.txt

RUN : \
  && apt update \
  && apt install -y ffmpeg \
  && pip install --no-cache-dir -r /app/requirements.txt \
  && python -m spacy download en_core_web_sm

# GIT_SHA busts ONLY the code layer below on every deploy. The labs
# COPY --exclude cache key proved unreliable for ADDED files (2026-06:
# a new emails/ module was silently missing from a "successful" build),
# so we force the copy per-commit instead of trusting the checksum.
ARG GIT_SHA=dev
RUN echo "build: ${GIT_SHA}" > /app/.build-sha

COPY \
  --exclude=antiabuse/antiporn \
  --exclude=test \
  --exclude=vm \
  . /app

CMD /app/api.main.sh
