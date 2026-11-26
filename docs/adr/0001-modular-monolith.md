# ADR 0001: Modular monolith with independent GUI and Core

Status: accepted — 2026-11-26

## Decision

Use one Python product with clear modules, but run Desktop and Core as separate OS processes. Telegram, scheduler, persistence and agent orchestration live in Core. Desktop is a loopback API client and may exit without stopping Core.

## Consequences

Deployment and migrations stay simple, while bot uptime does not depend on the GUI. Business logic is reused by REST and MCP. A future high-load component can be extracted behind existing interfaces without adopting a distributed system now.

