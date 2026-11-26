# Changelog

All notable changes follow Keep a Changelog. Versions use Semantic Versioning.

## [Unreleased]

## [0.2.0] - 2026-11-26

### Added

- Single per-user Windows Setup.exe bundling Core, Desktop, MCP, updater and runtime dependencies.
- Start menu/desktop shortcuts, installer autostart task and data-preserving uninstall/reinstall behavior.
- Dedicated Updates GUI with current/latest versions, release changelog, automatic checks and download progress.
- Ed25519-signed release manifests, SHA-256/size/source verification and optional Authenticode signing in CI.
- Detached update transaction with graceful Core shutdown, process quiescing, SQLite/application backups, post-install health validation and rollback.
- GitHub Actions release gate for clean installation, the detached updater transaction and upgrade from the previous stable installer while preserving data and settings.
- GitHub build-provenance attestation and signed release metadata assets.

### Changed

- Core and updater executables use the Windows GUI subsystem so no console window is shown.
- Application version is 0.2.0 and is reported consistently by Core and packaging.

## [0.1.0] - 2026-11-26

### Added

- Local FastAPI Core, PySide6 desktop and setup wizard.
- LM Studio Runtime provider with model discovery and multi-tool calling.
- Telegram long polling with allowlist, durable inbox and voice/image/document handling.
- Local SQLite tasks, projects, reminders, conversations, memory, FTS and deduplication.
- faster-whisper lifecycle controls and document extraction.
- MCP stdio server and permission-aware tool registry.
- Windows Credential Manager secrets, diagnostics, backup API and autostart support.
- Tests, Windows packaging scripts and GitHub Actions workflows.
