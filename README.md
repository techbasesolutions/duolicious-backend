# Ahavah Backend

Ahavah is a Bumpy-style Torah-observant international dating PWA. The backend is forked from [Duolicious](https://github.com/duolicious/duolicious-backend) and extended for Ahavah's Phase 1-5 features (international discovery, message translation, verification levels, photo moderation, IAP entitlements). The Duolicious license and contributor history are preserved; see LICENSE.

## Fork heritage

This repository keeps a number of internal identifiers from the upstream project verbatim because renaming them would force database migrations or break the codebase for no user-facing benefit:

- Python env-var prefix `DUO_*` (e.g. `DUO_ENV`, `DUO_DB_HOST`, `DUO_R2_*`, `DUO_SMTP_*`)
- Postgres schema/table names beginning with `duo_*` (`duo_api`, `duo_session`, …)
- Internal Python helper / variable names containing `duo`

User-visible strings (email subjects, body copy, sender display name, public URLs) are Ahavah-branded and driven by the env vars described in [DEVELOPER.md](DEVELOPER.md). The new `service/config.py` module centralises every public URL behind an `AHAVAH_*` environment variable so the same code runs in dev, staging, and production.

## Quickstart (copy & paste)

Requirements: Docker (with Compose), jq, curl, ffmpeg, zstd

```bash
# 1) Clone and start the full dev stack
git clone <your fork-of-ahavah-api remote>
cd ahavah-api
docker compose up -d

# 2) Wait for the API to be healthy
curl -sf http://localhost:5000/health && echo OK

# 3) Create a test user and seed data (OTP is auto-handled in dev)
./test/util/create-user.sh alice 30 1 true
```

- The command above signs up `alice@example.com`, finishes onboarding, answers questions, adds a photo and an audio bio.
- Use `./test/util/create-user.sh bob 50 2` to create more sample users.

## Run tests

Run one test file in a disposable environment:

```bash
./test/util/with-container.sh ./test/functionality1/status.sh
```

Run an entire test suite (e.g. all tests in functionality1):

```bash
./test/util/with-container.sh ./test/functionality.sh 1
```

## Common local URLs

- API: [http://localhost:5000/health](http://localhost:5000/health)
- Chat (WebSocket): `ws://localhost:5443`
- Mock S3 UI/endpoint: [http://localhost:9090](http://localhost:9090)
- MailHog (test email UI): [http://localhost:8025](http://localhost:8025)
- Postgres (host port): `localhost:5432`
- Status page: [http://localhost:8080](http://localhost:8080)
- PgAdmin: [http://localhost:8090](http://localhost:8090)

## Local development

Prefer Docker? You already started everything with `docker compose up -d`.

Prefer running the services from source (hot reload)? See the "Local development" section in [DEVELOPER.md](DEVELOPER.md).

## Contributing

Want to help build a Torah-observant dating community? Read our [CONTRIBUTING guide](CONTRIBUTING.md) for coding standards, how to run tests, and what makes a great PR. Developer setup steps live in [DEVELOPER.md](DEVELOPER.md).
