# syntax=docker.io/docker/dockerfile:1.7-labs
FROM python:3.11

ENV DUO_USE_VENV=false
ENV PYTHONUNBUFFERED=true

WORKDIR /app

# Dependencies FIRST so the pip layer caches on its own key
# (requirements.txt) and never re-runs for a code change.
COPY requirements.txt /app/requirements.txt

# The spaCy model ships with the api and chat images too. The cron
# container needs it because service.spotlight.storage imports
# service.person, which pulls duotypes and antiabuse.normalize, which
# loads en_core_web_sm at import time. Without it the spotlight image
# cleanup (a withdrawal deleting a member's card) died on import.
RUN : \n  && pip install --no-cache-dir -r /app/requirements.txt \n  && python -m spacy download en_core_web_sm

# See api.Dockerfile: force the code layer per-commit; the labs
# COPY --exclude cache key missed ADDED files.
ARG GIT_SHA=dev
RUN echo "build: ${GIT_SHA}" > /app/.build-sha

COPY \
  --exclude=test \
  --exclude=vm \
  . /app

CMD /app/cron.main.sh
