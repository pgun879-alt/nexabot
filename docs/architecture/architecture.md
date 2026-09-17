# NexaBot Architecture Specification

Date: 2026-08-24

## System shape

NexaBot is a modular monolith with async workers. Telegram is an interface, not
the product boundary.

```text
interfaces/
  telegram/     aiogram handlers, Telegram DTO mapping
  api/          FastAPI HTTP surface, health, future admin
  workers/      queue consumers

application/
  use cases, orchestration, transaction boundaries, policies

domain/
  entities, value objects, state machines, domain policies

ports/
  repository, LLM, queue, storage, search, audit, idempotency contracts

infrastructure/
  SQLAlchemy, Redis, LLM SDK adapters, Telegram file download, object storage

bootstrap/
  settings, dependency construction, process entrypoints
```

Dependency rule:

```text
domain -> no outward dependency
application -> domain + ports
infrastructure -> ports + external SDKs
interfaces -> application
bootstrap -> all layers for wiring only
```

## Module boundaries

### Identity

Owns users, Telegram identity mapping, roles, permissions, user status,
banning, and access policies.

Domain concepts:

- `User`
- `TelegramIdentity`
- `Role`
- `Permission`
- `UserStatus`

Application use cases:

- register or update a Telegram user
- resolve current actor
- ban/unban user
- assign/revoke roles

### Conversations

Owns conversation lifecycle and message history. It does not know how models
work.

Domain concepts:

- `Conversation`
- `Message`
- `ConversationStatus`
- `MessageRole`

Application use cases:

- create conversation
- append user/assistant/tool messages
- load bounded recent context
- archive/delete conversation

### Agent

Owns traceable agent execution state, not provider SDKs.

Domain concepts:

- `AgentRun`
- `AgentStep`
- `ToolExecution`
- `AgentRunStatus`
- `AgentStepType`
- `AgentLimits`

Execution flow:

```text
Incoming task
  -> normalize
  -> build context
  -> start AgentRun
  -> model call
  -> optional tool call
  -> authorized tool execution
  -> observation
  -> continue or finalize
  -> response validation
```

Safety controls:

- max steps
- max tool calls
- max retries
- max execution time
- context/token budgets
- explicit failure states

Agent workflow state is persisted incrementally. A multi-step model/tool
workflow must not run inside one database transaction.

### Tools

Tools are registered centrally and accessed through a registry contract.

Each tool exposes:

- identity
- description
- Pydantic input schema
- Pydantic output schema
- permission policy
- timeout policy
- execution function

Tool authorization is centralized. Tools do not decide whether a user is
allowed to invoke them.

### Memory

Memory is split into separate owners:

- conversation memory: bounded recent conversation context
- user memory: stable facts/preferences
- task memory: state for long-running work
- retrieval memory: future vector/RAG items

The context builder decides which memory is included in model input.

### Files

Binary file content is stored through a storage port, not in PostgreSQL.
PostgreSQL stores metadata and indexing/chunk records.

Pipeline:

```text
incoming file -> validate -> store -> extract -> normalize -> chunk -> index
```

Validation includes file size, MIME type, ownership, checksum, and processing
status.

### Tasks

Expensive or long-running work uses task records plus a queue adapter.

Task state includes:

- task ID
- task type
- state
- attempt counters
- timestamps
- error details
- retry policy
- idempotency key when applicable

Retries are bounded and only used for retryable failures.

### Usage and quotas

Usage is independent from the agent. Use cases ask a quota policy before
executing work.

Tracked dimensions:

- requests
- agent runs
- tool calls
- LLM usage
- file processing
- daily/monthly limits

### Administration

Administration is an application module. Telegram admin commands and future
HTTP admin endpoints must call the same use cases.

## Database model

Initial relational concepts:

- `users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`
- `telegram_identities`
- `conversations`
- `messages`
- `agent_runs`
- `agent_steps`
- `tools`
- `tool_executions`
- `memories`
- `memory_items`
- `files`
- `file_chunks`
- `usage_records`
- `subscriptions`
- `tasks`
- `task_attempts`
- `idempotency_records`
- `audit_logs`

Use UUID identifiers at application boundaries, foreign keys for ownership, and
indexes for common lookup paths. Avoid storing large binary data in relational
tables.

## Major ports

- `UserRepository`
- `ConversationRepository`
- `AgentRunRepository`
- `TaskRepository`
- `IdempotencyRepository`
- `LLMGateway`
- `ToolExecutor`
- `Queue`
- `BlobStorage`
- `SearchIndex`
- `AuditLogWriter`
- `UsageRecorder`

## Error model

Errors must be explicit and safely mappable by interfaces:

- `ValidationError`
- `AuthorizationError`
- `NotFoundError`
- `ConflictError`
- `RateLimitError`
- `ExternalServiceError`
- `TaskExecutionError`
- `AgentExecutionError`
- `StorageError`

Each error has a stable code, safe user-facing message, retryability flag, and
HTTP status mapping where relevant. Raw stack traces are logged internally and
never returned to users.

## Security boundaries

- Secrets come from environment variables or secret stores only.
- Secret values must not appear in logs or serialized settings output.
- All privileged use cases go through authorization policies.
- Tool execution is authorized centrally before execution.
- File access checks ownership before metadata or content access.
- Resource use is bounded: file sizes, agent steps, tool timeouts, rate limits,
  context size, task attempts.
- Audit logs record security-sensitive events and administrative actions.

## Observability

Every request/task/agent run should carry correlation identifiers:

- `request_id`
- `user_id`
- `conversation_id`
- `agent_run_id`
- `task_id`

Structured logs use JSON in production and redact known secret fields.

Health endpoints:

- `/health/live`: process is running.
- `/health/ready`: critical dependencies are reachable.

Metrics to add with the runtime adapters:

- request count and latency
- agent failures
- tool failures
- queue depth
- task retries
- provider failures
- usage counters

## Request flows

Telegram message:

```text
Telegram update
  -> idempotency check
  -> interface DTO mapping
  -> identity resolution
  -> quota check
  -> conversation append
  -> agent application service
  -> LLM/tool ports
  -> assistant message persisted
  -> Telegram response
```

File upload:

```text
Telegram file
  -> identity + ownership
  -> validation
  -> metadata record
  -> storage adapter
  -> async processing task
  -> extraction/chunking
  -> optional retrieval indexing
  -> status notification
```

Background task:

```text
task record
  -> queue message
  -> worker claim
  -> attempt record
  -> execute use case
  -> retry policy classification
  -> complete/fail/requeue
```
