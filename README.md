# NexaBot

NexaBot is an AI Telegram assistant and automation platform built as a modular
monolith. Telegram is only one interface; domain and application logic are kept
independent so REST APIs, dashboards, workers, and future clients can reuse the
same core.

Current implementation status:

- Phase 0 repository assessment complete.
- Phase 1 architecture documentation complete.
- Phase 2 foundation complete: configuration, error taxonomy, logging
  context, health checks, database metadata/migrations, and core domain
  entities/ports.
- Phase 3 persistence adapters in progress: SQLAlchemy repository
  implementations for identity, conversations, agent runs, and idempotency,
  plus a database-backed readiness check wired into bootstrap.
- Phase 4 runnable slice: the API process has an entrypoint, migrations run
  against the async driver, the domain error taxonomy is mapped onto HTTP, and
  persistence failures are translated into domain errors.

Not yet implemented: Redis queue, real LLM provider adapters, blob storage,
the tool registry/executor, quota enforcement, audit logging, and the Telegram
interface.

## Layout

```text
src/nexabot/
├── domain/
├── application/
├── ports/
├── infrastructure/
├── interfaces/
├── config/
├── observability/
└── bootstrap/
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Quality gates (both must stay clean):

```bash
mypy src && ruff check src tests
```

## Running

Apply migrations, then start the API:

```bash
alembic upgrade head
nexabot-api
```

`DATABASE_URL` uses an async driver, so Alembic runs through an async engine.
A synchronous engine over `postgresql+asyncpg` fails at the first query.

Health endpoints:

- `GET /health/live` — the process is up.
- `GET /health/ready` — 200 when every dependency is reachable, 503 otherwise.

## Configuration

Copy `.env.example` to `.env` and provide real values through environment
variables or a secret manager. Do not commit real secrets.
