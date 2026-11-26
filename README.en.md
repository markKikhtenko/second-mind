# Second Mind

Second Mind is a local-first personal AI assistant for Windows. It combines an independently running Core service, a PySide6 desktop application, Telegram long polling, durable memory, tasks, document indexing, local speech recognition and an MCP server.

[Русская документация](README.md) · [Architecture](docs/architecture.md) · [Roadmap](ROADMAP.md) · [Security](SECURITY.md)

## Status

Version 0.2.0 implements a working vertical path: configure LM Studio and Telegram in the GUI, send text or voice, let a local model call strictly validated tools, and persist tasks or knowledge in SQLite without using a console. Images and local PDF/DOCX/TXT indexing are included.

The dedicated Updates screen checks GitHub Releases automatically or manually, shows the changelog and download progress, and accepts an installer only after verifying its Ed25519-signed manifest, source, version, size and SHA-256.

Bionic exposes documented user-facing projects, sessions and Skills, but no documented public API for programmatically starting and collecting Bionic agent sessions was found. Second Mind therefore uses the supported LM Studio Runtime REST and OpenAI-compatible APIs. See [the dated research note](docs/bionic-research.md).

## Windows installation and updates

Download the single `Second-Mind-Setup-<version>.exe` from [GitHub Releases](https://github.com/markKikhtenko/second-mind/releases). It bundles Python, Qt, Core, Desktop, MCP, the detached updater and all other runtime dependencies. Setup creates Start menu and optional desktop shortcuts and offers configurable Windows autostart.

Program files are installed per user. Data remains separately under `%LOCALAPPDATA%\Second Mind\Second Mind`, so reinstall and normal uninstall preserve the database, attachments and settings. Before an update, the detached updater stops Core and installed helper processes, backs up both SQLite and the current application, installs silently, and validates the exact new Core version through its health API. Installer or health-check failures automatically restore the previous application and pre-migration database.

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
- Release metadata is authenticated by an embedded Ed25519 public key and rechecked by the detached updater.

Run `ruff check .` and `pytest --cov=second_mind`. Build Windows artifacts with `packaging\build.ps1`. The release workflow also tests a clean Setup.exe install, a real detached-updater transaction and an upgrade from the previous stable release while preserving user data.

Licensed under Apache-2.0.
