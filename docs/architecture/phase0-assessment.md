# Phase 0 Repository Assessment

Date: 2026-08-24

## Scope inspected

The working directory is `/home/il/Desktop`. It is not a valid project root for
NexaBot:

- `/home/il/Desktop/.git` exists but contains no repository metadata usable by
  `git status`.
- The directory contains multiple unrelated projects, virtual environments,
  media files, generated APK/payload outputs, and standalone scripts.
- No `nexabot`, `NexaBot`, or equivalent AI assistant project exists.

The inspection intentionally avoided runtime `.env` files and secret-bearing
configuration.

## Existing relevant directories

| Path | Observed purpose | Relevance to NexaBot |
|---|---|---|
| `sentinel-bot/` | aiogram-based Telegram remote-administration and monitoring bot | Not a suitable base. Thin handler/service separation is conceptually reusable, but the domain and security model are wrong for an AI assistant. |
| `superbot-phase1/superbot/` | Early aiogram bot skeleton with admin filter, command registry, logger, `/start`, `/help` tests | Some ideas are reusable, but the code is not aligned with clean/hexagonal architecture and has no AI, persistence, queue, usage, files, or provider abstraction. |
| `superbot/`, `rt/k/` | Telegram/system-control bot variants | Not suitable. These are operationally and architecturally outside NexaBot’s target domain. |
| `sentinel-bot/tests/`, `superbot-phase1/superbot/tests/` | Existing pytest examples | Useful as reference only; not imported into NexaBot. |

## Existing configuration and tests

- Existing Python bot projects use `requirements.txt`, `.env.example`, and
  pytest.
- `sentinel-bot` has a `pyproject.toml` and CI configuration.
- No existing PostgreSQL, SQLAlchemy, Alembic, Redis, FastAPI, AI provider, or
  agent runtime exists for NexaBot.

## Technical debt in the current workspace

- Mixed workspace root with unrelated projects and large binary artifacts.
- Multiple virtual environments committed or present in the workspace.
- Existing `.env` files are present in sibling projects; they were not read.
- Several directories contain generated payload/decompiled APK artifacts. These
  must not be reused for NexaBot.
- No valid git repository at the desktop root, so change tracking must be scoped
  carefully.

## Reusable components

No existing code is reused directly. The following design ideas are worth
retaining:

- Keep Telegram handlers thin.
- Gate privileged actions through centralized authorization.
- Use pytest for deterministic unit tests.
- Keep environment-based configuration and `.env.example`.

## Contradictions with the target architecture

Existing bot projects conflict with NexaBot requirements:

- Business logic is organized around Telegram/system administration rather than
  an interface-independent application core.
- No clean domain/application/ports/infrastructure layering.
- No persistent conversation, agent run, usage, task, file, memory, or audit
  models.
- No LLM gateway abstraction.
- No explicit agent state machine, execution limits, tool authorization, or
  idempotency model.
- No production readiness model for PostgreSQL, Redis, health checks, metrics,
  or migrations.

## Decision

Create a new isolated project at `/home/il/Desktop/nexabot`.

This avoids mutating unrelated projects and prevents remote-administration code
from leaking into an AI assistant platform. The first implementation slice will
establish architecture documentation, typed configuration, error taxonomy,
structured logging, database metadata/migrations, health checks, and core
ports/domain contracts before Telegram or AI provider integration.
