# Second Mind

Second Mind is a local-first personal AI assistant for Windows. It combines an independently running Core service, a PySide6 desktop application, Telegram long polling, durable memory, tasks, document indexing, local speech recognition and an MCP server.

[Русская документация](README.md) · [Architecture](docs/architecture.md) · [Roadmap](ROADMAP.md) · [Security](SECURITY.md)

## MVP status

Version 0.1.0 implements a working vertical path: configure LM Studio and Telegram in the GUI, send text or voice, let a local model call strictly validated tools, and persist tasks or knowledge in SQLite without using a console. Images and local PDF/DOCX/TXT indexing are included.

The desktop can check a configured GitHub Releases repository and download an installer only when GitHub provides a matching SHA-256 asset digest.

Bionic exposes documented user-facing projects, sessions and Skills, but no documented public API for programmatically starting and collecting Bionic agent sessions was found. Second Mind therefore uses the supported LM Studio Runtime REST and OpenAI-compatible APIs. See [the dated research note](docs/bionic-research.md).

## Development setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop,speech,dev]"
second-mind-desktop
```

LM Studio Runtime 0.4+ should listen on `http://127.0.0.1:1234` and have a local model available. The desktop starts Core as a detached process; closing the window leaves Core and Telegram running.

## Security defaults

- Core and LLM URLs are loopback-only.
- Telegram starts only with a non-empty numeric allowlist.
- Tokens are kept in Windows Credential Manager through `keyring`.
- Documents, forwarded messages and retrieved memory are marked as untrusted data in the agent prompt.
- External tools require explicit authorization; the default Personal Agent cannot send arbitrary Telegram messages.
- Inbox operations are durable and idempotent.

Run `ruff check .` and `pytest --cov=second_mind`. Build Windows artifacts with `packaging\build.ps1`.

Licensed under Apache-2.0.
