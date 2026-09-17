# ADR 0001: Modular Monolith with Clean/Hexagonal Architecture

Date: 2026-08-24

## Status

Accepted

## Context

NexaBot must support Telegram initially, but Telegram is only one interface.
The same application and domain logic must later support REST, a dashboard,
CLI, workers, and other interfaces. The platform also needs long-running work,
LLM providers, tools, permissions, memory, file processing, quotas, audit logs,
and operational observability.

Microservices would add deployment and consistency complexity before the domain
boundaries are proven.

## Decision

Build NexaBot as a modular monolith:

```text
interfaces -> application -> domain
                        \-> ports
infrastructure -> ports implementations
bootstrap wires concrete dependencies
```

Rules:

- Domain code has no dependency on infrastructure, Telegram, FastAPI, Redis,
  SQLAlchemy sessions, or AI SDKs.
- Application services coordinate use cases and depend on domain objects plus
  ports.
- Ports define repository, LLM, queue, storage, search, audit, and idempotency
  contracts.
- Infrastructure implements ports and owns external SDK usage.
- Interfaces translate external events into application commands.
- Bootstrap owns dependency construction.

## Consequences

- New interfaces can call the same use cases without duplicating business
  logic.
- Provider adapters can be replaced without touching the agent domain.
- Database and queue details remain outside business logic.
- Some upfront structure is required, but it prevents early coupling to
  Telegram or a single LLM vendor.

## Non-goals

- No microservices in the initial implementation.
- No unbounded agent loops.
- No direct AI SDK calls from domain/application code.
- No direct business decisions in Telegram handlers.
