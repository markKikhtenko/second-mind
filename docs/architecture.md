# Architecture

Second Mind is a modular monolith with separate Core and GUI processes. This avoids premature microservices while keeping bot availability independent from the window lifecycle.

```mermaid
flowchart LR
  U[User] --> D[PySide6 Desktop]
  U --> T[Telegram]
  D -->|loopback REST| C[Second Mind Core]
  T -->|long polling| G[Telegram Gateway]
  G --> C
  C --> O[Agent Orchestrator]
  O -->|OpenAI-compatible tools| L[LM Studio Runtime]
  O --> R[Permission-aware Tools]
  R --> S[(SQLite WAL + FTS5)]
  R --> F[Local attachments]
  C --> B[Storage Manager]
  B --> S
  B --> F
  B --> A[Backup archives]
  C --> W[faster-whisper]
  M[MCP clients / Bionic] -->|stdio| P[Second Mind MCP]
  P --> S
```

## Modules

- `api.py`: loopback control plane and GUI contract.
- `desktop.py`: design-system based GUI, setup wizard, tray lifecycle and operational screens.
- `ui_theme.py`: theme tokens, scalable SVG icons and reusable visual components.
- `telegram_gateway.py`: allowlist, idempotent inbox and media ingestion.
- `llm.py`: supported LM Studio API adapter.
- `services.py`: orchestration, speech, documents, scheduler and backup.
- `tools.py`: schemas, permissions and business tool execution.
- `repositories.py`: persistence boundary.
- `storage.py`: WAL-safe backup, restore, export, integrity, optimization and relocation.
- `mcp_server.py`: stdio MCP exposure of the same business tools.

## Request sequence

```mermaid
sequenceDiagram
  participant User
  participant Gateway
  participant Core
  participant LLM as LM Studio
  participant Tools
  participant DB as SQLite
  User->>Gateway: text / voice / image / document
  Gateway->>DB: durable idempotent inbox item
  Gateway->>Core: normalized message
  Core->>DB: conversation + relevant memory
  Core->>LLM: prompt, context, strict tool schemas
  loop up to 8 tool rounds
    LLM-->>Core: zero or many tool calls
    Core->>Tools: validate + authorize + execute
    Tools->>DB: transactional change/query
    Tools-->>Core: structured result
    Core->>LLM: tool result
  end
  LLM-->>Core: final response
  Core->>DB: response + execution log
  Core-->>Gateway: response
  Gateway-->>User: response
```

## Data model

IDs are UUIDs. Timestamps are UTC ISO-8601. SQLite uses foreign keys, WAL and a busy timeout. FTS5 indexes memory and extracted document text. Normalized SHA-256 hashes deduplicate memory and attachments. Inbox `(transport, external_id)` uniqueness prevents repeated effects after polling retries.

The Storage Manager uses SQLite's online Backup API rather than copying live database files. Backup archives contain a consistent database snapshot, sanitized configuration and attachments. Restore validates archive paths and database integrity, and relocation stages a verified copy before switching configuration.

## Failure behavior

- LM Studio unavailable: chat returns 503; tasks, memory, files and GUI remain usable.
- Telegram unavailable: Core remains healthy and records the error.
- Whisper missing/model load failure: text path remains usable and GUI shows the error.
- Duplicate Telegram update: previously processed inbox item is ignored.
- Tool validation failure: result is returned to the model as an error; no mutation occurs.
