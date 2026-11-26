# ADR 0002: LM Studio Runtime instead of undocumented Bionic automation

Status: accepted — 2026-11-26

## Context

Bionic is the preferred interactive agent environment, but its public documentation does not define programmatic session control. LM Studio Runtime documents REST, model discovery, tool calling and MCP.

## Decision

Integrate through a `LMStudioProvider` using documented loopback endpoints. Keep the provider boundary so future Bionic or other local providers can be added. Do not make UI automation or private endpoints mandatory.

## Consequences

Second Mind owns a small bounded tool loop. It does not duplicate Bionic projects, Skills or coding workflows. Users can expose Second Mind data and tools back to Bionic through the MCP server.

