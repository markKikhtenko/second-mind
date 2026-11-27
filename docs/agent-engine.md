# Agent Engine v0.4

## Execution pipeline

1. The gateway stores the user message in the existing conversation repository.
2. Deterministic routing selects one enabled profile. A mixed-domain request stays with Personal Agent so it can complete all actions in one model loop.
3. The Skill Registry scores only Skills assigned to that agent and loads at most four full `SKILL.md` bodies. The rest remain short catalog metadata.
4. Context Manager adds recent conversation turns and relevant SQLite FTS5 memory within the configured context budget.
5. A process-wide asynchronous lock sends the request to the shared LM Studio Runtime.
6. Every tool call is checked against the selected agent allowlist, the Pydantic input schema and the persisted backend policy.
7. Results return to the same model loop until a final answer, configured tool limit, timeout or cancellation.
8. `agent_runs`, `tool_call_history` and `execution_log` record factual stages and outcomes.

The trace exposed to users contains operational events such as “context built”, “waiting for Runtime”, “tool policy checked” and “completed”. It does not contain hidden chain-of-thought.

## Agent configuration

Each agent persists its name, description, system prompt, state, optional model override, global-model inheritance, temperature, optional reasoning effort, context/generation limits, timeout, tool-call limit, Skills, allowed tools, memory policy and confirmation policy.

Built-in agents cannot be deleted but can be disabled, edited and restored. Custom agents support create, clone, delete, JSON import/export and test runs. Factory defaults are stored with the seeded row so later application updates do not silently erase user edits.

## Skill format and storage

Repository Skills live at `skills/<skill-id>/SKILL.md` and use YAML frontmatter:

```markdown
---
name: Example Skill
description: Short discovery description.
version: 1.0.0
---
# Условия применения
...
# Инструкции
...
# Ограничения
...
# Примеры
...
```

The parser requires a mapping frontmatter, non-empty `name`, `description` and instruction body. Second Mind tool grants and policies are deliberately not written into `SKILL.md`; they remain in SQLite so import/export stays compatible with standard Agent Skills.

Custom files are stored under `<data_dir>/skills/custom`. Edits of a built-in Skill are stored under `<data_dir>/skills/overrides`. Restoring a built-in version removes its override. Both locations are outside the installation directory and survive application updates.

## Tool policies and approvals

- `auto`: validate and execute immediately.
- `confirm`: persist the exact tool name, validated arguments, agent, run, transport and user. Execution occurs only for the matching approval id.
- `deny`: reject at the backend before the handler runs.

Agent allowlists are checked before policy evaluation. Dry Run validates mutating calls and records the attempt without invoking the handler. An approval cannot be reused for different arguments or a different tool. Desktop uses the approvals view; Telegram accepts `/confirm <id>` and `/deny <id>` through the same chat service.

## Resource behavior

Agent profiles do not own model processes. They resolve to the global or configured LM Studio model when their turn enters the shared queue. One slow request can therefore delay later requests, but it cannot cause multiple home-PC model instances. Queue depth and active run ids are available through `/api/orchestrator/status`.

## API surface

- `/api/agents`: profile CRUD, clone, reset, import and export.
- `/api/skills`: validation, CRUD, enable/disable, import, export and reset.
- `/api/playground`: Dry Run or Execution test request.
- `/api/agent-runs`: monitoring and cancellation.
- `/api/tools`, `/api/tool-policies`, `/api/tool-history`: registry and audit.
- `/api/tool-approvals`: pending action approval or denial.

All endpoints remain loopback-only through the existing Core binding.
