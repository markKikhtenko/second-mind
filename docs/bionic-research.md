# Bionic integration research

Research date: **2026-11-26**. Only official LM Studio documentation was used.

## Findings

1. [Bionic](https://lmstudio.ai/docs/bionic) is a separate agent application with projects and independent sessions. It can use local, linked remote or LM Studio cloud models.
2. [Bionic Skills](https://lmstudio.ai/docs/bionic/agent/skills) use the standard `SKILL.md` format and can be installed or selected in the UI.
3. The official Bionic pages reviewed do not publish an API for creating sessions, spawning subagents, monitoring background Bionic work or collecting a session result from another application.
4. [LM Studio Developer APIs](https://lmstudio.ai/docs/developer) are documented for programmatic work. Runtime 0.4 provides `/api/v1/chat`, model management, stateful responses, MCP integrations and OpenAI-compatible tool calling.
5. [Tool use](https://lmstudio.ai/docs/developer/openai-compat/tools) follows OpenAI-compatible function schemas and supports multiple turns.
6. [MCP via API](https://lmstudio.ai/docs/developer/rest/quickstart) supports configured plugins and per-request ephemeral remote MCP servers.
7. [Offline operation](https://lmstudio.ai/docs/app/offline) keeps local inference and document work on-device after models are downloaded.

## Decision

Second Mind does not automate Bionic through UI automation or undocumented private endpoints. It integrates with the supported LM Studio Runtime API through a provider adapter and implements the minimum tool loop required for the product. The Second Mind MCP server can also be installed in Bionic/LM Studio as a user-controlled integration.

If LM Studio later publishes a stable Bionic session API, it can be added as a new provider/runtime without changing storage or domain tools.

## Compatibility target

- Preferred: LM Studio Runtime 0.4+ native API plus `/v1/chat/completions` tool calling.
- Fallback model discovery: `/v1/models` for older compatible local servers.
- No cloud endpoint is configured automatically. External providers require a separate adapter and explicit user consent.

